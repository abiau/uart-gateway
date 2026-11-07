"""新 TX 工作流程的真 pySerial PTY；不代表 Win11 或 IPCAM 驗收。"""

import asyncio
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

from uart_gateway.client import Client
from uart_gateway.client.tx_receipts import TxJournal
from uart_gateway.client.tx_workflow import TxWorkflow
from tests.integration import test_serial_backend as fixture


@unittest.skipUnless(os.name == "posix" and importlib.util.find_spec("serial"), "需要 POSIX PTY／pySerial")
class TxPTYTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.RealPySerialTests.asyncSetUp
    asyncTearDown = fixture.RealPySerialTests.asyncTearDown

    async def test_new_workflow_writes_exact_binary_and_keeps_uart_settings(self):
        port = self.client.transport.writer.get_extra_info("peername")[1]
        async def connect():
            return await Client.connect("127.0.0.1", port, self.token)
        with tempfile.TemporaryDirectory() as temporary:
            workflow = TxWorkflow(connect, TxJournal(Path(temporary)))
            before = await self.client.get_channel("pty")
            target = await workflow.acknowledge(await workflow.prepare(before, self.client.instance))
            data = b"PTY\x00\x7f\xff\r\n"
            receipt = await workflow.send(target, data)
            received = bytearray()
            async with asyncio.timeout(3):
                while len(received) < len(data):
                    try:
                        received.extend(os.read(self.master, 4096))
                    except BlockingIOError:
                        await asyncio.sleep(0.002)
            self.assertEqual(received, data)
            self.assertEqual(receipt.result["accepted_bytes"], len(data))
            self.assertEqual((await self.client.get_channel("pty"))["serial"], before["serial"])
            self.assertFalse(self.backend._serial.dtr)
            self.assertFalse(self.backend._serial.rts)
            self.assertIsNone(self.gateway.channels["pty"].lease.owner)
