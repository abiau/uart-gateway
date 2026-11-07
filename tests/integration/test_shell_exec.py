"""Shell Runner 的真 TCP／SDK 與可控模擬 Shell；不操作實體 IPCAM。"""

import asyncio
import copy
import json
from pathlib import Path
import re
import secrets
import tempfile
import unittest
from unittest.mock import patch

from uart_gateway.client import Client, ShellCancelled, ShellRunner, ShellTargets, TxJournal
from uart_gateway.client.shell_marker import markers
from uart_gateway.core.models import ChannelConfig
from uart_gateway.fake_serial import FakeSerial
from uart_gateway.protocol.errors import RemoteError
from uart_gateway.protected_files import write_private
from uart_gateway.server.auth import Principal
from uart_gateway.server.tcp import Gateway
from tests.fakes import eventually


class FakeShell(FakeSerial):
    def __init__(self):
        super().__init__(echo=False)
        self.body = b"Linux ipcam\r\n"
        self.code = 0
        self.mode = "complete"
        self.execution_id = None

    def feed(self, data):
        for start in range(0, len(data), 4096):
            self.inject(data[start:start+4096])

    def complete(self):
        self.feed(b"\n" + markers(self.execution_id)[1] + str(self.code).encode() + b"\n# ")

    def write(self, data):
        accepted = super().write(data)
        self.execution_id = re.search(rb"'([0-9a-f]{32})__'", data)[1].decode()
        self.feed(data)  # TTY echo；不應被誤認為命令完成。
        if self.mode != "silent":
            self.feed(b"\n" + markers(self.execution_id)[0] + b"\n" + self.body)
            if self.mode == "complete":
                self.complete()
        return accepted


class ShellExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.token = secrets.token_urlsafe(32)
        self.device = FakeShell()
        self.peer = FakeSerial(echo=False)
        self.gateway = Gateway([(ChannelConfig("a", "ipcam"), self.device), (ChannelConfig("b", "peer"), self.peer)],
            (Principal("operator", self.token, {cid: frozenset({"read", "configure", "tx"}) for cid in ("a", "b")}),))
        self.port = await self.gateway.start(port=0)
        self.runner = ShellRunner(self.connect, TxJournal(self.root / "receipts"), ShellTargets(self.root / "targets"))

    async def asyncTearDown(self):
        await self.gateway.close()

    async def connect(self):
        return await Client.connect("127.0.0.1", self.port, self.token)

    async def ready(self):
        return await self.runner.authorize(await self.runner.inspect("a"), "lf")

    async def test_explicit_authorization_then_exec_and_replay_query_never_resend(self):
        with self.assertRaises(ValueError):
            await self.runner.execute("a", "uname -a")
        await self.ready()
        self.assertEqual(self.device.write_count, 0)
        result = await self.runner.execute("a", "uname -a")
        value = result.as_dict()
        self.assertEqual((value["state"], value["exit_code"], value["output_text"]), ("completed", 0, "Linux ipcam\r\n"))
        self.assertTrue(value["output_complete"])
        self.assertTrue(value["background_output_possible"])
        self.assertEqual(self.device.write_count, 1)
        self.assertIsNone(self.gateway.channels["a"].lease.owner)
        token = self.gateway.channels["a"].lease.token
        queried = await self.runner.status(value["execution_id"])
        self.assertEqual(queried.as_dict()["output_text"], value["output_text"])
        self.assertEqual(self.gateway.channels["a"].lease.token, token)
        self.assertEqual(self.device.write_count, 1)
        text = self.runner.journal.path(result.receipt).read_text()
        self.assertNotIn("uname", text)
        self.assertNotIn("Linux ipcam", text)

    async def test_nonzero_exit_and_truncated_output_remain_separate(self):
        await self.ready()
        self.device.code = 7
        self.device.body = b"x" * 50000
        result = await self.runner.execute("a", "false", maximum=16)
        value = result.as_dict()
        self.assertEqual((value["state"], value["exit_code"], result.process_exit_code), ("completed", 7, 1))
        self.assertEqual(len(value["output_text"]), 16)
        self.assertTrue(value["output_truncated"])
        self.assertFalse(value["output_complete"])
        self.assertEqual(value["observed_output_bytes"], 50000)

    async def test_timeout_blocks_next_exec_until_query_observes_delayed_completion(self):
        await self.ready()
        self.device.mode = "begin"
        result = await self.runner.execute("a", "slow", timeout=0.1)
        self.assertEqual(result.as_dict()["state"], "unknown")
        self.assertIsNone(result.as_dict()["exit_code"])
        with self.assertRaisesRegex(ValueError, "尚未結案"):
            await self.runner.execute("a", "must-not-queue")
        self.assertEqual(self.device.write_count, 1)
        self.device.complete()
        queried = await self.runner.status(result.receipt.tx_id)
        self.assertEqual(queried.as_dict()["state"], "completed")
        self.device.mode = "complete"
        await self.runner.execute("a", "new-command")
        self.assertEqual(self.device.write_count, 2)

    async def test_lease_covers_entire_command_and_peer_rx_continues(self):
        await self.ready()
        self.device.mode = "begin"
        task = asyncio.create_task(self.runner.execute("a", "slow", timeout=3))
        try:
            await eventually(lambda: self.device.write_count == 1)
            async with await self.connect() as other:
                with self.assertRaises(RemoteError) as error:
                    await other.acquire(await other.get_channel("a"))
                self.assertEqual(error.exception.code, "LEASE_BUSY")
                await other.subscribe("b")
                self.peer.inject(b"peer continues\n")
                async with asyncio.timeout(2):
                    while (await other.next_event())["event"] != "rx":
                        pass
            self.device.complete()
            result = await task
            self.assertEqual(result.as_dict()["state"], "completed")
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_cancel_and_lease_loss_keep_unknown_receipts_without_ctrl_c(self):
        await self.ready()
        self.device.mode = "begin"
        task = asyncio.create_task(self.runner.execute("a", "slow"))
        await eventually(lambda: self.device.write_count == 1)
        task.cancel()
        with self.assertRaises(ShellCancelled) as error:
            await task
        self.assertEqual(error.exception.result.as_dict()["state"], "unknown")
        self.assertIsNone(self.gateway.channels["a"].lease.owner)
        self.assertEqual(self.device.write_count, 1)
        # 由測試操作者明確重新核對；不透過 Runner 自動復原 Shell。
        await self.ready()
        task = asyncio.create_task(self.runner.execute("a", "another"))
        await eventually(lambda: self.device.write_count == 2)
        self.gateway.channels["a"].revoke("expired", "LEASE_EXPIRED")
        result = await task
        self.assertEqual(result.as_dict()["state"], "unknown")
        self.assertFalse(result.as_dict()["output_complete"])
        self.assertEqual(self.device.write_count, 2)

    async def test_stale_target_and_device_loss_are_not_automatically_confirmed(self):
        await self.ready()
        async with await self.connect() as other:
            closed = await other.rebind(await other.get_channel("a"))
            await other.confirm(closed, "new-board")
        with self.assertRaises(ValueError):
            await self.runner.execute("a", "stale")
        self.assertEqual(self.device.write_count, 0)
        await self.ready()
        self.device.mode = "begin"
        task = asyncio.create_task(self.runner.execute("a", "slow"))
        await eventually(lambda: self.device.write_count == 1)
        self.device.close()
        result = await task
        self.assertEqual(result.as_dict()["state"], "unknown")
        self.assertIsNone(result.as_dict()["exit_code"])
        self.assertFalse(self.gateway.channels["a"].confirmed)

    async def test_old_instance_query_preserves_known_completion_but_not_old_output(self):
        await self.ready()
        result = await self.runner.execute("a", "known")
        await self.gateway.close()
        self.gateway = Gateway([(ChannelConfig("a", "new"), self.device)],
            (Principal("operator", self.token, {"a": frozenset({"read", "configure", "tx"})}),))
        self.port = await self.gateway.start(port=0)
        queried = await self.runner.status(result.receipt.tx_id)
        value = queried.as_dict()
        self.assertEqual((value["state"], value["exit_code"]), ("completed", 0))
        self.assertIsNone(value["output_text"])
        self.assertFalse(value["output_complete"])
        self.assertEqual(self.device.write_count, 1)

    async def test_query_wait_follows_original_live_cursor_without_new_lease(self):
        await self.ready()
        self.device.mode = "begin"
        result = await self.runner.execute("a", "slow", timeout=0.1)
        fencing = self.gateway.channels["a"].lease.token
        query = asyncio.create_task(self.runner.status(result.receipt.tx_id, wait=2))
        await eventually(lambda: bool(self.gateway.channels["a"].subscriptions))
        self.device.complete()
        value = (await query).as_dict()
        self.assertEqual((value["state"], value["exit_code"]), ("completed", 0))
        self.assertTrue(value["output_complete"])
        self.assertEqual(self.gateway.channels["a"].lease.token, fencing)
        self.assertEqual(self.device.write_count, 1)

    async def test_lost_replay_history_keeps_unknown_and_never_resends(self):
        await self.ready()
        self.device.mode = "begin"
        result = await self.runner.execute("a", "slow", timeout=0.1)
        channel = self.gateway.channels["a"]
        channel.ring_bytes = 8192
        self.device.feed(b"x" * 20000)
        await eventually(lambda: channel.earliest > int(result.receipt.execution["start_offset"]))
        self.device.complete()
        query = await self.runner.status(result.receipt.tx_id)
        self.assertEqual(query.as_dict()["state"], "unknown")
        self.assertFalse(query.as_dict()["output_complete"])
        self.assertEqual(self.device.write_count, 1)

    async def test_receipt_failure_after_write_preserves_observed_completion_in_memory(self):
        await self.ready()
        original = self.runner.journal.save
        def save(receipt, *, create=False):
            if create:
                return original(receipt, create=True)
            raise OSError("模擬收據磁碟故障")
        with patch.object(self.runner.journal, "save", side_effect=save):
            value = (await self.runner.execute("a", "known")).as_dict()
        self.assertEqual((value["state"], value["exit_code"]), ("completed", 0))
        self.assertFalse(value["receipt_saved"])
        self.assertEqual(value["reason_code"], "RECEIPT_SAVE_FAILED")
        with self.assertRaisesRegex(ValueError, "尚未結案"):
            await self.runner.execute("a", "must-not-repeat")
        self.assertEqual(self.device.write_count, 1)

    async def test_corrupt_authorization_fails_before_any_uart_write(self):
        await self.ready()
        path = self.runner.targets.path("a")
        original = json.loads(path.read_text())
        for field, value in (("adapter", []), ("binding", None), ("stream_epoch", None), ("label", 3)):
            broken = copy.deepcopy(original)
            broken["channel"][field] = value
            write_private(path, json.dumps(broken).encode(), replace=True)
            with self.assertRaisesRegex(ValueError, "尚無有效"):
                await self.runner.execute("a", "must-not-send")
        self.assertEqual(self.device.write_count, 0)
