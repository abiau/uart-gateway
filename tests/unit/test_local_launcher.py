"""本機啟動器的鎖、認證與 Ready 規則；不存取實體 COM。"""

import asyncio
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import tomllib
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from uart_gateway.local_launcher import (
    LauncherCleanupError, LauncherError, OwnedGateway, PortLock, _profile, process_options, wait_ready,
)
from uart_gateway.serial_backend import AdapterSelector


def ready_channel():
    return {"state": "ready", "adapter": {"port": "COM3"},
            "serial": {"dtr": False, "rts": False, "baudrate": 115200,
                       "bytesize": 8, "parity": "none", "stopbits": 1, "flow_control": "none"}}


def candidate(channel=None):
    return SimpleNamespace(
        auth={"grants": [{"channel_id": "ch_uart_1", "permissions": ["read"]}]},
        get_channel=AsyncMock(return_value=channel or ready_channel()), close=AsyncMock())


class LockAndProcessTests(unittest.TestCase):
    def test_selected_identity_is_preserved_in_existing_config_loader(self):
        from uart_gateway.server.app import load

        selector = AdapterSelector(port="COM8", vid=1027, pid=24577,
                                   serial_number='id"\\末', location="usb-1")
        profile = _profile("COM8", 18081, selector)
        parsed = tomllib.loads(profile)
        self.assertEqual(parsed["channels"][0]["adapter"], vars(selector))
        self.assertEqual(parsed["auth"]["principals"][0]["permissions"], ["read"])
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"UART_GATEWAY_TOKEN": secrets.token_urlsafe(32)}):
            path = Path(directory) / "gateway.toml"
            path.write_text(profile)
            gateway, _, _ = load(path, fake=False)
        self.assertEqual(gateway.channels["ch_uart_1"].port.selector, selector)
        self.assertFalse(gateway.channels["ch_uart_1"].port.settings.dtr)
        self.assertFalse(gateway.channels["ch_uart_1"].port.settings.rts)

    def test_lock_blocks_duplicate_alias_in_another_process_then_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with PortLock("COM3", root):
                code = """from pathlib import Path
import sys
from uart_gateway.local_launcher import PortLock,LauncherError
try:
    with PortLock('com03',Path(sys.argv[1])):
        raise SystemExit(3)
except LauncherError:
    pass
"""
                result = subprocess.run([sys.executable, "-c", code, str(root)],
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
            with PortLock("\\\\.\\COM3", root):
                pass
            self.assertEqual(len(list(root.iterdir())), 1)

    def test_profile_and_argv_never_contain_token_and_parent_env_unchanged(self):
        token = secrets.token_urlsafe(32)
        parent_token = "parent_value_must_not_change"
        profile = _profile("COM3", 18081)
        config = tomllib.loads(profile)
        self.assertNotIn(token, profile)
        self.assertEqual(config["auth"]["principals"][0]["permissions"], ["read"])
        self.assertFalse(config["channels"][0]["serial"]["dtr"])
        self.assertFalse(config["channels"][0]["serial"]["rts"])
        with patch.dict(os.environ, {"UART_GATEWAY_TOKEN": parent_token}), \
                patch("uart_gateway.local_launcher.subprocess.Popen") as popen, \
                patch("uart_gateway.local_launcher.threading.Thread"):
            owned = OwnedGateway(Path("path with spaces/gateway.toml"), token, reporter=lambda _: None)
            owned.start()
            args, kwargs = popen.call_args
            self.assertNotIn(token, repr(args))
            self.assertEqual(kwargs["env"]["UART_GATEWAY_TOKEN"], token)
            self.assertEqual(os.environ["UART_GATEWAY_TOKEN"], parent_token)
            self.assertEqual(kwargs["stdin"], subprocess.PIPE)
            self.assertNotIn("shell", kwargs)
        self.assertEqual(process_options("nt"), {"creationflags": 0x200})
        self.assertEqual(process_options("posix"), {"start_new_session": True})

    def test_shutdown_closes_own_pipe_before_owned_process_fallback(self):
        owned = OwnedGateway(Path("unused"), "secret", reporter=lambda _: None)
        process = Mock()
        process.wait.side_effect = [subprocess.TimeoutExpired("owned", 12),
                                    subprocess.TimeoutExpired("owned", 2), 0]
        owned.process = process
        owned.stop()
        process.stdin.close.assert_called_once()
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 3)

    def test_child_log_redacts_token(self):
        import io
        logs = []
        owned = OwnedGateway(Path("unused"), "sensitive_value", reporter=logs.append)
        owned.process = SimpleNamespace(stdout=io.StringIO("訊息 sensitive_value\n"))
        owned._read_logs()
        self.assertNotIn("sensitive_value", repr(logs))
        self.assertIn("已隱藏憑證", logs[0])


class ReadyTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_child_cleanup_has_a_distinct_fatal_error(self):
        from uart_gateway.local_launcher import launch_local

        owned = Mock()
        owned.stop.side_effect = LauncherError("子程序未結束")
        with patch("uart_gateway.local_launcher._check_listener"), \
                patch("uart_gateway.local_launcher.OwnedGateway", return_value=owned), \
                patch("uart_gateway.local_launcher.wait_ready", new=AsyncMock(side_effect=LauncherError("啟動失敗"))):
            with self.assertRaisesRegex(LauncherCleanupError, "無法確認本次 Gateway 已清理"):
                await launch_local("COM3", AsyncMock(), reporter=lambda _: None)
        owned.stop.assert_called_once()
    async def test_ready_accepts_selected_identity_and_normalized_port(self):
        selector = AdapterSelector(port="com03", vid=1027, pid=24577,
                                   serial_number="expected", location="usb-1")
        client = candidate()
        client.get_channel.return_value["adapter"].update(vars(selector))
        with patch("uart_gateway.local_launcher.Client.connect", new=AsyncMock(return_value=client)):
            self.assertIs(await wait_ready(self.owned(), "COM3", 18081, 1, selector), client)
        client.close.assert_not_awaited()

    async def test_ready_rejects_changed_selected_identity(self):
        selector = AdapterSelector(port="COM3", vid=1027, pid=24577,
                                   serial_number="expected", location="usb-1")
        for field in ("vid", "pid", "serial_number", "location"):
            client = candidate()
            client.get_channel.return_value["adapter"].update(vars(selector))
            client.get_channel.return_value["adapter"][field] = None
            with self.subTest(field=field), patch("uart_gateway.local_launcher.Client.connect", new=AsyncMock(return_value=client)):
                with self.assertRaisesRegex(LauncherError, "裝置資訊"):
                    await wait_ready(self.owned(), "COM3", 18081, 1, selector)
            client.close.assert_awaited_once()

    async def test_mismatched_selected_port_starts_no_process(self):
        from uart_gateway.local_launcher import launch_local

        with patch("uart_gateway.local_launcher.OwnedGateway.start") as start:
            with self.assertRaisesRegex(LauncherError, "端點不一致"):
                await launch_local("COM3", AsyncMock(), selector=AdapterSelector(port="COM8"))
        start.assert_not_called()

    def owned(self, exited=False):
        return SimpleNamespace(process=SimpleNamespace(poll=lambda: 1 if exited else None), token="unused")

    async def test_retries_until_channel_ready_instead_of_sleep_success(self):
        opening = candidate({"state": "opening"})
        ready = candidate()
        with patch("uart_gateway.local_launcher.Client.connect", new=AsyncMock(side_effect=[opening, ready])) as connect:
            result = await wait_ready(self.owned(), "COM3", 18081, 1)
        self.assertIs(result, ready)
        opening.close.assert_awaited_once()
        ready.close.assert_not_awaited()
        self.assertEqual(connect.await_count, 2)

    async def test_busy_channel_fails_with_mobaxterm_hint_and_closes_sdk(self):
        busy = candidate({"state": "faulted", "last_error": {"code": "PORT_BUSY"}})
        with patch("uart_gateway.local_launcher.Client.connect", new=AsyncMock(return_value=busy)):
            with self.assertRaisesRegex(LauncherError, "MobaXterm"):
                await wait_ready(self.owned(), "COM3", 18081, 1)
        busy.close.assert_awaited_once()

    async def test_ready_with_wrong_port_or_grant_is_not_accepted(self):
        for issue in ("port", "grant", "dtr"):
            client = candidate()
            if issue == "port":
                client.get_channel.return_value["adapter"]["port"] = "COM4"
            elif issue == "grant":
                client.auth["grants"][0]["permissions"].append("tx")
            else:
                client.get_channel.return_value["serial"]["dtr"] = True
            with self.subTest(issue=issue), patch("uart_gateway.local_launcher.Client.connect", new=AsyncMock(return_value=client)):
                with self.assertRaises(LauncherError):
                    await wait_ready(self.owned(), "COM3", 18081, 1)
            client.close.assert_awaited_once()

    async def test_timeout_and_early_exit_are_failures(self):
        with patch("uart_gateway.local_launcher.Client.connect", new=AsyncMock(side_effect=ConnectionRefusedError)):
            with self.assertRaisesRegex(LauncherError, "逾時"):
                await wait_ready(self.owned(), "COM3", 18081, 0.02)
        with patch("uart_gateway.local_launcher.Client.connect", new=AsyncMock()) as connect:
            with self.assertRaisesRegex(LauncherError, "就緒前已結束"):
                await wait_ready(self.owned(exited=True), "COM3", 18081, 1)
            connect.assert_not_awaited()

    async def test_cancellation_during_query_closes_sdk(self):
        client = candidate()
        entered = asyncio.Event()

        async def blocked(*_):
            entered.set()
            await asyncio.Event().wait()

        client.get_channel.side_effect = blocked
        with patch("uart_gateway.local_launcher.Client.connect", new=AsyncMock(return_value=client)):
            task = asyncio.create_task(wait_ready(self.owned(), "COM3", 18081, 10))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        client.close.assert_awaited_once()
