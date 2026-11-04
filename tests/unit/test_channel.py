import asyncio
import threading
import unittest
from unittest.mock import patch

from uart_gateway.core.channel import Channel, TxStore
from uart_gateway.core.models import ChannelConfig, DomainError
from uart_gateway.fake_serial import FakeSerial
from tests.fakes import FakeClock, eventually


class ChannelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = FakeClock()
        self.events = []
        self.port = FakeSerial(echo=False)
        self.store = TxStore("instance", clock=self.clock)
        self.channel = Channel(ChannelConfig("ch", "board"), self.port, self.store,
            lambda owner, event: self.events.append((owner, event)), clock=self.clock, ring_bytes=600)
        await self.channel.start()

    async def asyncTearDown(self):
        if self.port.write_gate:
            self.port.write_gate.set()
        await self.channel.stop()

    def arm(self, session="owner"):
        self.channel.confirm(self.channel.context, "board", None)
        return self.channel.acquire(session, self.channel.context)

    def submit(self, tx_id="tx", session="owner", token=None, payload=b"command\n"):
        return self.channel.submit("operator", session, self.channel.context,
            token or self.channel.lease.token, tx_id, payload)

    async def test_confirmation_and_exclusive_lease(self):
        with self.assertRaises(DomainError) as error:
            self.channel.acquire("owner", self.channel.context)
        self.assertEqual(error.exception.code, "NOT_BOUND")
        first = self.arm()
        with self.assertRaises(DomainError) as error:
            self.channel.acquire("other", self.channel.context)
        self.assertEqual(error.exception.code, "LEASE_BUSY")
        self.clock.advance(2)
        same = self.channel.acquire("owner", self.channel.context)
        self.assertEqual(first["fencing_token"], same["fencing_token"])
        self.assertLess(same["remaining_ms"], first["remaining_ms"])

    async def test_expired_queued_write_never_reaches_backend(self):
        self.arm()
        record = self.submit()
        self.clock.advance(16)
        await eventually(lambda: record.state != "pending")
        self.assertEqual(record.state, "rejected")
        self.assertFalse(record.started)
        self.assertEqual(self.port.write_count, 0)
        second = self.channel.acquire("other", self.channel.context)
        self.assertEqual(second["fencing_token"], "2")

    async def test_disconnect_rejects_pending_without_io(self):
        self.arm()
        record = self.submit()
        self.channel.disconnect("owner")
        self.assertEqual(record.state, "rejected")
        self.assertEqual(record.reason, "SESSION_CLOSED")
        self.assertIsNone(self.channel.lease.owner)

    async def test_expiry_does_not_handover_inflight_io(self):
        self.arm()
        self.port.write_gate = threading.Event()
        record = self.submit()
        await eventually(self.port.write_entered.is_set)
        self.clock.advance(16)
        self.channel.expire()
        with self.assertRaises(DomainError) as error:
            self.channel.acquire("other", self.channel.context)
        self.assertEqual(error.exception.code, "CHANNEL_BUSY")
        self.port.write_gate.set()
        await eventually(lambda: record.state != "pending")
        self.assertEqual(record.state, "written_to_driver")
        self.channel.acquire("other", self.channel.context)

    async def test_fencing_and_stale_binding(self):
        first = self.arm()
        stale = self.channel.context
        self.channel.release("owner", stale, int(first["fencing_token"]))
        new = self.channel.acquire("owner", stale)
        with self.assertRaises(DomainError) as error:
            self.submit(token=int(first["fencing_token"]))
        self.assertEqual(error.exception.code, "LEASE_NOT_OWNER")
        self.channel.release("owner", stale, int(new["fencing_token"]))
        await self.channel.rebind(stale)
        with self.assertRaises(DomainError) as error:
            self.channel.acquire("owner", stale)
        self.assertEqual(error.exception.code, "STALE_BINDING")

    async def test_dedup_and_conflict_without_new_write(self):
        self.arm()
        record = self.submit()
        await eventually(lambda: record.state != "pending")
        self.channel.release("owner", self.channel.context, self.channel.lease.token)
        self.assertIs(self.submit(), record)
        self.assertEqual(self.port.write_count, 1)
        with self.assertRaises(DomainError) as error:
            self.submit(payload=b"different")
        self.assertEqual(error.exception.code, "TX_ID_CONFLICT")

    async def test_partial_write_is_unknown_and_blocks_new_tx(self):
        self.arm()
        self.port.write_result = 2
        record = self.submit()
        queued = self.submit("queued")
        await eventually(lambda: record.state != "pending")
        self.assertEqual((record.state, record.accepted), ("unknown", 2))
        self.assertEqual(queued.state, "rejected")
        with self.assertRaises(DomainError) as error:
            self.channel.acquire("other", self.channel.context)
        self.assertEqual(error.exception.code, "TX_BLOCKED")
        self.assertEqual(self.port.write_count, 1)

    async def test_queue_and_cache_capacity_reject_before_io(self):
        self.arm()
        self.channel.tx_items = 1
        self.submit()
        with self.assertRaises(DomainError) as error:
            self.submit("overflow")
        self.assertEqual(error.exception.code, "QUEUE_FULL")
        self.channel.disconnect("owner")
        self.channel.acquire("owner", self.channel.context)
        self.store.capacity = 1
        with self.assertRaises(DomainError) as error:
            self.submit("full_cache")
        self.assertEqual(error.exception.code, "RESULT_CACHE_FULL")
        self.clock.advance(601)
        self.assertIsNone(self.store.lookup("operator", "tx"))

    async def test_replay_slices_and_eviction_gap(self):
        self.channel.ingest(b"abcdef")
        result, events = self.channel.subscribe("reader", epoch=self.channel.epoch, offset=2)
        self.assertEqual(events[0].data["payload"], b"cdef")
        self.assertEqual(events[0].data["offset"], "2")
        self.assertEqual(events[0].data["chunk_offset"], "0")
        self.assertEqual(events[0].data["delivery"], "replay")
        self.channel.ingest(b"g" * 100)
        with self.assertRaises(DomainError) as error:
            self.channel.subscribe("reader", epoch=self.channel.epoch, offset=0)
        self.assertEqual(error.exception.code, "HISTORY_GAP")
        result, events = self.channel.subscribe("reader", epoch=self.channel.epoch, offset=0, skip=True)
        self.assertEqual(events[0].kind, "gap")
        self.assertEqual(events[1].data["delivery"], "replay")
        self.assertLessEqual(self.channel.ring_used, self.channel.ring_bytes)

    async def test_empty_read_is_not_disconnect_but_closed_port_is(self):
        await asyncio.sleep(0.05)
        self.assertEqual(self.channel.state, "ready")
        self.arm()
        self.port.close()
        await eventually(lambda: self.channel.state == "lost")
        self.assertIsNone(self.channel.lease.owner)
        self.assertFalse(self.channel.confirmed)
        self.assertIsNone(self.channel.epoch)

    async def test_write_timeout_is_unknown_and_never_retried(self):
        self.arm()
        self.port.write_error = TimeoutError("測試逾時")
        record = self.submit()
        await eventually(lambda: record.state != "pending")
        self.assertEqual((record.state, record.reason), ("unknown", "SERIAL_WRITE_TIMEOUT"))
        self.assertEqual(self.port.write_count, 1)
        self.assertTrue(self.channel.blocked)

    async def test_revision_exhaustion_never_wraps(self):
        self.channel.revision = 2**64 - 1
        with self.assertRaises(DomainError):
            self.channel.confirm(self.channel.context, "board", None)
        self.assertEqual(self.channel.revision, 2**64 - 1)

    async def test_close_failure_invalidates_target_without_claiming_closed(self):
        old = self.channel.context
        with patch.object(self.port, "close", side_effect=OSError("測試關閉失敗")):
            with self.assertRaises(DomainError) as error:
                await self.channel.rebind(old)
        self.assertEqual(error.exception.code, "SERIAL_IO_FAILED")
        self.assertEqual(self.channel.state, "faulted")
        self.assertIsNone(self.channel.epoch)
        self.assertGreater(self.channel.revision, old.revision)
        self.assertFalse(self.channel.confirmed)

    async def test_late_open_after_timeout_cannot_rearm_binding(self):
        port = FakeSerial()
        channel = Channel(ChannelConfig("late", "late"), port, self.store,
                          lambda owner, event: None, lifecycle_timeout=0.03)
        gate = threading.Event()
        original = port.open

        def delayed_open():
            if not gate.wait(1):
                raise OSError("測試開啟未收斂")
            original()

        try:
            with patch.object(port, "open", side_effect=delayed_open):
                with self.assertRaises(DomainError):
                    await channel.start()
                self.assertEqual(channel.state, "faulted")
                with self.assertRaises(DomainError) as error:
                    await channel.rebind(channel.context)
                self.assertEqual(error.exception.code, "CHANNEL_BUSY")
                gate.set()
                await asyncio.wait_for(asyncio.shield(channel.operation), 1)
            self.assertFalse(channel.confirmed)
            self.assertIsNone(channel.epoch)
            self.assertFalse(channel.running)
            channel.lifecycle_timeout = 1
            await channel.rebind(channel.context)
            await channel.start()
            self.assertFalse(channel.confirmed)
        finally:
            gate.set()
            channel.lifecycle_timeout = 1
            await channel.stop()
