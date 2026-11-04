"""Linux 虛擬端點的真 pySerial 測試；不宣稱 Windows／IPCAM 已驗證。"""

import asyncio
import importlib.util
import os
import secrets
import unittest

from uart_gateway.client import Client
from uart_gateway.core.models import ChannelConfig, SerialSettings
from uart_gateway.protocol.codec import binary_decode
from uart_gateway.serial_backend import AdapterSelector, PySerialBackend
from uart_gateway.server.auth import Principal
from uart_gateway.server.tcp import Gateway
from tests.fakes import eventually


@unittest.skipUnless(os.name == "posix" and importlib.util.find_spec("serial") is not None,
                     "需要 POSIX PTY 與 [server] 選用依賴；不是 Windows 硬體測試")
class RealPySerialTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.master, self.slave = os.openpty()
        os.set_blocking(self.master, False)
        self.path = os.ttyname(self.slave)
        self.token = secrets.token_urlsafe(32)
        self.settings = SerialSettings(baudrate=57600)
        self.backend = PySerialBackend(AdapterSelector(port=self.path), self.settings)
        self.gateway = Gateway([(ChannelConfig("pty", "virtual", serial=self.settings), self.backend)],
            (Principal("operator", self.token, {"pty": frozenset({"read", "tx", "configure"})}),))
        port = await self.gateway.start(port=0)
        self.client = await Client.connect("127.0.0.1", port, self.token)

    async def asyncTearDown(self):
        await self.client.close()
        await self.gateway.close()
        if self.master >= 0:
            os.close(self.master)
        os.close(self.slave)

    async def test_native_binary_rx_and_lease_tx_preserve_profile(self):
        channel = await self.client.get_channel("pty")
        self.assertEqual(channel["state"], "ready")
        self.assertEqual(channel["binding"]["status"], "unconfirmed")
        self.assertEqual(channel["adapter"]["port"], self.path)
        sub = await self.client.subscribe("pty")
        data = bytes(range(256))
        os.write(self.master, data)
        received = bytearray()
        async with asyncio.timeout(2):
            while len(received) < len(data):
                event = await self.client.next_event()
                if event["event"] == "rx" and event["data"]["subscription_id"] == sub["subscription_id"]:
                    received.extend(binary_decode(event["data"]["data_b64"]))
        self.assertEqual(bytes(received), data)
        channel = await self.client.confirm(channel, "virtual_confirmed")
        self.assertEqual(channel["serial"]["baudrate"], 57600)
        async with await self.client.acquire(channel) as lease:
            pending = await lease.write_bytes(b"virtual_only\n")
            result = await self.client.wait_tx("pty", pending["tx_id"])
            self.assertEqual(result["state"], "written_to_driver")
        output = bytearray()
        async with asyncio.timeout(2):
            while len(output) < 13:
                try:
                    output.extend(os.read(self.master, 4096))
                except BlockingIOError:
                    await asyncio.sleep(0.002)
        self.assertEqual(bytes(output), b"virtual_only\n")

    async def test_device_removal_revokes_lease_without_reopening(self):
        channel = await self.client.confirm(await self.client.get_channel("pty"), "virtual")
        await self.client.acquire(channel)
        os.close(self.master)
        self.master = -1
        await eventually(lambda: self.gateway.channels["pty"].state == "lost")
        status = await self.client.get_channel("pty")
        self.assertEqual(status["last_error"]["code"], "DEVICE_MISSING")
        self.assertEqual(status["adapter"]["match_status"], "missing")
        self.assertIsNone(status["tx"]["owner_session_id"])
        self.assertIsNone(status["stream_epoch"])
        self.assertEqual(status["binding"]["status"], "unconfirmed")

    async def test_duplicate_channel_endpoint_fault_does_not_stop_other_rx(self):
        other = PySerialBackend(AdapterSelector(port=self.path), self.settings)
        gateway = Gateway([(ChannelConfig("busy", "busy"), other)],
            (Principal("reader", self.token, {"busy": frozenset({"read"})}),))
        port = await gateway.start(port=0)
        try:
            async with await Client.connect("127.0.0.1", port, self.token) as client:
                status = await client.get_channel("busy")
                self.assertEqual(status["state"], "faulted")
                self.assertEqual(status["last_error"]["code"], "PORT_BUSY")
            self.assertEqual((await self.client.get_channel("pty"))["state"], "ready")
        finally:
            await gateway.close()
