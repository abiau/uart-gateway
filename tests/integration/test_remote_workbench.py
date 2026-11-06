"""真 Gateway／SDK 與遠端選單整合；不操作公司或實體 COM。"""

import asyncio
import io
import os
from pathlib import Path
import secrets
import signal
import sys
import unittest
from unittest.mock import patch

from uart_gateway.cli import StreamSelectionChanged, watch_client
from uart_gateway.client import Client
from uart_gateway.core.models import ChannelConfig
from uart_gateway.fake_serial import FakeSerial
from uart_gateway.protocol.errors import RemoteError
from uart_gateway.remote_workbench import query_channels, monitor_channel
from uart_gateway.server.auth import Principal
from uart_gateway.server.tcp import Gateway
from tests.fakes import eventually
from tests.integration import test_workbench as console_fixture


class RemoteSdkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.token = secrets.token_urlsafe(32)
        self.devices = [FakeSerial(echo=False) for _ in range(3)]
        self.gateway = Gateway([(ChannelConfig(cid, cid), device) for cid, device in
                                zip(("a", "b", "hidden"), self.devices)],
            (Principal("reader", self.token, {cid: frozenset({"read"}) for cid in ("a", "b")}),))
        self.port = await self.gateway.start(port=0)
        self.clients = []

    async def connect(self):
        client = await Client.connect("127.0.0.1", self.port, self.token)
        self.clients.append(client)
        return client

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        await self.gateway.close()

    async def monitor(self, client, cid, replay, expected):
        return await watch_client(client, cid, duration=0.15, text_mode=True, terminal_mode=True,
                                  replay=replay, expected_context=expected)

    async def selected(self):
        instance, channels = await query_channels(self.connect)
        self.assertEqual([c["channel_id"] for c in channels], ["a", "b"])
        self.assertTrue(self.clients[-1].transport.reader_task.done())
        return instance, channels[0]

    async def exercise_watch(self, replay):
        self.devices[0].inject(b"old\n")
        await eventually(lambda: self.gateway.channels["a"].offset == 4)
        instance, selected = await self.selected()
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            task = asyncio.create_task(monitor_channel(self.connect, self.monitor, selected, instance, replay))
            try:
                await eventually(lambda: bool(self.gateway.channels["a"].subscriptions))
                self.devices[0].inject(b"new\n")
                self.assertEqual(await asyncio.wait_for(task, 2), 0)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertIn("new", stdout.getvalue())
        self.assertEqual("old" in stdout.getvalue(), replay)
        self.assertEqual(self.gateway.channels["a"].state, "ready")
        self.assertTrue(all(client.transport.reader_task.done() for client in self.clients))
        self.assertTrue(all(device.write_count == 0 for device in self.devices))

    async def test_live_only_and_query_filters_acl(self):
        await self.exercise_watch(False)

    async def test_replay_then_live_and_connections_close(self):
        await self.exercise_watch(True)

    async def test_restarted_gateway_snapshot_is_rejected_before_rx(self):
        instance, selected = await self.selected()
        with patch("sys.stdout", io.StringIO()) as stdout:
            with self.assertRaises(StreamSelectionChanged):
                await monitor_channel(self.connect, self.monitor, selected, instance + "old", False)
        self.assertFalse(stdout.getvalue())
        self.assertFalse(self.gateway.channels["a"].subscriptions)
        self.assertTrue(self.clients[-1].transport.reader_task.done())

    async def test_real_subscription_epoch_race_never_displays_wrong_stream(self):
        for replay in (False, True):
            instance, selected = await self.selected()
            original = Client.subscribe
            channel = self.gateway.channels["a"]

            async def raced(client, *args, **kwargs):
                # 僅在 Fake 裝置安排競態，不透過 UI 呼叫 Confirm 或傳送 UART。
                channel.confirmed = False
                channel.confirm(channel.context, "changed", None)
                result = await original(client, *args, **kwargs)
                channel.ingest(b"must-not-display")
                return result

            with self.subTest(replay=replay), patch.object(Client, "subscribe", new=raced), \
                    patch("sys.stdout", io.StringIO()) as stdout:
                with self.assertRaises((StreamSelectionChanged, RemoteError)):
                    await monitor_channel(self.connect, self.monitor, selected, instance, replay)
                self.assertFalse(stdout.getvalue())
            self.assertFalse(channel.subscriptions)
            self.assertTrue(self.clients[-1].transport.reader_task.done())

    async def test_device_loss_ends_only_monitor_and_keeps_peer_ready(self):
        instance, selected = await self.selected()
        async def continuous(client, cid, replay, expected):
            return await watch_client(client, cid, duration=None, text_mode=True, replay=replay,
                                      expected_context=expected)
        with patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()) as stderr:
            task = asyncio.create_task(monitor_channel(self.connect, continuous, selected, instance, False))
            try:
                await eventually(lambda: bool(self.gateway.channels["a"].subscriptions))
                self.devices[0].close()
                self.assertEqual(await asyncio.wait_for(task, 2), 2)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertIn("裝置已失去連線", stderr.getvalue())
        self.assertEqual(self.gateway.channels["b"].state, "ready")
        self.assertTrue(self.clients[-1].transport.reader_task.done())

    async def test_connection_drop_keeps_gateway_alive_and_manual_query_works(self):
        instance, selected = await self.selected()
        with patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()):
            task = asyncio.create_task(monitor_channel(self.connect, self.monitor, selected, instance, False))
            try:
                await eventually(lambda: bool(self.gateway.channels["a"].subscriptions))
                self.clients[-1].transport.writer.close()
                with self.assertRaises(OSError):
                    await asyncio.wait_for(task, 2)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.gateway.channels["a"].state, "ready")
        new_instance, _ = await self.selected()
        self.assertEqual(instance, new_instance)


@unittest.skipUnless(os.name == "posix", "需要 POSIX PTY；Win11 遠端工作台另行驗證")
class RemoteMenuProcessTests(unittest.IsolatedAsyncioTestCase):
    start = console_fixture.WorkbenchProcessTests.start
    expect = console_fixture.WorkbenchProcessTests.expect
    answer = console_fixture.WorkbenchProcessTests.answer
    finish = console_fixture.WorkbenchProcessTests.finish

    async def asyncSetUp(self):
        await console_fixture.WorkbenchProcessTests.asyncSetUp(self)
        self.token = secrets.token_urlsafe(32)
        self.device = FakeSerial(echo=False)
        self.gateway = Gateway([(ChannelConfig("a", "IPCAM"), self.device)],
                               (Principal("reader", self.token, {"a": frozenset({"read"})}),))
        self.port = await self.gateway.start(port=0)

    async def asyncTearDown(self):
        await console_fixture.WorkbenchProcessTests.asyncTearDown(self)
        await self.gateway.close()

    async def test_cli_token_reload_live_replay_ctrl_c_and_gateway_not_stopped(self):
        token_file = self.root / "gateway.token"
        token_file.write_text(secrets.token_urlsafe(32))
        self.start([sys.executable, "-m", "uart_gateway.cli", "--port", str(self.port),
                    "--token-file", str(token_file), "menu", "--remote"])
        await self.expect("AUTH_FAILED")
        await self.expect("尚未連線；")
        token_file.write_text(self.token)
        self.answer("r")
        await self.expect("輸入 Channel 編號")
        self.answer("1")
        await self.expect("L 或 Enter")
        self.answer("")
        await eventually(lambda: bool(self.gateway.channels["a"].subscriptions))
        self.device.inject(b"remote live\n")
        await self.expect("remote live")
        self.process.send_signal(signal.SIGINT)
        await self.expect("監看已中止")
        await self.expect("輸入 Channel 編號")
        self.assertEqual(self.gateway.channels["a"].state, "ready")
        self.answer("1")
        await self.expect("L 或 Enter")
        self.answer("r")
        await self.expect("歷史重播")
        await self.expect("remote live")
        self.process.send_signal(signal.SIGINT)
        await self.expect("輸入 Channel 編號")
        await self.finish()
        self.assertNotIn(self.token, self.output.decode(errors="replace"))
        self.assertNotIn("本次建立的 Gateway", self.output.decode(errors="replace"))
        self.assertEqual(self.device.write_count, 0)
        self.device.inject(b"still running")
        await eventually(lambda: self.gateway.channels["a"].offset == 25)
