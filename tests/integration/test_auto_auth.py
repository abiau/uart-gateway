"""真 Gateway／TCP／SDK 的自動認證；UART 使用 Fake，不宣稱 Win11／SSH 驗證。"""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from uart_gateway.auth_setup import import_credential
from uart_gateway.client import Client, resolve_connection
from uart_gateway.fake_serial import FakeSerial
from uart_gateway.protocol.codec import binary_decode
from uart_gateway.protocol.errors import RemoteError
from uart_gateway.protected_files import read_private
from uart_gateway.server.app import load
from tests.fakes import eventually
from tests.unit import test_credentials as credential_fixture
from tests.integration import test_workbench as console_fixture


class AutoAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = credential_fixture.CredentialTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.client
        await self.start_gateway(0)
        for role in ("read", "manage"):
            import_credential(read_private(self.fixture.export(role), 16384), directory=self.root, port=self.port)

    async def start_gateway(self, port):
        self.gateway, _, _ = load(self.fixture.gateway_config, fake=False)
        self.device = FakeSerial(echo=False)
        self.gateway.channels["ch_uart_1"].port = self.device
        self.port = await self.gateway.start(port=port)

    async def asyncTearDown(self):
        await self.gateway.close()

    async def test_reader_receives_but_cannot_configure_confirm_or_acquire_tx(self):
        async with await Client.connect_auto(directory=self.root) as client:
            channel = await client.get_channel("ch_uart_1")
            self.assertEqual(client.auth["grants"][0]["permissions"], ["read"])
            await client.subscribe("ch_uart_1")
            self.device.inject(b"automatic-auth RX\n")
            async with asyncio.timeout(3):
                while (event := await client.next_event())["event"] != "rx":
                    pass
            self.assertEqual(binary_decode(event["data"]["data_b64"]), b"automatic-auth RX\n")
            for operation in (lambda: client.reopen_rx(channel), lambda: client.confirm(channel, "test"),
                              lambda: client.acquire(channel)):
                with self.assertRaises(RemoteError) as caught:
                    await operation()
                self.assertEqual(caught.exception.code, "PERMISSION_DENIED")
        self.assertEqual(self.device.write_count, 0)
        self.assertFalse(self.gateway.channels["ch_uart_1"].confirmed)

    async def test_explicit_manager_reopens_rx_without_tx_or_lease(self):
        self.device.close()
        await eventually(lambda: self.gateway.channels["ch_uart_1"].state == "lost")
        async with await Client.connect_auto(directory=self.root, access="manage") as client:
            snapshot = await client.get_channel("ch_uart_1")
            result = await client.reopen_rx(snapshot)
            self.assertEqual(result["state"], "ready")
            self.assertEqual(result["binding"]["status"], "unconfirmed")
            with self.assertRaises(RemoteError) as caught:
                await client.acquire(result)
            self.assertEqual(caught.exception.code, "PERMISSION_DENIED")
        self.assertEqual(self.device.write_count, 0)
        self.assertIsNone(self.gateway.channels["ch_uart_1"].lease.owner)

    async def test_wrong_or_mislabelled_credential_never_falls_back_or_returns_a_privileged_client(self):
        path = self.root / "connections/default/read.secret"
        correct = path.read_bytes()
        path.write_text("w" * 43)
        with patch.dict(os.environ, {"UART_GATEWAY_TOKEN": correct.decode().strip()}):
            with self.assertRaises(RemoteError) as caught:
                await Client.connect_auto(directory=self.root)
            self.assertEqual(caught.exception.code, "AUTH_FAILED")
        path.write_bytes(correct)
        bundle = json.loads(read_private(self.fixture.export("manage"), 16384))
        bundle["access"] = "read"
        other = self.fixture.root / "mislabelled"
        import_credential(json.dumps(bundle).encode(), directory=other, port=self.port)
        with self.assertRaisesRegex(ValueError, "權限.*不符"):
            await Client.connect_auto(directory=other)
        (self.root / "connections/default/manage.secret").write_bytes(correct)
        with self.assertRaisesRegex(ValueError, "權限.*不符"):
            await Client.connect_auto(directory=self.root, access="manage")
        await eventually(lambda: not self.gateway.sessions)
        self.assertEqual(self.device.write_count, 0)

    async def test_restart_keeps_credentials_and_settings_reread_token_on_each_session(self):
        settings = resolve_connection(directory=self.root)
        async with await settings.connect() as client:
            old_instance = client.instance
        await self.gateway.close()
        await self.start_gateway(self.port)
        async with await settings.connect() as client:
            self.assertNotEqual(client.instance, old_instance)
        secret = self.root / "connections/default/read.secret"
        original = secret.read_bytes()
        secret.write_text("x" * 43)
        with self.assertRaises(RemoteError):
            await settings.connect()
        secret.write_bytes(original)
        async with await settings.connect() as client:
            self.assertEqual(client.auth["grants"][0]["permissions"], ["read"])

    async def test_cli_default_profile_and_explicit_management_need_no_token_argument(self):
        env = os.environ.copy()
        env.pop("UART_GATEWAY_TOKEN", None)
        command = [sys.executable, "-m", "uart_gateway.cli", "--connections-dir", str(self.root)]
        result = await asyncio.to_thread(subprocess.run, command + ["channels"], env=env,
                                         text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]["channel_id"], "ch_uart_1")
        self.device.close()
        await eventually(lambda: self.gateway.channels["ch_uart_1"].state == "lost")
        result = await asyncio.to_thread(subprocess.run, command + ["--access", "manage", "reopen", "ch_uart_1"],
                                         env=env, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.device.write_count, 0)


@unittest.skipUnless(os.name == "posix", "需要 POSIX PTY；Win11 操作另行驗證")
class AutoAuthWorkbenchTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = console_fixture.WorkbenchProcessTests.asyncSetUp
    start = console_fixture.WorkbenchProcessTests.start
    expect = console_fixture.WorkbenchProcessTests.expect
    answer = console_fixture.WorkbenchProcessTests.answer
    finish = console_fixture.WorkbenchProcessTests.finish

    async def asyncTearDown(self):
        await console_fixture.WorkbenchProcessTests.asyncTearDown(self)

    async def test_remote_workbench_uses_sdk_default_profile_without_env_or_token_flags(self):
        fixture = credential_fixture.CredentialTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        gateway, _, _ = load(fixture.gateway_config, fake=False)
        device = FakeSerial(echo=False)
        gateway.channels["ch_uart_1"].port = device
        port = await gateway.start(port=0)
        try:
            import_credential(read_private(fixture.export(), 16384), directory=fixture.client, port=port)
            self.start([sys.executable, "-m", "uart_gateway.cli", "--connections-dir", str(fixture.client), "menu", "--remote"])
            await self.expect("模式：讀取")
            await self.expect("輸入 Channel 編號")
            await self.finish()
            self.assertTrue(gateway.listener.is_serving())
            self.assertEqual(device.write_count, 0)
            self.assertNotIn("--token", self.output.decode(errors="replace"))
        finally:
            await gateway.close()
