"""真 pySerial／虛擬 UART／POSIX sh 往返；不代表 IPCAM 實機驗收。"""

import asyncio
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from uart_gateway.client import Client, ShellRunner, ShellTargets, TxJournal
from tests.integration import test_serial_backend as fixture


@unittest.skipUnless(os.name == "posix" and importlib.util.find_spec("serial"), "需要 POSIX PTY／pySerial")
class ShellPTYTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.RealPySerialTests.asyncSetUp
    asyncTearDown = fixture.RealPySerialTests.asyncTearDown

    async def test_real_shell_command_exit_output_and_readonly_replay(self):
        port = self.client.transport.writer.get_extra_info("peername")[1]
        async def connect():
            return await Client.connect("127.0.0.1", port, self.token)
        os.set_blocking(self.master, True)
        # 此測試專屬 Shell 充當 UART 另一端，不執行任何使用者提供的命令。
        process = subprocess.Popen(["/bin/sh", "+i"], stdin=self.master, stdout=self.master, stderr=self.master,
                                   start_new_session=True)
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                runner = ShellRunner(connect, TxJournal(root / "receipts"), ShellTargets(root / "targets"))
                before = await self.client.get_channel("pty")
                await runner.authorize(await runner.inspect("pty"), "lf")
                result = await runner.execute("pty", "printf '測試 UART\\n'; printf 'err\\n' >&2; exit 7")
                value = result.as_dict()
                observed = b"".join(chunk.data for chunk in self.gateway.channels["pty"].ring)
                self.assertEqual((value["state"], value["exit_code"]), ("completed", 7), (value, observed, process.poll()))
                self.assertIn("測試 UART", value["output_text"])
                self.assertIn("err", value["output_text"])
                self.assertTrue(value["output_complete"])
                fencing = self.gateway.channels["pty"].lease.token
                query = await runner.status(value["execution_id"])
                self.assertEqual(query.as_dict()["output_b64"], value["output_b64"])
                self.assertEqual(self.gateway.channels["pty"].lease.token, fencing)
                self.assertEqual((await self.client.get_channel("pty"))["serial"], before["serial"])
                self.assertFalse(self.backend._serial.dtr)
                self.assertFalse(self.backend._serial.rts)
                self.assertIsNone(self.gateway.channels["pty"].lease.owner)
        finally:
            process.terminate()
            try:
                await asyncio.wait_for(asyncio.to_thread(process.wait), 2)
            except TimeoutError:
                process.kill()
                await asyncio.to_thread(process.wait)
