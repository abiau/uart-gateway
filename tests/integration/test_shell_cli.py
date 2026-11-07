"""真 CLI／固定認證／模擬 Shell；不操作實體 UART 或公司 SSH。"""

import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import unittest

from uart_gateway.auth_setup import initialize
from uart_gateway.client import Client, ShellRunner, ShellTargets, TxJournal
from uart_gateway.protected_files import read_private, write_private
from uart_gateway.server.app import load
from tests.integration.test_shell_exec import FakeShell
from tests.integration import test_workbench as fixture
from tests.fakes import eventually


@unittest.skipUnless(os.name == "posix", "互動 CLI 需要 POSIX PTY；Windows 另列驗證")
class ShellCLITests(unittest.IsolatedAsyncioTestCase):
    start = fixture.WorkbenchProcessTests.start
    expect = fixture.WorkbenchProcessTests.expect
    answer = fixture.WorkbenchProcessTests.answer

    async def asyncSetUp(self):
        await fixture.WorkbenchProcessTests.asyncSetUp(self)
        self.authority = self.root / "authority"
        config = initialize(Path("examples/gateway.com3.toml"), directory=self.authority, enable_tx=True)
        self.gateway, _, _ = load(config, fake=False)
        self.device = FakeShell()
        self.gateway.channels["ch_uart_1"].port = self.device
        port = await self.gateway.start(port=0)
        path = config.parent / "client.json"
        profile = json.loads(read_private(path, 16384))
        profile["port"] = port
        write_private(path, json.dumps(profile).encode(), replace=True)
        async def connect():
            return await Client.connect_auto(directory=self.authority, access="tx")
        self.runner = ShellRunner(connect, TxJournal(self.authority / "tx-receipts" / "default"),
                                  ShellTargets(self.authority / "shell-targets" / "default"))
        self.argv = [sys.executable, "-W", "error::RuntimeWarning", "-m", "uart_gateway.cli",
                     "--connections-dir", str(self.authority)]

    async def asyncTearDown(self):
        await self.gateway.close()
        await fixture.WorkbenchProcessTests.asyncTearDown(self)

    async def cli(self, *args):
        process = await asyncio.create_subprocess_exec(*self.argv, *args, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(process.communicate(), 10)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        return process.returncode, out.decode(), err.decode()

    async def ready(self):
        await self.runner.authorize(await self.runner.inspect("ch_uart_1"), "lf")

    async def test_human_setup_noninteractive_and_cancel_do_not_confirm_or_send(self):
        code, _, error = await self.cli("shell-ready", "ch_uart_1", "--newline", "lf")
        self.assertEqual(code, 1)
        self.assertIn("互動終端", error)
        self.start([*self.argv, "shell-ready", "ch_uart_1", "--newline", "lf"])
        await self.expect("輸入 Y")
        self.answer("n")
        self.assertEqual(await asyncio.wait_for(asyncio.to_thread(self.process.wait), 5), 0)
        self.assertFalse(self.gateway.channels["ch_uart_1"].confirmed)
        self.assertEqual(self.device.write_count, 0)
        code, output, _ = await self.cli("exec", "ch_uart_1", "uname -a")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output)["reason_code"], "SHELL_NOT_READY")

    async def test_human_setup_then_json_exec_query_and_nonzero_exit(self):
        self.start([*self.argv, "shell-ready", "ch_uart_1", "--newline", "lf"])
        await self.expect("輸入 Y")
        self.answer("y")
        self.assertEqual(await asyncio.wait_for(asyncio.to_thread(self.process.wait), 5), 0)
        self.assertTrue(self.gateway.channels["ch_uart_1"].confirmed)
        self.assertEqual(self.device.write_count, 0)
        code, output, error = await self.cli("exec", "ch_uart_1", "uname -a")
        self.assertEqual((code, error), (0, ""))
        value = json.loads(output)
        self.assertEqual((value["state"], value["exit_code"], value["output_text"]), ("completed", 0, "Linux ipcam\r\n"))
        code, output, _ = await self.cli("exec-status", value["execution_id"])
        self.assertEqual((code, json.loads(output)["output_text"]), (0, value["output_text"]))
        self.assertEqual(self.device.write_count, 1)
        self.device.code = 9
        code, output, _ = await self.cli("exec", "ch_uart_1", "exit 9")
        self.assertEqual((code, json.loads(output)["exit_code"]), (1, 9))

    async def test_timeout_json_and_explicit_read_role_never_resend(self):
        await self.ready()
        self.device.mode = "begin"
        code, output, _ = await self.cli("exec", "ch_uart_1", "slow", "--timeout", "0.1")
        value = json.loads(output)
        self.assertEqual((code, value["state"], value["reason_code"]), (2, "unknown", "COMMAND_TIMEOUT"))
        code, output, _ = await self.cli("exec", "ch_uart_1", "must-not-queue")
        self.assertEqual((code, json.loads(output)["state"]), (2, "not_sent"))
        code, output, _ = await self.cli("--access", "read", "exec", "ch_uart_1", "forbidden")
        self.assertEqual((code, json.loads(output)["state"]), (2, "not_sent"))
        self.assertEqual(self.device.write_count, 1)

    async def test_text_mode_escapes_terminal_controls(self):
        await self.ready()
        self.device.body = b"ok\x1b]52;c;bad\x07\n"
        code, output, diagnostic = await self.cli("exec", "ch_uart_1", "printf ok", "--text")
        self.assertEqual(code, 0)
        self.assertNotIn("\x1b", output)
        self.assertIn("\\x1b", output)
        self.assertIn("Shell 狀態", diagnostic)

    async def test_sigint_returns_original_id_and_does_not_send_ctrl_c(self):
        await self.ready()
        self.device.mode = "begin"
        process = await asyncio.create_subprocess_exec(*self.argv, "exec", "ch_uart_1", "slow",
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            await eventually(lambda: self.device.write_count == 1)
            process.send_signal(signal.SIGINT)
            output, error = await asyncio.wait_for(process.communicate(), 5)
            value = json.loads(output)
            self.assertEqual((process.returncode, error), (130, b""))
            self.assertEqual((value["state"], value["reason_code"]), ("unknown", "CANCELLED"))
            self.assertEqual(value["execution_id"], self.device.execution_id)
            self.assertEqual(self.device.write_count, 1)
            self.assertIsNone(self.gateway.channels["ch_uart_1"].lease.owner)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
