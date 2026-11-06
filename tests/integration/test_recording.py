"""真 Gateway／SDK 與模擬裝置錄製；PTY 不等於 Win11 實機。"""

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import secrets
import signal
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from uart_gateway.client import Client
from uart_gateway.core.models import ChannelConfig
from uart_gateway.fake_serial import FakeSerial
from uart_gateway.local_launcher import _gateway_profile
from uart_gateway.local_profile import LocalChannel
from uart_gateway.protocol.codec import binary_decode
from uart_gateway.recording_info import inspect_capture
from uart_gateway.serial_backend import AdapterSelector
from uart_gateway.server.app import load
from uart_gateway.server.auth import Principal
from uart_gateway.server.recording import RecordingConfig
from uart_gateway.server.tcp import Gateway
from tests.fakes import eventually
from tests.integration.test_local_launcher import free_port


class RecordingIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.token = secrets.token_urlsafe(32)
        self.devices = [FakeSerial(echo=False), FakeSerial(echo=False)]
        principal = Principal("reader", self.token, {cid: frozenset({"read"}) for cid in ("a", "b")})
        self.gateway = Gateway([(ChannelConfig(cid, label), device) for cid, label, device in
                                zip(("a", "b"), ("主控台", "MCU"), self.devices)], (principal,),
                               recording=RecordingConfig(self.root, queue_bytes=65536))
        self.port = await self.gateway.start(port=0)
        self.clients = []

    async def client(self):
        client = await Client.connect("127.0.0.1", self.port, self.token)
        self.clients.append(client)
        return client

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        await self.gateway.close()
        self.temporary.cleanup()

    def capture(self, cid):
        return self.gateway.recorders[cid].directory

    def payload(self, cid):
        data = bytearray()
        for file in sorted(self.capture(cid).glob("*.jsonl")):
            for line in file.read_bytes().splitlines():
                row = json.loads(line)
                if row["type"] == "rx":
                    data.extend(binary_decode(row["data_b64"]))
        return bytes(data)

    async def test_no_clients_then_replay_and_client_disconnect_do_not_duplicate_or_stop_capture(self):
        payload = b"\x00\xff\x1b[32mboot\r\n"
        self.devices[0].inject(payload)
        await eventually(lambda: self.gateway.channels["a"].offset == len(payload))
        for _ in range(2):
            client = await self.client()
            channel = await client.get_channel("a")
            self.assertEqual(channel["recording"]["state"], "recording")
            await client.subscribe("a", epoch=channel["stream_epoch"], offset="0")
            event = await client.next_event()
            self.assertEqual(binary_decode(event["data"]["data_b64"]), payload)
            await client.close()
        self.devices[0].inject(b"after disconnect\n")
        await eventually(lambda: self.gateway.channels["a"].offset == len(payload) + 17)
        await self.gateway.close()
        self.assertEqual(self.payload("a"), payload + b"after disconnect\n")
        self.assertEqual(inspect_capture(self.capture("a"))["integrity"], "no_known_gap")
        self.assertTrue(all(device.write_count == 0 for device in self.devices))
        raw = b"".join(path.read_bytes() for path in self.capture("a").glob("*.jsonl"))
        self.assertNotIn(self.token.encode(), raw)

    async def test_usb_loss_marks_unknown_and_other_channel_continues(self):
        self.devices[0].inject(b"first")
        await eventually(lambda: self.gateway.channels["a"].offset == 5)
        self.devices[0].close()
        await eventually(lambda: self.gateway.channels["a"].state == "lost")
        self.devices[1].inject(b"still receiving")
        await eventually(lambda: self.gateway.channels["b"].offset == 15)
        client = await self.client()
        self.assertEqual((await client.get_channel("a"))["recording"], {"state": "degraded", "integrity": "unknown"})
        await self.gateway.close()
        self.assertEqual(inspect_capture(self.capture("a"))["integrity"], "unknown")
        self.assertEqual(inspect_capture(self.capture("b"))["integrity"], "no_known_gap")
        self.assertEqual(self.payload("b"), b"still receiving")

    async def test_manual_rx_reopen_preserves_recorded_epochs_and_loss_integrity(self):
        self.gateway.principals = (Principal("manager", self.token, {"a": frozenset({"read", "configure"})}),)
        client = await self.client()
        old_epoch = self.gateway.channels["a"].epoch
        self.devices[0].inject(b"before")
        await eventually(lambda: self.gateway.channels["a"].offset == 6)
        self.devices[0].close()
        await eventually(lambda: self.gateway.channels["a"].state == "lost")
        result = await client.reopen_rx(await client.get_channel("a"))
        self.devices[0].inject(b"after")
        await eventually(lambda: self.gateway.channels["a"].offset == 5)
        await self.gateway.close()
        rows = [json.loads(line) for path in sorted(self.capture("a").glob("*.jsonl"))
                for line in path.read_bytes().splitlines()]
        chunks = [row for row in rows if row["type"] == "rx"]
        self.assertEqual([row["stream_epoch"] for row in chunks], [old_epoch, result["stream_epoch"]])
        self.assertEqual([row["offset"] for row in chunks], ["0", "0"])
        self.assertEqual(self.payload("a"), b"beforeafter")
        self.assertEqual(inspect_capture(self.capture("a"))["integrity"], "unknown")
        self.assertEqual(inspect_capture(self.capture("b"))["integrity"], "no_known_gap")
        self.assertEqual(self.devices[0].write_count, 0)

    async def test_slow_disk_overflow_notifies_sdk_without_blocking_either_rx(self):
        entered, release = threading.Event(), threading.Event()
        recorder = self.gateway.recorders["a"]
        original = recorder._write

        def slow(raw, snap):
            entered.set()
            if not release.wait(5):
                raise OSError("測試未釋放磁碟")
            original(raw, snap)

        client = await self.client()
        await client.subscribe("a")
        with patch.object(recorder, "_write", side_effect=slow):
            try:
                self.devices[0].inject(b"start")
                await eventually(entered.is_set)
                for _ in range(20):
                    self.gateway.channels["a"].ingest(b"x" * 4096)
                async with asyncio.timeout(2):
                    while True:
                        event = await client.next_event()
                        if event["event"] == "recording_state" and event["data"]["state"] == "failed":
                            self.assertEqual(event["data"]["reason_code"], "RECORDING_OVERFLOW")
                            break
                self.assertEqual((await client.get_channel("a"))["state"], "ready")
                self.devices[0].inject(b"after overflow")
                self.devices[1].inject(b"peer")
                await eventually(lambda: self.gateway.channels["a"].offset == 5 + 20 * 4096 + 14)
                await eventually(lambda: self.gateway.channels["b"].offset == 4)
                self.assertEqual((await client.get_channel("a"))["recording"]["integrity"], "incomplete")
            finally:
                release.set()
                await self.gateway.close()
        self.assertEqual(inspect_capture(self.capture("a"))["integrity"], "incomplete")
        self.assertEqual(inspect_capture(self.capture("b"))["integrity"], "no_known_gap")

    async def test_disk_error_is_channel_local_and_api_remains_available(self):
        recorder = self.gateway.recorders["a"]
        await eventually(lambda: recorder.used == 0)
        with patch.object(recorder, "_write", side_effect=OSError("模擬磁碟已滿")):
            self.devices[0].inject(b"first")
            await eventually(lambda: recorder.state == "failed")
        self.devices[0].inject(b"second")
        self.devices[1].inject(b"peer")
        await eventually(lambda: self.gateway.channels["a"].offset == 11 and self.gateway.channels["b"].offset == 4)
        client = await self.client()
        self.assertEqual((await client.get_channel("a"))["state"], "ready")
        self.assertEqual((await client.get_channel("a"))["recording"]["state"], "failed")
        self.assertEqual((await client.get_channel("b"))["recording"]["state"], "recording")

    async def test_shutdown_with_connected_client_does_not_wait_forever_before_finalizing(self):
        client = await self.client()
        await client.subscribe("a")
        self.devices[0].inject(b"shutdown")
        await eventually(lambda: self.gateway.channels["a"].offset == 8)
        await asyncio.wait_for(self.gateway.close(), 2)
        await eventually(lambda: client.transport.failure is not None)
        result = inspect_capture(self.capture("a"))
        self.assertTrue(result["closed_cleanly"])
        self.assertEqual(result["rx_bytes"], 8)

    async def test_config_loader_reuses_existing_serial_profile_and_validates_before_open(self):
        channels = (LocalChannel("ch_uart_1", "測試", AdapterSelector(port="COM3")),)
        path = self.root / "test.toml"
        profile = _gateway_profile(channels, 18081, self.root)
        path.write_text(profile, encoding="utf-8")
        with patch.dict(os.environ, {"UART_GATEWAY_TOKEN": self.token}), patch("uart_gateway.serial_backend.PySerialBackend.open") as opened:
            gateway, _, _ = load(path, fake=False)
            self.assertEqual(len(gateway.recorders), 1)
            self.assertFalse((self.root / ("capture-" + gateway.store.instance)).exists())
            opened.assert_not_called()
            for invalid in (
                profile.replace(json.dumps(str(self.root)), '"relative"'),
                profile.replace('[recording]', '[recording]\nqueue_bytes = true'),
                profile.replace('enabled = true', 'enabled = "yes"'),
                profile.replace('[recording]', '[recording]\nretention_days = 7'),
            ):
                path.write_text(invalid, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load(path, fake=False)
            opened.assert_not_called()


@unittest.skipUnless(os.name == "posix" and importlib.util.find_spec("serial") is not None,
                     "需要 POSIX PTY 與 [server]；不是 Win11 錄製驗收")
class LocalRecordingTests(unittest.IsolatedAsyncioTestCase):
    async def test_cli_rx_and_sigint_finalize_recording_and_release_port(self):
        master, slave = os.openpty()
        with tempfile.TemporaryDirectory() as directory:
            process = await asyncio.create_subprocess_exec(sys.executable, "-m", "uart_gateway.cli", "local",
                "--port", os.ttyname(slave), "--gateway-port", str(free_port()), "--record-dir", directory,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
            try:
                async with asyncio.timeout(5):
                    while True:
                        line = (await process.stderr.readline()).decode()
                        self.assertTrue(line)
                        if "已就緒；" in line:
                            break
                os.write(master, b"recorded boot\n")
                self.assertIn(b"recorded boot", await asyncio.wait_for(process.stdout.readline(), 3))
                process.send_signal(signal.SIGINT)
                _, error = await asyncio.wait_for(process.communicate(), 5)
                self.assertEqual(process.returncode, 130, error.decode())
                captures = list(Path(directory).glob("capture-*/ch_uart_1"))
                self.assertEqual(len(captures), 1)
                info = inspect_capture(captures[0])
                self.assertEqual(info["rx_bytes"], 14)
                self.assertEqual(info["integrity"], "no_known_gap")
                import serial
                with serial.Serial(os.ttyname(slave), exclusive=True):
                    pass
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                os.close(master)
                os.close(slave)
