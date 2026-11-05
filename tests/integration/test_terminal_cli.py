"""真 CLI 與 POSIX 主控台端點；不代表實體 COM 或 Windows 顯示驗證。"""

import asyncio
import os
from pathlib import Path
import secrets
import sys
import tempfile
import unittest

from uart_gateway.core.models import ChannelConfig
from uart_gateway.fake_serial import FakeSerial
from uart_gateway.server.auth import Principal
from uart_gateway.server.tcp import Gateway
from uart_gateway.text_renderer import RESET


@unittest.skipUnless(os.name == "posix", "需要 POSIX 主控台 PTY；Windows 顯示另行驗證")
class TerminalCliTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.token = secrets.token_urlsafe(32)
        self.gateway = Gateway([(ChannelConfig("a", "test"), FakeSerial())],
            (Principal("reader", self.token, {"a": frozenset({"read"})}),))
        self.port = await self.gateway.start(port=0)
        self.temporary = tempfile.TemporaryDirectory()
        self.secret = Path(self.temporary.name) / "token"
        self.secret.write_text(self.token)
        self.process = None

    async def asyncTearDown(self):
        if self.process is not None and self.process.returncode is None:
            self.process.kill()
            await self.process.wait()
        await self.gateway.close()
        self.temporary.cleanup()

    def command(self):
        return [sys.executable, "-m", "uart_gateway.cli", "--port", str(self.port),
                "--token-file", str(self.secret), "watch", "a", "--terminal", "--replay", "--duration", "0.3"]

    async def test_real_cli_in_tty_renders_replay_controls_and_restores_color(self):
        import tty

        payload = b"boot\r\nloading |\b/\b-\b\\\b\r\n\x1b[0;32mMCU ready\x1b[0m\r\n"
        source = payload + b"\x1b]52;c;hidden\x07\x1b[6n"
        for offset in range(0, len(source), 3):
            self.gateway.channels["a"].ingest(source[offset:offset + 3])
        master, slave = os.openpty()
        tty.setraw(slave)
        os.set_blocking(master, False)
        try:
            self.process = await asyncio.create_subprocess_exec(*self.command(), stdout=slave,
                stdin=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            _, stderr = await asyncio.wait_for(self.process.communicate(), 5)
            received = bytearray()
            while True:
                try:
                    received.extend(os.read(master, 65536))
                except BlockingIOError:
                    break
            self.assertEqual(self.process.returncode, 0, stderr.decode())
            self.assertEqual(bytes(received).replace(RESET.encode(), b""), payload.replace(RESET.encode(), b""))
            self.assertTrue(received.endswith(RESET.encode()))
            self.assertIn("歷史重播", stderr.decode())
        finally:
            os.close(master)
            os.close(slave)

    async def test_real_cli_rejects_redirected_terminal_mode(self):
        self.process = await asyncio.create_subprocess_exec(*self.command(),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(self.process.communicate(), 5)
        self.assertEqual(self.process.returncode, 1)
        self.assertFalse(stdout)
        self.assertIn("互動式終端機", stderr.decode())
        self.assertFalse(self.gateway.sessions)
