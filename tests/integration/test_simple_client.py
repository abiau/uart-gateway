"""Linux 安裝包、真 TCP／SDK／終端與程序收尾；SSH 傳輸以本機程序替代。"""

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch

from uart_gateway.client import Client
from uart_gateway.fake_serial import FakeSerial
from uart_gateway.remote_install import RemoteTarget, install_remote, install_payload, open_remote, stop_ssh
from uart_gateway.remote_setup import serve_remote
from uart_gateway.server.app import load
from tests.fakes import eventually
from tests.unit import test_credentials as credential_fixture
from tests.integration import test_workbench as console_fixture


@unittest.skipUnless(os.name == "posix", "遠端安裝需要 Linux／POSIX；Windows 另測引導與程序邊界")
class RemoteInstallTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = credential_fixture.CredentialTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.home = self.fixture.root / "linux-home"
        self.home.mkdir()
        self.environment = dict(os.environ, HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / ".config"),
                                PATH="/usr/bin:/bin", PYTHONIOENCODING="utf-8")
        self.environment.pop("UART_GATEWAY_TOKEN", None)
        self.gateway, _, _ = load(self.fixture.gateway_config, fake=False)
        self.device = FakeSerial(echo=False)
        self.gateway.channels["ch_uart_1"].port = self.device
        self.port = await self.gateway.start(port=0)
        self.target = RemoteTarget("test@ssh.invalid", port=self.port, client="python")
        self.processes, self.commands = [], []
        self.bootstrap = Path("src/uart_gateway/remote_bootstrap.py").read_text(encoding="utf-8")
        self.real_spawn = asyncio.create_subprocess_exec

    async def asyncTearDown(self):
        for process in self.processes:
            await stop_ssh(process)
        await self.gateway.close()

    async def local_transport(self, *args, **kwargs):
        self.commands.append(args)
        process = await self.real_spawn(sys.executable, "-c", self.bootstrap, env=self.environment, **kwargs)
        self.processes.append(process)
        return process

    async def install(self, target=None):
        with patch("uart_gateway.remote_install.asyncio.create_subprocess_exec", self.local_transport):
            return await install_remote(target or self.target, name="default", root=self.fixture.authority)

    async def cli(self, *args):
        launcher = self.home / ".local/bin/uart"
        process = await asyncio.create_subprocess_exec(str(launcher), *args, env=self.environment,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        async with asyncio.timeout(10):
            stdout, stderr = await process.communicate()
        return process.returncode, stdout, stderr

    async def test_installs_self_contained_client_fixed_auth_and_real_sdk_without_venv(self):
        self.assertFalse(await self.install())
        code, stdout, stderr = await self.cli("channels")
        self.assertEqual(code, 0, stderr.decode())
        self.assertIn(b"ch_uart_1", stdout)
        for role in ("manage", "tx"):
            code, _, stderr = await self.cli("--access", role, "channels")
            self.assertEqual(code, 1)
            self.assertIn("未配置".encode(), stderr)
        secret = (self.home / ".config/uart-gateway/connections/company/read.secret").read_bytes()
        self.assertEqual(secret, (self.fixture.gateway_config.parent / "read.secret").read_bytes())
        self.assertEqual((self.home / ".config/uart-gateway/connections/company/read.secret").stat().st_mode & 0o777, 0o600)
        self.assertFalse(list(self.home.rglob("*.uart-credential")))
        await self.install()
        self.assertEqual(secret, (self.home / ".config/uart-gateway/connections/company/read.secret").read_bytes())
        self.assertNotIn(secret.decode().strip(), str(self.commands))
        self.assertEqual(self.device.write_count, 0)
        await eventually(lambda: not self.gateway.sessions)

    async def test_explicit_manager_import_and_conflict_preserve_original_secret(self):
        await self.install()
        target = RemoteTarget("test@ssh.invalid", port=self.port, roles=("read", "manage"), client="python")
        await self.install(target)
        self.assertEqual((await self.cli("--access", "manage", "channels"))[0], 0)
        path = self.home / ".config/uart-gateway/connections/company/read.secret"
        original = path.read_bytes()
        payload = json.loads(install_payload(self.target, name="default", root=self.fixture.authority, hold=False))
        token = "secret-conflict-" + "x" * 40
        payload["credentials"][0]["token"] = token
        with patch("uart_gateway.remote_install.asyncio.create_subprocess_exec", self.local_transport):
            with self.assertRaises(ValueError) as caught:
                await open_remote(self.target, (json.dumps(payload) + "\n").encode())
        self.assertNotIn(token, str(caught.exception))
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual((await self.cli("channels"))[0], 0)
        self.assertEqual(self.device.write_count, 0)

    async def test_existing_user_launcher_and_bad_hash_are_not_overwritten(self):
        directory = self.home / ".local/bin"
        directory.mkdir(parents=True)
        launcher = directory / "uart"
        launcher.write_text("#!/bin/sh\necho USER-OWNED\n")
        with self.assertRaises(ValueError):
            await self.install()
        self.assertEqual(launcher.read_text(), "#!/bin/sh\necho USER-OWNED\n")
        self.assertFalse((self.home / ".config/uart-gateway").exists())
        payload = json.loads(install_payload(self.target, name="default", root=self.fixture.authority, hold=False))
        payload["sha256"] = "0" * 64
        with patch("uart_gateway.remote_install.asyncio.create_subprocess_exec", self.local_transport):
            with self.assertRaises(ValueError):
                await open_remote(self.target, (json.dumps(payload) + "\n").encode())
        self.assertFalse((self.home / ".config/uart-gateway").exists())

    async def test_hold_ready_requires_real_gateway_auth_and_eof_closes_only_ssh(self):
        payload = install_payload(self.target, name="default", root=self.fixture.authority, hold=True)
        with patch("uart_gateway.remote_install.asyncio.create_subprocess_exec", self.local_transport):
            process, _ = await open_remote(self.target, payload, forward=self.port)
        self.assertIsNone(process.returncode)
        await eventually(lambda: not self.gateway.sessions)
        await stop_ssh(process)
        self.assertEqual(process.returncode, 0)
        self.assertTrue(self.gateway.listener.is_serving())
        self.device.inject(b"still running\n")
        await eventually(lambda: self.gateway.channels["ch_uart_1"].offset > 0)
        self.assertEqual(self.device.write_count, 0)

    async def test_remote_start_disconnect_manual_retry_keep_gateway_epoch_and_cleanup(self):
        await self.gateway.close()
        gateway, _, _ = load(self.fixture.gateway_config, fake=False)
        device = FakeSerial(echo=False)
        gateway.channels["ch_uart_1"].port = device
        self.gateway = gateway
        messages, phase = [], 0
        initial_epoch = None

        def poll():
            nonlocal phase, initial_epoch
            if phase == 0:
                initial_epoch = gateway.channels["ch_uart_1"].epoch
                self.processes[-1].kill()
                phase = 1
            elif phase == 1 and any("SSH 連線已中斷" in m for m in messages):
                device.inject(b"RX-during-disconnect\n")
                phase = 2
                return "r"
            elif phase == 2 and len(self.processes) == 2:
                self.assertEqual(gateway.channels["ch_uart_1"].epoch, initial_epoch)
                self.assertGreater(gateway.channels["ch_uart_1"].offset, 0)
                return "q"
            return None

        with patch("uart_gateway.remote_setup.load_target"), patch("uart_gateway.server.app.load", return_value=(gateway, "127.0.0.1", self.port)), \
                patch("uart_gateway.remote_install.asyncio.create_subprocess_exec", self.local_transport):
            async with asyncio.timeout(15):
                self.assertEqual(await serve_remote(self.fixture.authority, "default", self.target, poll=poll, report=messages.append), 0)
        self.assertEqual(len(self.processes), 2)
        self.assertTrue(all(process.returncode is not None for process in self.processes))
        self.assertEqual(device.write_count, 0)
        self.assertFalse(gateway.sessions)
        self.assertEqual(gateway.channels["ch_uart_1"].state, "closed")


@unittest.skipUnless(os.name == "posix", "需要 POSIX 互動終端")
class SimpleClientConsoleTests(console_fixture.WorkbenchProcessTests):
    async def test_no_subcommand_reads_fixed_profile_remembers_selection_and_preserves_gateway(self):
        fixture = credential_fixture.CredentialTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        gateway, _, _ = load(fixture.gateway_config, fake=False)
        device = FakeSerial(echo=False)
        gateway.channels["ch_uart_1"].port = device
        port = await gateway.start(port=0)
        from uart_gateway.auth_setup import credential_bundle, import_credential
        import_credential(credential_bundle(directory=fixture.authority), directory=fixture.client, port=port)
        try:
            code = """import sys
from unittest.mock import patch
from uart_gateway.cli import main
sys.argv = ['uart', '--connections-dir', sys.argv[1]]
with patch('uart_gateway.client_entry.importlib.util.find_spec', return_value=None):
    main()
"""
            for attempt in range(2):
                self.start([sys.executable, "-c", code, str(fixture.client)])
                await self.expect("ch_uart_1／")
                if attempt:
                    await self.expect("Enter 使用上次選擇 1")
                    self.answer("")
                else:
                    await self.expect("輸入 Channel 編號")
                    self.answer("1")
                await self.expect("L 或 Enter 即時接收")
                self.answer("")
                await eventually(lambda: bool(gateway.channels["ch_uart_1"].subscriptions))
                device.inject(f"simple-client-{attempt}\n".encode())
                await self.expect(f"simple-client-{attempt}")
                self.process.send_signal(signal.SIGINT)
                await self.expect("監看已中止")
                await self.finish()
                await eventually(lambda: not gateway.sessions)
            self.assertEqual(device.write_count, 0)
            self.assertTrue(gateway.listener.is_serving())
        finally:
            await gateway.close()

    # 只重用終端生命週期工具，不重跑工作台本身的既有案例。
    test_browse_check_play_export_with_real_cli_and_no_gateway = None
    test_record_ctrl_c_cleanup_then_return_to_workbench = None


HAS_TUI = importlib.util.find_spec("textual") is not None and importlib.util.find_spec("pyte") is not None


@unittest.skipUnless(HAS_TUI, "需要可選 TUI 依賴")
class ClientPreferenceTUITests(unittest.IsolatedAsyncioTestCase):
    async def test_saved_selection_only_preselects_fresh_picker_and_needs_enter(self):
        from uart_gateway.tui_app import UARTApp
        from uart_gateway.tui_widgets import ChannelPicker
        from textual.widgets import SelectionList
        fixture = credential_fixture.CredentialTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        gateway, _, _ = load(fixture.gateway_config, fake=False)
        device = FakeSerial(echo=False)
        gateway.channels["ch_uart_1"].port = device
        port = await gateway.start(port=0)

        async def connect():
            token = (fixture.gateway_config.parent / "read.secret").read_text().strip()
            return await Client.connect("127.0.0.1", port, token)

        try:
            for preferred in (["ch_uart_1"], ["removed"]):
                saved = AsyncMock()
                app = UARTApp(connect, preferred_channels=preferred, selection_saved=saved)
                async with app.run_test(size=(100, 32)) as pilot:
                    await eventually(lambda: isinstance(app.screen, ChannelPicker))
                    await pilot.pause()
                    self.assertFalse(gateway.channels["ch_uart_1"].subscriptions)
                    self.assertEqual(app.screen.query_one(SelectionList).selected,
                                     ["ch_uart_1"] if preferred == ["ch_uart_1"] else [])
                    await pilot.press("enter")
                    if preferred == ["ch_uart_1"]:
                        await eventually(lambda: bool(gateway.channels["ch_uart_1"].subscriptions))
                        await eventually(lambda: saved.await_count == 1)
                        saved.assert_awaited_once_with(["ch_uart_1"])
                    else:
                        await pilot.pause()
                        self.assertFalse(gateway.channels["ch_uart_1"].subscriptions)
                        saved.assert_not_awaited()
                        await pilot.press("escape")
                    await pilot.press("q")
                await eventually(lambda: not gateway.sessions)
            self.assertEqual(device.write_count, 0)
            self.assertTrue(gateway.listener.is_serving())
        finally:
            await gateway.close()
