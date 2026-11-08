"""TX 工作流程的真 SDK／TCP／Fake Serial 與結果不明；非實體 UART。"""

import asyncio
import secrets
import tempfile
import threading
from pathlib import Path
import unittest
from unittest.mock import patch

from uart_gateway.client import Client, LeaseHandle, TxUncertain
from uart_gateway.client.tx_receipts import TxJournal
from uart_gateway.client.tx_workflow import TxWorkflow
from uart_gateway.core.models import ChannelConfig
from uart_gateway.fake_serial import FakeSerial
from uart_gateway.protocol.errors import RemoteError
from uart_gateway.server.auth import Principal
from uart_gateway.server.tcp import Gateway
from tests.fakes import eventually


class TxWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.token = secrets.token_urlsafe(32)
        self.devices = [FakeSerial(echo=False), FakeSerial(echo=False)]
        self.gateway = Gateway([(ChannelConfig(cid, cid), d) for cid, d in zip(("a", "b"), self.devices)],
            (Principal("writer", self.token, {cid: frozenset({"read", "configure", "tx"}) for cid in ("a", "b")}),))
        self.port = await self.gateway.start(port=0)
        self.workflow = TxWorkflow(self.connect, TxJournal(Path(self.temp.name) / "receipts"))

    async def asyncTearDown(self):
        for device in self.devices:
            if device.write_gate:
                device.write_gate.set()
        await self.gateway.close()

    async def connect(self):
        return await Client.connect("127.0.0.1", self.port, self.token)

    async def prepared(self):
        async with await self.connect() as client:
            snapshot = await client.get_channel("a")
        return await self.workflow.prepare(snapshot, self.gateway.store.instance)

    async def target(self):
        return await self.workflow.acknowledge(await self.prepared())

    async def test_prepare_is_zero_io_and_ack_send_release_and_query_are_exact(self):
        before = self.gateway.channels["a"].epoch
        target = await self.prepared()
        self.assertFalse(self.gateway.channels["a"].confirmed)
        self.assertIsNone(self.gateway.channels["a"].lease.owner)
        with self.assertRaisesRegex(ValueError, "未人工核對"):
            await self.workflow.send(target, b"no")
        target = await self.workflow.acknowledge(target)
        self.assertNotEqual(target.channel["stream_epoch"], before)
        receipt = await self.workflow.send(target, "測試q\\n\r\n".encode())
        self.assertEqual(receipt.result["state"], "written_to_driver")
        self.assertEqual(list(self.devices[0].writes), ["測試q\\n\r\n".encode()])
        self.assertIsNone(self.gateway.channels["a"].lease.owner)
        await self.workflow.query(receipt)
        self.assertEqual(self.devices[0].write_count, 1)
        self.assertNotIn("測試", self.workflow.journal.path(receipt).read_text())
        self.assertFalse(self.gateway.channels["a"].config.serial.dtr)

    async def test_stale_preview_and_competing_owner_never_write_or_steal(self):
        target = await self.target()
        async with await self.connect() as other:
            async with await other.acquire(target.channel):
                owner = self.gateway.channels["a"].lease.owner
                with self.assertRaises(RemoteError) as error:
                    await self.workflow.send(target, b"blocked")
                self.assertEqual(error.exception.code, "LEASE_BUSY")
                self.assertEqual(self.gateway.channels["a"].lease.owner, owner)
                self.assertEqual(self.workflow.journal.list(), [])
            closed = await other.rebind(target.channel)
            await other.confirm(closed, "changed-board")
        with self.assertRaisesRegex(ValueError, "已改變"):
            await self.workflow.send(target, b"stale")
        self.assertEqual(self.devices[0].write_count, 0)

    async def test_lost_write_response_is_queried_from_saved_receipt_without_resend(self):
        target = await self.target()
        original = LeaseHandle.write_bytes
        async def lost(lease, payload, *, tx_id):
            result = await original(lease, payload, tx_id=tx_id)
            await lease.client.wait_tx("a", result["tx_id"])
            raise TxUncertain(tx_id, "a", lease.client.instance)
        with patch.object(LeaseHandle, "write_bytes", lost):
            receipt = await self.workflow.send(target, b"only-once")
        self.assertEqual(receipt.result["state"], "unknown")
        resumed = TxWorkflow(self.connect, self.workflow.journal)
        saved = resumed.journal.list()[0]
        await resumed.query(saved)
        self.assertEqual(saved.result["state"], "written_to_driver")
        self.assertEqual(self.devices[0].write_count, 1)
        self.assertIsNone(self.gateway.channels["a"].lease.owner)

    async def test_partial_write_stays_unknown_and_blocked_without_recovery(self):
        target = await self.target()
        self.devices[0].write_result = 2
        receipt = await self.workflow.send(target, b"partial")
        self.assertEqual(receipt.result["state"], "unknown")
        self.assertEqual(receipt.result["reason_code"], "PARTIAL_WRITE")
        self.assertTrue(self.gateway.channels["a"].blocked)
        with self.assertRaisesRegex(ValueError, "封鎖"):
            await self.prepared()
        await self.workflow.query(receipt)
        self.assertEqual(self.devices[0].write_count, 1)

    async def test_cancellation_preserves_receipt_and_releases_only_own_lease(self):
        target = await self.target()
        self.devices[0].write_gate = threading.Event()
        task = asyncio.create_task(self.workflow.send(target, b"inflight"))
        try:
            await eventually(self.devices[0].write_entered.is_set)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(len(self.workflow.journal.list()), 1)
            self.assertIsNone(self.gateway.channels["a"].lease.owner)
            self.assertIn("不能撤回", self.workflow.notice)
        finally:
            self.devices[0].write_gate.set()
            await asyncio.gather(task, return_exceptions=True)
        await eventually(lambda: self.devices[0].write_count == 1)
        await self.workflow.query(self.workflow.last)
        self.assertEqual(self.devices[0].write_count, 1)

    async def test_failed_receipt_write_does_not_transmit_and_releases_lease(self):
        target = await self.target()
        with patch.object(self.workflow.journal, "save", side_effect=OSError("模擬磁碟失敗")):
            with self.assertRaises(OSError):
                await self.workflow.send(target, b"never-written")
        self.assertEqual(self.devices[0].write_count, 0)
        self.assertIsNone(self.gateway.channels["a"].lease.owner)

    async def test_restart_queries_original_instance_as_unknown_and_does_not_write(self):
        receipt = await self.workflow.send(await self.target(), b"before-restart")
        await self.gateway.close()
        self.gateway = Gateway([(ChannelConfig("a", "a"), self.devices[0])],
            (Principal("writer", self.token, {"a": frozenset({"read", "configure", "tx"})}),))
        self.port = await self.gateway.start(port=0)
        await self.workflow.query(receipt)
        self.assertEqual(receipt.last_query["state"], "unknown")
        self.assertEqual(receipt.last_query["reason_code"], "SERVER_RESTARTED")
        self.assertEqual(receipt.result["state"], "written_to_driver")
        self.assertEqual(self.devices[0].write_count, 1)

    async def test_other_principal_cannot_query_receipt(self):
        receipt = await self.workflow.send(await self.target(), b"private-result")
        other = secrets.token_urlsafe(32)
        self.gateway.principals += (Principal("different", other, {"a": frozenset({"read", "configure", "tx"})}),)
        async def connect_other():
            return await Client.connect("127.0.0.1", self.port, other)
        workflow = TxWorkflow(connect_other, self.workflow.journal)
        with self.assertRaisesRegex(ValueError, "另一個"):
            await workflow.query(receipt)
        self.assertEqual(self.devices[0].write_count, 1)

    async def test_cancelled_confirm_is_not_automatically_repeated(self):
        prepared = await self.prepared()
        accepted = asyncio.Event()
        original = Client.confirm
        async def delayed(client, *args):
            result = await original(client, *args)
            accepted.set()
            await asyncio.Event().wait()
            return result
        with patch.object(Client, "confirm", delayed):
            task = asyncio.create_task(self.workflow.acknowledge(prepared))
            await asyncio.wait_for(accepted.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(self.workflow.acknowledged)
        self.assertTrue(self.gateway.channels["a"].confirmed)
        with patch.object(Client, "confirm", side_effect=AssertionError("不可再次 Confirm")):
            refreshed = await self.workflow.acknowledge(await self.prepared())
        self.assertTrue(self.workflow.is_acknowledged(refreshed))
        self.assertEqual(self.devices[0].write_count, 0)

    async def test_pending_timeout_closes_lease_and_only_manual_query_continues(self):
        target = await self.target()
        self.devices[0].write_gate = threading.Event()
        try:
            receipt = await self.workflow.send(target, b"slow", wait=0.001)
            self.assertEqual(receipt.result["state"], "pending")
            self.assertIsNone(self.gateway.channels["a"].lease.owner)
        finally:
            self.devices[0].write_gate.set()
        await eventually(lambda: self.devices[0].write_count == 1)
        await self.workflow.query(receipt)
        self.assertNotEqual(receipt.result["state"], "pending")
        self.assertEqual(self.devices[0].write_count, 1)

    async def test_send_uses_terminal_event_without_status_roundtrip(self):
        target = await self.target()
        with patch.object(Client, "tx_status", side_effect=AssertionError("不應查詢已收到的終態")):
            receipt = await self.workflow.send(target, b"event")
        self.assertEqual(receipt.result["state"], "written_to_driver")
        self.assertEqual(self.devices[0].write_count, 1)
