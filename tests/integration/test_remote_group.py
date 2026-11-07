"""真 Gateway／SDK／有界 Writer 與模擬 UART；不是 Win11／dxan 實機。"""

import asyncio
from collections import deque
import io
import os
import secrets
import signal
import sys
import threading
import unittest
from unittest.mock import patch
from uart_gateway.protocol.errors import RemoteError

from uart_gateway.cli import _WatchOutput, watch_client
from uart_gateway.client import Client
from uart_gateway.core.models import ChannelConfig
from uart_gateway.fake_serial import FakeSerial
from uart_gateway.remote_group import run_remote_group
from uart_gateway.remote_workbench import query_channels
from uart_gateway.serial_backend import SerialBackendError
from uart_gateway.server.auth import Principal
from uart_gateway.server.tcp import Gateway
from tests.fakes import eventually
from tests.integration import test_workbench as console_fixture


class GroupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.token = secrets.token_urlsafe(32)
        self.devices = [FakeSerial(echo=False), FakeSerial(echo=False)]
        self.gateway = Gateway([(ChannelConfig(cid, cid), d) for cid, d in zip(("a", "b"), self.devices)],
            (Principal("operator", self.token, {"a": frozenset({"read", "configure"}), "b": frozenset({"read"})}),))
        self.port = await self.gateway.start(port=0)
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        self.out_patch = patch("sys.stdout", self.stdout)
        self.err_patch = patch("sys.stderr", self.stderr)
        self.out_patch.start()
        self.err_patch.start()
        self.commands = deque()
        self.task = None
        self.clients = []

    async def connect(self):
        client = await Client.connect("127.0.0.1", self.port, self.token)
        self.clients.append(client)
        return client

    async def start(self, *, replay=False, entries=None, instance=None, sink_factory=None, poll=None):
        actual_instance, actual_entries = await query_channels(self.connect)
        self.task = asyncio.create_task(run_remote_group(self.connect, entries or actual_entries,
            instance or actual_instance, replay, watch=watch_client,
            sink_factory=sink_factory or (lambda: _WatchOutput(terminal=True)),
            poll=poll or (lambda: self.commands.popleft() if self.commands else None)))
        return self.task

    async def ready(self):
        await eventually(lambda: all(c.subscriptions for c in self.gateway.channels.values()))

    async def expect(self, text):
        await eventually(lambda: text in self.stderr.getvalue() + self.stdout.getvalue())

    async def asyncTearDown(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        for client in self.clients:
            await client.close()
        self.out_patch.stop()
        self.err_patch.stop()
        await self.gateway.close()

    async def test_dual_live_tag_colors_and_manual_refresh_does_not_duplicate_subscriptions(self):
        await self.start()
        await self.ready()
        self.devices[0].inject(b"\x1b[31mfirst\r\n")
        self.devices[1].inject(b"second\x08\n")
        await self.expect("first\\r")
        await self.expect("second\\x08")
        self.assertIn("[a／a]", self.stdout.getvalue())
        self.assertIn("[b／b]", self.stdout.getvalue())
        self.assertIn("\x1b[31m", self.stdout.getvalue())
        self.commands.extend(["r", "s 1"])
        await self.expect("未建立重複訂閱")
        self.assertEqual([len(c.subscriptions) for c in self.gateway.channels.values()], [1, 1])
        self.commands.append("q")
        self.assertEqual(await asyncio.wait_for(self.task, 2), 0)
        await eventually(lambda: not self.gateway.sessions)
        self.assertTrue(all(c.transport.reader_task.done() for c in self.clients))
        self.assertTrue(all(d.write_count == 0 for d in self.devices))

    async def test_replay_and_stale_instance_do_not_display_another_stream(self):
        self.devices[0].inject(b"history-a\n")
        self.devices[1].inject(b"history-b\n")
        await eventually(lambda: all(c.offset for c in self.gateway.channels.values()))
        await self.start(replay=True, instance="different")
        await self.expect("未自動重連")
        self.assertNotIn("history-", self.stdout.getvalue())
        self.assertFalse(any(c.subscriptions for c in self.gateway.channels.values()))
        self.commands.append("q")
        await self.task
        await self.start(replay=True)
        await self.expect("history-a")
        await self.expect("history-b")
        self.assertIn("歷史重播", self.stderr.getvalue())

    async def test_one_stale_epoch_rejects_only_that_source_before_display(self):
        instance, entries = await query_channels(self.connect)
        entries[0] = dict(entries[0], stream_epoch="stale-epoch")
        await self.start(entries=entries, instance=instance)
        await self.expect("a：監看失敗")
        await eventually(lambda: bool(self.gateway.channels["b"].subscriptions))
        self.assertFalse(self.gateway.channels["a"].subscriptions)
        self.devices[0].inject(b"wrong-stream-must-not-show\n")
        self.devices[1].inject(b"valid-peer\n")
        await self.expect("valid-peer")
        self.assertNotIn("wrong-stream-must-not-show", self.stdout.getvalue())

    async def test_unplug_and_slow_manual_reopen_keep_peer_subscription_epoch_and_data(self):
        await self.start()
        await self.ready()
        old_epoch = self.gateway.channels["a"].epoch
        peer_epoch = self.gateway.channels["b"].epoch
        peer_subs = dict(self.gateway.channels["b"].subscriptions)
        self.devices[0].close()
        await self.expect("裝置已失去連線")
        self.commands.append("r")
        await self.expect("通道：裝置遺失")
        gate, entered = threading.Event(), threading.Event()
        original = self.devices[0].open

        def delayed_open():
            entered.set()
            if not gate.wait(3):
                raise OSError("測試等待逾時")
            original()

        try:
            with patch.object(self.devices[0], "open", side_effect=delayed_open):
                self.commands.extend(["o 1", "y"])
                await eventually(entered.is_set)
                self.devices[1].inject(b"peer-during-reopen\n")
                await self.expect("peer-during-reopen")
                gate.set()
                await self.expect("RX 重開成功")
                await self.ready()
        finally:
            gate.set()
        self.devices[0].inject(b"recovered-a\n")
        await self.expect("recovered-a")
        self.assertNotEqual(self.gateway.channels["a"].epoch, old_epoch)
        self.assertEqual(self.gateway.channels["b"].epoch, peer_epoch)
        self.assertEqual(self.gateway.channels["b"].subscriptions, peer_subs)
        self.assertFalse(self.gateway.channels["a"].confirmed)
        self.assertTrue(all(d.write_count == 0 for d in self.devices))

    async def test_read_only_and_cancel_do_not_open_and_stale_confirmation_is_rejected(self):
        await self.start()
        await self.ready()
        self.devices[1].close()
        self.devices[0].close()
        await self.expect("[b／b] 裝置已失去連線")
        # 兩個 Reader 獨立偵測遺失；B 已結束不代表 A 已更新狀態。
        await eventually(lambda: all(c.state == "lost" for c in self.gateway.channels.values()))
        self.commands.append("r")
        await self.expect("通道：裝置遺失")
        with patch.object(self.devices[0], "open", wraps=self.devices[0].open) as opened:
            self.commands.extend(["o 2", "o 1", "n"])
            await self.expect("已取消重開")
            opened.assert_not_called()
            self.commands.append("o 1")
            await self.expect("輸入 Y 確認")
            await self.gateway.channels["a"].rebind(self.gateway.channels["a"].context)
            self.commands.append("y")
            await self.expect("修訂或資料流已改變")
            opened.assert_not_called()

    async def test_one_network_session_loss_does_not_stop_peer_or_auto_reconnect(self):
        await self.start()
        await self.ready()
        owner = next(iter(self.gateway.channels["a"].subscriptions.values()))
        self.gateway.sessions[owner].abort()
        await self.expect("a：監看失敗")
        count = len(self.clients)
        self.devices[1].inject(b"peer-after-network-loss\n")
        await self.expect("peer-after-network-loss")
        self.assertEqual(len(self.clients), count)
        self.commands.extend(["r", "s 1"])
        await self.ready()
        self.devices[0].inject(b"manual-subscription\n")
        await self.expect("manual-subscription")

    async def test_changed_instance_refresh_blocks_hardware_and_subscribe_actions(self):
        await self.start()
        await self.ready()
        with patch("uart_gateway.remote_group.query_channels", return_value=("new-instance", [])):
            self.commands.append("r")
            await self.expect("Gateway 已改變")
        with patch.object(self.devices[0], "reopen_rx") as opened:
            self.commands.extend(["o 1", "s 1"])
            await self.expect("缺少有效快照")
            await self.expect("請 Q 返回清單重新選擇")
            opened.assert_not_called()

    async def test_shared_display_failure_closes_both_clients(self):
        with patch("uart_gateway.cli._display", side_effect=OSError("模擬終端故障")):
            await self.start()
            with self.assertRaises(OSError):
                await asyncio.wait_for(self.task, 2)
        await eventually(lambda: not self.gateway.sessions)

    async def test_busy_reopen_does_not_retry_or_stop_peer_and_can_be_retried_after_refresh(self):
        await self.start()
        await self.ready()
        self.devices[0].close()
        await self.expect("裝置已失去連線")
        self.commands.append("r")
        await self.expect("通道：裝置遺失")
        with patch.object(self.devices[0], "reopen_rx", side_effect=SerialBackendError("PORT_BUSY", "模擬端點占用")) as opened:
            self.commands.extend(["o 1", "y"])
            await self.expect("占用時請關閉 MobaXterm")
            self.devices[1].inject(b"peer-after-busy\n")
            await self.expect("peer-after-busy")
            opened.assert_called_once()
        self.commands.append("r")
        await self.expect("通道：故障")
        self.commands.extend(["o 1", "y"])
        await self.expect("RX 重開成功")
        await self.ready()

    async def test_eof_closes_both_clients_without_reconnecting(self):
        def eof():
            raise EOFError

        await self.start(poll=eof)
        self.assertEqual(await asyncio.wait_for(self.task, 2), 0)
        self.assertIn("EOF", self.stderr.getvalue())
        await eventually(lambda: not self.gateway.sessions)

    async def test_output_capacity_failure_is_explicit_and_cleans_both(self):
        await self.start(sink_factory=lambda: _WatchOutput(terminal=True, maximum=32))
        with self.assertRaisesRegex(ConnectionError, "佇列已滿"):
            await asyncio.wait_for(self.task, 2)
        await eventually(lambda: not self.gateway.sessions)

    async def test_auth_failure_on_reopen_hides_message_and_peer_continues(self):
        await self.start()
        await self.ready()
        self.devices[0].close()
        await self.expect("裝置已失去連線")
        self.commands.append("r")
        await self.expect("通道：裝置遺失")
        error = RemoteError({"code": "AUTH_FAILED", "message": "must-not-echo-secret", "details": {}})
        with patch("uart_gateway.remote_group.reopen_channel", side_effect=error):
            self.commands.extend(["o 1", "y"])
            await self.expect("認證失敗（AUTH_FAILED）")
        self.devices[1].inject(b"peer-after-auth-failure\n")
        await self.expect("peer-after-auth-failure")
        self.assertNotIn("must-not-echo-secret", self.stderr.getvalue())

    async def test_cancel_during_reopen_reports_uncertainty_and_cleans_clients(self):
        await self.start()
        await self.ready()
        self.devices[0].close()
        await self.expect("裝置已失去連線")
        self.commands.append("r")
        await self.expect("通道：裝置遺失")
        entered = asyncio.Event()

        async def pending(*args):
            entered.set()
            await asyncio.Event().wait()

        with patch("uart_gateway.remote_group.reopen_channel", side_effect=pending):
            self.commands.extend(["o 1", "y"])
            await asyncio.wait_for(entered.wait(), 2)
            self.task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await self.task
        self.assertIn("Gateway 可能已接受請求", self.stderr.getvalue())
        await eventually(lambda: not self.gateway.sessions)


@unittest.skipUnless(os.name == "posix", "需要 POSIX PTY；Win11 主控台另行驗證")
class GroupProcessTests(unittest.IsolatedAsyncioTestCase):
    start = console_fixture.WorkbenchProcessTests.start
    expect = console_fixture.WorkbenchProcessTests.expect
    answer = console_fixture.WorkbenchProcessTests.answer
    finish = console_fixture.WorkbenchProcessTests.finish

    async def asyncSetUp(self):
        await console_fixture.WorkbenchProcessTests.asyncSetUp(self)
        self.token = secrets.token_urlsafe(32)
        self.devices = [FakeSerial(echo=False), FakeSerial(echo=False)]
        self.gateway = Gateway([(ChannelConfig(cid, cid), d) for cid, d in zip(("a", "b"), self.devices)],
            (Principal("operator", self.token, {cid: frozenset({"read", "configure"}) for cid in ("a", "b")}),))
        self.port = await self.gateway.start(port=0)

    async def asyncTearDown(self):
        await console_fixture.WorkbenchProcessTests.asyncTearDown(self)
        await self.gateway.close()

    async def launch(self):
        path = self.root / "auth.token"
        path.write_text(self.token)
        self.start([sys.executable, "-m", "uart_gateway.cli", "--port", str(self.port),
                    "--token-file", str(path), "menu", "--remote"])
        await self.expect("輸入 Channel 編號")
        self.answer("1,2")
        await self.expect("雙路模式")
        self.answer("")
        await self.expect("雙路工作區")
        await eventually(lambda: all(c.subscriptions for c in self.gateway.channels.values()))

    async def test_real_keyboard_unplug_reopen_peer_continues_and_q_cleans_clients(self):
        await self.launch()
        self.devices[0].inject(b"first-a\n")
        await self.expect("first-a")
        self.devices[1].inject(b"first-b\n")
        await self.expect("first-b")
        self.devices[0].close()
        await self.expect("裝置已失去連線")
        self.answer("r")
        await self.expect("通道：裝置遺失")
        self.answer("o 1")
        await self.expect("輸入 Y 確認")
        self.devices[1].inject(b"while-confirming\n")
        await self.expect("while-confirming")
        self.answer("y")
        await self.expect("RX 重開成功")
        await eventually(lambda: bool(self.gateway.channels["a"].subscriptions))
        self.devices[0].inject(b"after-reopen\n")
        await self.expect("after-reopen")
        self.answer("q")
        await self.expect("已返回清單")
        await self.expect("目前沒有監看連線")
        await eventually(lambda: not self.gateway.sessions)
        await self.finish()
        self.assertTrue(all(c.state == "ready" for c in self.gateway.channels.values()))
        self.assertTrue(all(d.write_count == 0 for d in self.devices))
        self.assertNotIn(self.token, self.output.decode(errors="replace"))

    async def test_ctrl_c_returns_to_menu_and_gateway_continues(self):
        await self.launch()
        self.process.send_signal(signal.SIGINT)
        await self.expect("雙路監看已中止")
        await self.expect("目前沒有監看連線")
        await eventually(lambda: not self.gateway.sessions)
        await self.finish()
        self.devices[1].inject(b"still-running")
        await eventually(lambda: self.gateway.channels["b"].offset == 13)

    async def test_q_then_dual_replay_with_unequal_long_history_keeps_both_streams(self):
        for index in range(20):
            self.gateway.channels["a"].ingest(f"a-history-{index:04d}\n".encode())
        for index in range(1200):
            self.gateway.channels["b"].ingest(f"b-history-{index:04d}\n".encode())
        await self.launch()
        self.answer("q")
        await self.expect("目前沒有監看連線")
        self.answer("r")
        await self.expect("輸入 Channel 編號")
        self.answer("1,2")
        await self.expect("雙路模式")
        self.answer("r")
        await self.expect("b-history-1199")
        if b"a-history-0019" not in self.output:
            await self.expect("a-history-0019")
        transcript = self.output.decode(errors="replace")
        self.assertIn("a-history-0019", transcript)
        self.assertNotIn("監看失敗", transcript)
        self.devices[0].inject(b"a-live-after-replay\n")
        await self.expect("a-live-after-replay")
        self.devices[1].inject(b"b-live-after-replay\n")
        await self.expect("b-live-after-replay")
        self.answer("q")
        await self.expect("目前沒有監看連線")
        await self.finish()
