import asyncio
import secrets
import threading
import unittest
from unittest.mock import patch

from uart_gateway.client import Client, TxUncertain
from uart_gateway.cli import watch_client, safe_text
from uart_gateway.text_renderer import RESET
from uart_gateway.client.transport import LocalDataLoss
from uart_gateway.core.models import ChannelConfig
from uart_gateway.fake_serial import FakeSerial
from uart_gateway.protocol.codec import binary_decode, binary_encode, encode_frame, read_frame
from uart_gateway.protocol.errors import RemoteError
from uart_gateway.server.auth import Principal
from uart_gateway.server.tcp import Gateway
from uart_gateway.server.config import Limits
from tests.fakes import FakeClock, eventually


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_replay_budget_rejection_removes_new_subscription_and_keeps_connection_usable(self):
        self.gateway.channels["a"].ingest(b"history")
        client = await self.client(True)
        channel = await client.get_channel("a")
        session = self.gateway.sessions[client.auth["session_id"]]
        session.replay_max = 1
        with self.assertRaises(RemoteError) as error:
            await client.subscribe("a", epoch=channel["stream_epoch"], offset="0")
        self.assertEqual(error.exception.code, "QUEUE_FULL")
        self.assertFalse(self.gateway.channels["a"].subscriptions)
        self.assertFalse(session.replays)
        self.assertFalse(session.closed)
        self.assertEqual((await client.get_channel("a"))["state"], "ready")
        await client.subscribe("a")
        self.ports[0].inject(b"live")
        event = await asyncio.wait_for(client.next_event(), 1)
        self.assertEqual(binary_decode(event["data"]["data_b64"]), b"live")

    async def test_replay_survives_temporarily_stalled_console(self):
        import io
        import time

        class Console(io.StringIO):
            def write(self, value):
                time.sleep(0.2)
                return super().write(value)

        payload = (b"boot\r\n" + b"x" * 140) * 400
        for offset in range(0, len(payload), 146):
            self.gateway.channels["a"].ingest(payload[offset:offset + 146])
        for terminal in (False, True):
            client = await self.client(True)
            console = Console()
            with self.subTest(terminal=terminal), patch("sys.stdout", console), patch("sys.stderr", io.StringIO()):
                result = await watch_client(client, "a", duration=1, text_mode=True, replay=True,
                                            terminal_mode=terminal)
                self.assertEqual(result, 0)
                expected = payload.decode() if terminal else safe_text(payload.decode())
                self.assertEqual(console.getvalue().replace(RESET, ""), expected)
                self.assertIsNone(client.transport.failure)

    async def test_stalled_terminal_does_not_block_event_consumption_or_rpc(self):
        import io

        entered, release = threading.Event(), threading.Event()

        class Console(io.StringIO):
            def write(self, value):
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("測試未釋放終端輸出")
                return super().write(value)

        payload = b"x" * 58400
        for offset in range(0, len(payload), 146):
            self.gateway.channels["a"].ingest(payload[offset:offset + 146])
        client = await self.client(True)
        received = 0
        original = client.next_event

        async def next_event():
            nonlocal received
            event = await original()
            if event["event"] == "rx":
                received += len(binary_decode(event["data"]["data_b64"]))
            return event

        console = Console()
        with patch.object(client, "next_event", side_effect=next_event), \
                patch("sys.stdout", console), patch("sys.stderr", io.StringIO()):
            task = asyncio.create_task(watch_client(client, "a", duration=None, text_mode=True,
                                                    replay=True, terminal_mode=True))
            try:
                await eventually(entered.is_set)
                await eventually(lambda: received == len(payload))
                self.assertIsNone(client.transport.failure)
                self.assertFalse(task.done())
                self.assertEqual((await asyncio.wait_for(client.get_channel("a"), 1))["state"], "ready")
            finally:
                release.set()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertEqual(console.getvalue().replace(RESET, ""), payload.decode())

    async def test_text_replay_small_chunks_preserves_output_with_slow_console(self):
        import io
        import time

        class Console(io.StringIO):
            def write(self, value):
                time.sleep(0.001)
                return super().write(value)

        payload = (b"boot\r\n" + b"x" * 140) * 400
        for offset in range(0, len(payload), 146):
            self.gateway.channels["a"].ingest(payload[offset:offset + 146])
        for terminal in (False, True):
            client = await self.client(True)
            console = Console()
            expected = payload.decode() if terminal else safe_text(payload.decode())
            with self.subTest(terminal=terminal), patch("sys.stdout", console), patch("sys.stderr", io.StringIO()):
                task = asyncio.create_task(watch_client(client, "a", duration=None, text_mode=True,
                                                        replay=True, terminal_mode=terminal))
                try:
                    await eventually(lambda: task.done() or
                        len(console.getvalue().replace(RESET, "")) >= len(expected), timeout=5)
                    if task.done():
                        self.fail(f"重播應持續監看，實際結束結果：{await task}")
                finally:
                    if not task.done():
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
            self.assertEqual(console.getvalue().replace(RESET, ""), expected)
            self.assertIsNone(client.transport.failure)

    async def asyncSetUp(self):
        self.token, self.read_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.ports = [FakeSerial(), FakeSerial()]
        self.clock = FakeClock()
        self.gateway = Gateway([(ChannelConfig("a", "A"), self.ports[0]),
                                (ChannelConfig("b", "B"), self.ports[1])],
            (Principal("operator", self.token, {cid: frozenset({"read", "tx", "configure"}) for cid in ("a", "b")}),
             Principal("reader", self.read_token, {"a": frozenset({"read"})})), clock=self.clock)
        self.port = await self.gateway.start(port=0)
        self.clients = []

    async def client(self, readonly=False):
        client = await Client.connect("127.0.0.1", self.port, self.read_token if readonly else self.token)
        self.clients.append(client)
        return client

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        await self.gateway.close()

    async def rx(self, client, sub_id):
        async with asyncio.timeout(2):
            while True:
                event = await client.next_event()
                if event["event"] == "rx" and event["data"]["subscription_id"] == sub_id:
                    return event

    async def test_full_binary_path_broadcast_and_channel_isolation(self):
        owner, reader = await self.client(), await self.client(True)
        a = await owner.confirm(await owner.get_channel("a"), "A")
        b = await owner.confirm(await owner.get_channel("b"), "B")
        sub1 = await owner.subscribe("a")
        sub2 = await reader.subscribe("a")
        sub_b = await owner.subscribe("b")
        payload = bytes(range(256))
        async with await owner.acquire(a) as lease:
            pending = await lease.write_bytes(payload)
            self.assertEqual(pending["state"], "pending")
            final = await owner.wait_tx("a", pending["tx_id"])
            self.assertEqual(final["state"], "written_to_driver")
            for client, sub in ((owner, sub1), (reader, sub2)):
                event = await self.rx(client, sub["subscription_id"])
                self.assertEqual(binary_decode(event["data"]["data_b64"]), payload)
        self.assertEqual(self.ports[1].write_count, 0)
        self.ports[1].inject(b"B-only")
        event = await self.rx(owner, sub_b["subscription_id"])
        self.assertEqual(binary_decode(event["data"]["data_b64"]), b"B-only")
        self.assertEqual((await owner.get_channel("b"))["binding"], b["binding"])

    async def test_authorization_hides_channels_and_rejects_tx(self):
        reader = await self.client(True)
        self.assertEqual([x["channel_id"] for x in await reader.list_channels()], ["a"])
        for method, params in (("channel.get", {"channel_id": "b"}),
            ("lease.acquire", {"channel_id": "a", "expected": reader.expected(await reader.get_channel("a"))})):
            with self.assertRaises(RemoteError) as error:
                await reader.transport.request(method, params)
            self.assertEqual(error.exception.code, "PERMISSION_DENIED")

    async def test_disconnect_releases_lease_and_ends_subscriptions(self):
        owner, other = await self.client(), await self.client()
        a = await owner.confirm(await owner.get_channel("a"), "A")
        await owner.subscribe("a")
        await owner.acquire(a)
        with self.assertRaises(RemoteError) as error:
            await other.acquire(a)
        self.assertEqual(error.exception.code, "LEASE_BUSY")
        await owner.close()
        channel = self.gateway.channels["a"]
        await eventually(lambda: channel.lease.owner is None and not channel.subscriptions)
        lease = await other.acquire(a)
        self.assertEqual(lease.token, "2")
        await lease.close()

    async def test_reconnect_can_query_result_but_cannot_replay_old_write(self):
        owner = await self.client()
        a = await owner.confirm(await owner.get_channel("a"), "A")
        lease = await owner.acquire(a)
        pending = await lease.write_bytes(b"once", tx_id="same_id")
        await owner.wait_tx("a", pending["tx_id"])
        await owner.close()
        other = await self.client()
        result = await other.tx_status("a", "same_id")
        self.assertEqual(result["state"], "written_to_driver")
        cached = await other.transport.request("tx.write", {"channel_id": "a", "expected": other.expected(a),
            "fencing_token": lease.token, "tx_id": "same_id", "data_b64": binary_encode(b"once")})
        self.assertEqual(cached["tx"]["state"], "written_to_driver")
        self.assertEqual(self.ports[0].write_count, 1)
        unknown = await other.tx_status("a", "missing")
        self.assertEqual((unknown["state"], unknown["status_origin"]), ("unknown", "unavailable"))
        restarted = await other.tx_status("a", "same_id", instance="old_instance")
        self.assertEqual(restarted["reason_code"], "SERVER_RESTARTED")

    async def test_replay_preserves_bytes_delivery_and_epoch(self):
        client = await self.client()
        self.ports[0].inject(b"boot\x00\xff\n")
        await eventually(lambda: self.gateway.channels["a"].offset == 7)
        a = await client.get_channel("a")
        sub = await client.subscribe("a", epoch=a["stream_epoch"], offset="2")
        event = await self.rx(client, sub["subscription_id"])
        self.assertEqual(event["data"]["delivery"], "replay")
        self.assertEqual(binary_decode(event["data"]["data_b64"]), b"ot\x00\xff\n")
        await client.unsubscribe(sub["subscription_id"])
        closed = await client.rebind(a)
        new = await client.confirm(closed, "new_board")
        self.assertNotEqual(new["stream_epoch"], a["stream_epoch"])
        with self.assertRaises(RemoteError) as error:
            await client.subscribe("a", epoch=a["stream_epoch"], offset="7")
        self.assertEqual(error.exception.code, "EPOCH_MISMATCH")

    async def test_idle_owner_lease_expires_with_fake_clock(self):
        first, second = await self.client(), await self.client()
        a = await first.confirm(await first.get_channel("a"), "A")
        old = await first.acquire(a)
        self.clock.advance(16)
        fresh = await second.acquire(a)
        with self.assertRaises(RemoteError) as error:
            await old.write_bytes(b"stale")
        self.assertEqual(error.exception.code, "LEASE_NOT_OWNER")
        self.assertEqual(self.ports[0].write_count, 0)
        await fresh.close()

    async def test_raw_tcp_handshake_fragmentation_and_invalid_order(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        packet = encode_frame({"type": "request", "id": "1", "method": "hello", "params": {
            "versions": [{"major": 1, "minor": 0}], "client_name": "test", "client_version": "1"}})
        writer.write(packet[:10])
        await writer.drain()
        writer.write(packet[10:] + encode_frame({"type": "request", "id": "2", "method": "auth.authenticate",
            "params": {"scheme": "token", "token": self.token}}))
        await writer.drain()
        self.assertTrue((await read_frame(reader))["ok"])
        self.assertTrue((await read_frame(reader))["ok"])
        writer.write(encode_frame({"type": "request", "id": "2", "method": "ping", "params": {}}))
        await writer.drain()
        error = await asyncio.wait_for(read_frame(reader), 2)
        self.assertEqual(error["error"]["code"], "INVALID_REQUEST_ID")
        self.assertEqual(await reader.read(), b"")
        writer.close()
        await writer.wait_closed()

    async def test_bad_auth_and_version_fail_without_establishing_session(self):
        with self.assertRaises(RemoteError) as error:
            await Client.connect("127.0.0.1", self.port, secrets.token_urlsafe(32))
        self.assertEqual(error.exception.code, "AUTH_FAILED")
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(encode_frame({"type": "request", "id": "1", "method": "hello", "params": {
            "versions": [{"major": 2, "minor": 0}], "client_name": "test", "client_version": "1"}}))
        await writer.drain()
        self.assertEqual((await read_frame(reader))["error"]["code"], "UNSUPPORTED_VERSION")
        writer.close()
        await writer.wait_closed()

    async def test_bind_failure_does_not_open_serial_and_closes_channels(self):
        backend = FakeSerial()
        second = Gateway([(ChannelConfig("other", "other"), backend)],
            (Principal("operator", self.token, {"other": frozenset({"read"})}),))
        with patch.object(backend, "open", wraps=backend.open) as opened, self.assertRaises(OSError):
            await second.start(port=self.port)
        opened.assert_not_called()
        self.assertEqual(second.channels["other"].state, "closed")
        self.assertFalse(second.channels["other"].running)

    async def test_binding_change_notifies_old_subscriber(self):
        client = await self.client()
        old = await client.get_channel("a")
        sub = await client.subscribe("a")
        new = await client.confirm(old, "changed")
        ended = await asyncio.wait_for(client.next_event(), 1)
        state = await asyncio.wait_for(client.next_event(), 1)
        self.assertEqual(ended["event"], "subscription_state")
        self.assertEqual(ended["data"]["subscription_id"], sub["subscription_id"])
        self.assertEqual(state["event"], "channel_state")
        self.assertEqual(state["data"]["channel"]["stream_epoch"], new["stream_epoch"])

    async def test_lost_tx_response_exposes_id_and_does_not_retry(self):
        client = await self.client()
        a = await client.confirm(await client.get_channel("a"), "A")
        lease = await client.acquire(a)
        original = client.transport.request

        async def lose_reply(method, params):
            result = await original(method, params)
            if method == "tx.write":
                raise TimeoutError("模擬回覆遺失")
            return result

        with patch.object(client.transport, "request", side_effect=lose_reply):
            with self.assertRaises(TxUncertain) as error:
                await lease.write_bytes(b"once", tx_id="uncertain")
        self.assertEqual(error.exception.tx_id, "uncertain")
        final = await client.wait_tx("a", error.exception.tx_id)
        self.assertEqual(final["state"], "written_to_driver")
        self.assertEqual(self.ports[0].write_count, 1)
        await lease.close()

    async def test_local_event_overflow_is_visible_and_does_not_block_other_clients(self):
        slow, healthy = await self.client(), await self.client()
        await slow.subscribe("a")
        slow.transport.event_bytes = 1
        self.ports[0].inject(b"overflow")
        with self.assertRaises(LocalDataLoss):
            await asyncio.wait_for(slow.next_event(), 1)
        self.assertEqual((await healthy.get_channel("b"))["state"], "ready")

    async def test_replay_yielding_consumer_preserves_bytes_and_rpc_without_overflow(self):
        payload = bytes(range(256)) * 16
        for _ in range(192):
            self.gateway.channels["a"].ingest(payload)
        client = await self.client(True)
        channel = await client.get_channel("a")
        sub = await client.subscribe("a", epoch=channel["stream_epoch"], offset="0")
        received = bytearray()
        query = None
        async with asyncio.timeout(5):
            for index in range(192):
                event = await self.rx(client, sub["subscription_id"])
                self.assertEqual(event["data"]["offset"], str(len(received)))
                received.extend(binary_decode(event["data"]["data_b64"]))
                if index == 96:
                    query = asyncio.create_task(client.get_channel("a"))
                # 模擬畫面處理每筆之後交回事件迴圈。
                await asyncio.sleep(0)
            self.assertEqual((await query)["state"], "ready")
        self.assertEqual(bytes(received), payload * 192)
        self.assertIsNone(client.transport.failure)
        self.assertLessEqual(client.transport.event_used, client.transport.event_bytes)

    async def test_event_count_limit_still_rejects_a_consumer_that_never_reads(self):
        slow, healthy = await self.client(), await self.client()
        await slow.subscribe("a")
        for _ in range(300):
            self.gateway.channels["a"].ingest(b"x")
        await eventually(lambda: slow.transport.failure is not None)
        with self.assertRaises(LocalDataLoss):
            await slow.next_event()
        self.assertEqual(slow.transport.event_used, 0)
        self.assertEqual((await healthy.get_channel("b"))["state"], "ready")

    async def test_heartbeat_and_lease_context_renew_without_user_traffic(self):
        limits = Limits(heartbeat_interval_ms=1000, idle_timeout_ms=3000,
                        lease_ttl_ms=3000, lease_renew_interval_ms=1000)
        gateway = Gateway([(ChannelConfig("x", "X"), FakeSerial())],
            (Principal("operator", self.token, {"x": frozenset({"read", "tx", "configure"})}),), limits=limits)
        port = await gateway.start(port=0)
        try:
            async with await Client.connect("127.0.0.1", port, self.token) as client:
                channel = await client.confirm(await client.get_channel("x"), "X")
                async with await client.acquire(channel) as lease:
                    initial = gateway.channels["x"].lease.deadline
                    await asyncio.sleep(3.15)
                    self.assertTrue(lease.active)
                    self.assertGreater(gateway.channels["x"].lease.deadline, initial)
                    self.assertEqual((await client.get_channel("x"))["tx"]["state"], "leased")
        finally:
            await gateway.close()

    async def test_oversized_frame_is_rejected_before_lf(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(b"x" * 65536)
        await writer.drain()
        reply = await asyncio.wait_for(read_frame(reader), 1)
        self.assertEqual(reply["error"]["code"], "FRAME_TOO_LARGE")
        self.assertIsNone(reply["id"])
        self.assertEqual(await reader.read(), b"")
        writer.close()
        await writer.wait_closed()

    async def test_parallel_confirms_cannot_reserve_the_same_label(self):
        first, other = await self.client(), await self.client()
        a = await first.rebind(await first.get_channel("a"))
        b = await other.rebind(await other.get_channel("b"))
        gate = threading.Event()
        original = self.ports[0].open

        def delayed_open():
            if not gate.wait(1):
                raise OSError("測試開啟逾時")
            original()

        with patch.object(self.ports[0], "open", side_effect=delayed_open):
            opening = asyncio.create_task(first.confirm(a, "shared"))
            try:
                await eventually(lambda: self.gateway.channels["a"].state == "opening")
                with self.assertRaises(RemoteError) as error:
                    await other.confirm(b, "shared")
                self.assertEqual(error.exception.code, "INVALID_PARAMS")
            finally:
                gate.set()
                await opening
        self.assertEqual((await first.get_channel("a"))["label"], "shared")
        self.assertEqual((await other.get_channel("b"))["label"], "B")
