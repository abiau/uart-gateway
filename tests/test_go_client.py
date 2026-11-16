"""Go CLI against the unchanged Python TCP server and fake UART.
Build dist/uart-client-windows-amd64.exe first, or set UART_CLIENT_BIN.
"""
import json
import os
from pathlib import Path
import subprocess
import unittest
import test_gateway as fixtures

BIN = Path(os.environ.get("UART_CLIENT_BIN", "dist/uart-client-windows-amd64.exe" if os.name == "nt" else "dist/uart-client-linux-amd64")).resolve()

@unittest.skipUnless(BIN.is_file(), "Build the Go client first")
class GoClientTests(unittest.TestCase):
    setUp = fixtures.GatewayTests.setUp
    tearDown = fixtures.GatewayTests.tearDown
    # Reuse fixture lifecycle only; avoid running inherited Python client tests.
    def cli(self, *args):
        return subprocess.run([str(BIN), "--port", str(self.address[1]), *args], capture_output=True, timeout=8)

    def test_go_status(self):
        r = self.cli("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("shell", json.loads(r.stdout))

    def test_go_exec_cwd(self):
        self.serial.auto_reply = lambda cid: self.serial.incoming.put(fixtures.framed(cid, b"/tmp\n", 7))
        r = self.cli("exec", "pwd", "--cwd", "/tmp", "--json")
        self.assertEqual(r.returncode, 1, r.stderr)
        result = json.loads(r.stdout)
        self.assertEqual(result["exit_code"], 7)
        self.assertEqual(result["cwd"], "/tmp")
        self.assertEqual(len(self.serial.writes), 1)

    def test_go_interrupt_and_ready(self):
        r = self.cli("interrupt")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.serial.writes, [b"\x03"])
        r = self.cli("terminal", "--shell-ready")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.serial.writes, [b"\x03"])

    def test_go_terminal_rejects_pipe(self):
        r = self.cli("terminal")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.serial.writes, [])
