"""Gateway 設定、錄製清理與稽核的完整操作驗證。"""

import asyncio
import json
from pathlib import Path
import secrets
import tempfile
import threading
import unittest

from uart_gateway.client import Client
from uart_gateway.core.models import ChannelConfig
from uart_gateway.fake_serial import FakeSerial
from uart_gateway.gateway_config import manage_config
from uart_gateway.protocol.errors import RemoteError
from uart_gateway.recording_retention import retention
from uart_gateway.server.audit import AuditConfig, query_audit
from uart_gateway.server.auth import Principal
from uart_gateway.server.config import Limits
from uart_gateway.server.recording import RecordingConfig
from uart_gateway.server.tcp import Gateway
from tests.fakes import eventually


class OperationsTests(unittest.IsolatedAsyncioTestCase):
    async def test_configure_is_fenced_and_audit_correlates_final_tx(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tx = secrets.token_urlsafe(32)
            reader = secrets.token_urlsafe(32)
            device, peer = FakeSerial(), FakeSerial()
            gateway = Gateway([(ChannelConfig("a", "A"), device), (ChannelConfig("b", "B"), peer)],
                (Principal("operator", tx, {"a": frozenset({"read", "configure", "tx"}), "b": frozenset({"read"})}),
                 Principal("observer", reader, {"a": frozenset({"read"})})), audit=AuditConfig(root))
            port = await gateway.start(port=0)
            try:
                async with await Client.connect("127.0.0.1", port, tx) as owner, \
                        await Client.connect("127.0.0.1", port, reader) as observer:
                    original = await owner.get_channel("a")
                    peer_original = await owner.get_channel("b")
                    await owner.subscribe("b")
                    with self.assertRaises(RemoteError) as denied:
                        await observer.configure_rx(original, {"read_timeout_ms": 10}, instance=observer.instance)
                    self.assertEqual(denied.exception.code, "PERMISSION_DENIED")
                    with self.assertRaises(RemoteError) as restarted:
                        await owner.configure_rx(original, {"read_timeout_ms": 10}, instance="old_instance")
                    self.assertEqual(restarted.exception.code, "INSTANCE_MISMATCH")
                    confirmed = await owner.confirm(original, "A")
                    async with await owner.acquire(confirmed) as lease:
                        with self.assertRaises(RemoteError) as busy:
                            await owner.configure_rx(confirmed, {"read_timeout_ms": 10}, instance=owner.instance)
                        self.assertEqual(busy.exception.code, "LEASE_BUSY")
                        pending = await lease.write_bytes(b"PRIVATE_PAYLOAD")
                        tx_id = pending["tx_id"]
                        await owner.wait_tx("a", tx_id)
                    updated = await owner.configure_rx(confirmed, {"read_timeout_ms": 10}, instance=owner.instance)
                    self.assertEqual(updated["serial"]["read_timeout_ms"], 10)
                    self.assertEqual(updated["binding"]["status"], "unconfirmed")
                    self.assertNotEqual(updated["stream_epoch"], confirmed["stream_epoch"])
                    self.assertEqual(device.settings.read_timeout_ms, 10)
                    self.assertEqual(device.write_count, 1)
                    self.assertEqual((await owner.get_channel("b"))["stream_epoch"], peer_original["stream_epoch"])
                    peer.inject(b"still running")
                    async with asyncio.timeout(3):
                        while (await owner.next_event())["channel_id"] != "b":
                            pass
                    with self.assertRaises(RemoteError) as stale:
                        await owner.configure_rx(confirmed, {"read_timeout_ms": 15}, instance=owner.instance)
                    self.assertIn(stale.exception.code, {"STALE_BINDING", "EPOCH_MISMATCH"})
            finally:
                await gateway.close()
            events = query_audit(root, tx_id=tx_id)
            self.assertEqual({row["method"] for row in events}, {"tx.write", "tx.result"})
            self.assertEqual(events[0]["outcome"], "written_to_driver")
            operator = query_audit(root, principal="operator", channel="a")
            self.assertIn("channel.configure_rx", {row["method"] for row in operator})
            self.assertNotIn("PRIVATE_PAYLOAD", json.dumps(operator))
            self.assertNotIn(tx, json.dumps(operator))

    async def test_retention_keeps_active_capture_and_rejects_stale_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            token = secrets.token_urlsafe(32)

            def create():
                return Gateway([(ChannelConfig("a", "A"), FakeSerial())],
                    (Principal("reader", token, {"a": frozenset({"read"})}),),
                    recording=RecordingConfig(root))

            old, active = create(), create()
            await old.start(port=0)
            await old.close()
            await active.start(port=0)
            try:
                preview = retention(root, max_bytes=1)
                old_name, active_name = (f"capture-{item.store.instance}" for item in (old, active))
                self.assertEqual([item["capture"] for item in preview["candidates"]], [old_name])
                self.assertEqual(preview["protected"], [active_name])
                with self.assertRaises(ValueError):
                    retention(root, max_bytes=2, apply=preview["plan"])
                result = retention(root, max_bytes=1, apply=preview["plan"])
                self.assertEqual(result["removed"], [old_name])
                self.assertTrue(result["target_exceeded"])
                self.assertTrue((root / active_name).exists())
            finally:
                await active.close()

    def test_settings_preview_has_no_credential_references_and_apply_is_exclusive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current, candidate, output = (root / name for name in ("current.toml", "candidate.toml", "new.toml"))
            raw = Path("examples/gateway.com3.toml").read_text(encoding="utf-8")
            current.write_text(raw, encoding="utf-8")
            candidate.write_text(raw.replace("115200", "9600").replace("UART_GATEWAY_TOKEN", "PRIVATE_REFERENCE"),
                                 encoding="utf-8")
            kwargs = {"candidate": candidate, "fake": False}
            preview = manage_config(current, output=None, apply=None, **kwargs)
            self.assertNotIn("PRIVATE_REFERENCE", json.dumps(preview))
            self.assertEqual(preview["credential_references_changed"], ["local_observer"])
            manage_config(current, output=output, apply=preview["plan"], **kwargs)
            self.assertIn("baudrate = 9600", output.read_text())
            self.assertEqual(current.read_text(encoding="utf-8"), raw)
            with self.assertRaises(FileExistsError):
                manage_config(current, output=output, apply=preview["plan"], **kwargs)

    async def test_history_gap_and_recording_disk_collision_keep_live_rx(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            token = secrets.token_urlsafe(32)
            gateway = Gateway([(ChannelConfig("a", "A"), FakeSerial())],
                (Principal("reader", token, {"a": frozenset({"read"})}),),
                recording=RecordingConfig(root, segment_bytes=65536),
                limits=Limits(rx_ring_bytes=8192))
            port = await gateway.start(port=0)
            try:
                recorder = gateway.recorders["a"]
                (recorder.directory / "000002.jsonl").mkdir()
                async with await Client.connect("127.0.0.1", port, token) as reader:
                    old = await reader.get_channel("a")
                    await reader.subscribe("a")
                    for _ in range(15):
                        gateway.channels["a"].ingest(b"x" * 4096)
                        await eventually(lambda: recorder.used == 0)
                    self.assertEqual(recorder.health, {"state": "failed", "integrity": "incomplete"})
                    with self.assertRaises(RemoteError) as gap:
                        await reader.subscribe("a", epoch=old["stream_epoch"], offset="0")
                    self.assertEqual(gap.exception.code, "HISTORY_GAP")
                    async with asyncio.timeout(3):
                        while (event := await reader.next_event())["event"] != "recording_state":
                            pass
                        self.assertEqual(event["data"]["reason_code"], "RECORDING_IO_FAILED")
                        gateway.channels["a"].ingest(b"live after disk error")
                        while True:
                            event = await reader.next_event()
                            if event["event"] == "rx" and event["data"]["offset"] == str(15 * 4096):
                                break
            finally:
                await gateway.close()

    async def test_settings_interrupts_read_before_reopening_handle(self):
        entered, released = threading.Event(), threading.Event()

        class BlockingReader(FakeSerial):
            def read(self, size):
                entered.set()
                if not released.wait(2):
                    raise AssertionError("應先中斷讀取")
                return super().read(size)

            def interrupt_read(self):
                released.set()
                super().interrupt_read()

        token = secrets.token_urlsafe(32)
        gateway = Gateway([(ChannelConfig("a", "A"), BlockingReader())],
            (Principal("operator", token, {"a": frozenset({"read", "configure"})}),))
        port = await gateway.start(port=0)
        try:
            await eventually(entered.is_set)
            async with await Client.connect("127.0.0.1", port, token) as client:
                old = await client.get_channel("a")
                updated = await client.configure_rx(old, {"read_timeout_ms": 30}, instance=client.instance)
                self.assertTrue(released.is_set())
                self.assertNotEqual(updated["stream_epoch"], old["stream_epoch"])
                self.assertEqual(updated["state"], "ready")
        finally:
            released.set()
            await gateway.close()

    async def test_retention_keeps_all_candidates_if_one_hash_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            token = secrets.token_urlsafe(32)
            captures = []
            for _ in range(2):
                gateway = Gateway([(ChannelConfig("a", "A"), FakeSerial())],
                    (Principal("reader", token, {"a": frozenset({"read"})}),),
                    recording=RecordingConfig(root))
                await gateway.start(port=0)
                await gateway.close()
                captures.append(root / f"capture-{gateway.store.instance}")
            segment = captures[-1] / "a" / "000001.jsonl"
            segment.write_bytes(segment.read_bytes().replace(b'"label":"A"', b'"label":"B"', 1))
            plan = retention(root, max_bytes=1)
            self.assertEqual(len(plan["candidates"]), 2)
            with self.assertRaisesRegex(ValueError, "未通過檢查"):
                retention(root, max_bytes=1, apply=plan["plan"])
            self.assertTrue(all(path.is_dir() for path in captures))
