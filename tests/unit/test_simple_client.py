"""簡化入口、無秘密偏好與部署界線；所有憑證均位於隔離目錄。"""

import asyncio
import io
import json
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
import zipfile

from uart_gateway.cli import main
from uart_gateway.client_preferences import load_selection, preference_path, save_selection
from uart_gateway.remote_install import RemoteTarget, build_client, build_python_client, install_payload, load_target, remote_command, save_target, ssh_arguments
from uart_gateway.remote_setup import GatewayPortUnavailable, check_idle, run_remote_entry, setup_remote, update_devices
from uart_gateway.remote_workbench import run_remote_workbench
from tests.unit import test_credentials as credential_fixture
from tests.unit.test_remote_workbench import channel


class SimpleClientTests(unittest.TestCase):
    def test_no_subcommand_routes_to_client_and_preserves_global_options(self):
        with patch("sys.argv", ["uart", "--connection", "company", "--access", "manage"]), \
                patch("uart_gateway.client_entry.run_client", return_value=0) as entry:
            with self.assertRaises(SystemExit) as result:
                main()
        self.assertEqual(result.exception.code, 0)
        args = entry.call_args.args[0]
        self.assertEqual((args.command, args.connection, args.access, args.text), ("client", "company", "manage", False))

    def test_noninteractive_default_exits_without_loading_credentials(self):
        with patch("sys.argv", ["uart"]), patch("sys.stdin.isatty", return_value=False), \
                patch("uart_gateway.cli.client_connection") as resolve, patch("sys.stderr", new_callable=io.StringIO) as output:
            with self.assertRaises(SystemExit) as result:
                main()
        self.assertEqual(result.exception.code, 1)
        self.assertIn("uart channels", output.getvalue())
        resolve.assert_not_called()

    def test_fixed_connection_without_tui_uses_text_without_auth_fallback(self):
        from uart_gateway.client_entry import run_client
        settings = SimpleNamespace(name="company", host="127.0.0.1", port=28081, access="read", deployment_id=None, connect=AsyncMock())
        args = SimpleNamespace(text=False, connections_dir=None)
        with patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True), \
                patch("uart_gateway.cli.client_connection", return_value=settings), \
                patch("uart_gateway.client_entry.importlib.util.find_spec", return_value=None), \
                patch("uart_gateway.client_entry.run_remote_workbench", return_value=0) as menu, patch("sys.stderr", io.StringIO()):
            self.assertEqual(run_client(args, Mock()), 0)
        self.assertIs(menu.call_args.args[0], settings.connect)
        self.assertEqual(menu.call_args.kwargs["port"], 28081)

    def test_channel_preferences_are_deployment_and_role_scoped_and_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = SimpleNamespace(deployment_id="a" * 32, access="read")
            path = preference_path(root, settings)
            save_selection(path, ["b", "a"])
            self.assertEqual(load_selection(path), ["b", "a"])
            self.assertNotEqual(path, preference_path(root, SimpleNamespace(deployment_id="a" * 32, access="manage")))
            self.assertNotEqual(path, preference_path(root, SimpleNamespace(deployment_id="b" * 32, access="read")))
            before = path.read_bytes()
            for bad in (["a", "a"], ["a"] * 5, ["../secret"], [], ["\x1b"]):
                with self.assertRaises(ValueError):
                    save_selection(path, bad)
                self.assertEqual(path.read_bytes(), before)
            path.write_text("broken")
            with self.assertRaises(ValueError):
                save_selection(path, ["a"])
            self.assertEqual(path.read_text(), "broken")

    def test_text_enter_reselects_current_snapshot_and_cancel_never_saves(self):
        for state, answers, expected in (("ready", ["", "", "q"], 1), ("lost", ["", "q"], 0), ("ready", ["1", "q", "q"], 0)):
            saved, monitor = Mock(), AsyncMock(return_value=0)
            current = dict(channel(state), stream_epoch="new-epoch", label="本次實際目標")
            with patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True), \
                    patch("uart_gateway.remote_workbench.query_channels", new=AsyncMock(return_value=("new-instance", [current]))), \
                    patch("uart_gateway.remote_workbench.monitor_channel", monitor):
                run_remote_workbench(AsyncMock(), AsyncMock(), host="127.0.0.1", port=18081,
                    read=Mock(side_effect=answers), report=Mock(), preferred_channels=["a"], selection_saved=saved)
            self.assertEqual(saved.call_count, expected)
            self.assertEqual(monitor.await_count, expected)
            if expected:
                self.assertEqual(monitor.call_args.args[2:4], (current, "new-instance"))

    def test_client_zip_is_deterministic_runs_without_venv_and_excludes_backups(self):
        archive = build_python_client()
        self.assertEqual(archive, build_python_client())
        with zipfile.ZipFile(io.BytesIO(archive)) as package:
            self.assertFalse(any(name.endswith(".bak") or ".secret" in name for name in package.namelist()))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "client.pyz"
            path.write_bytes(archive)
            result = subprocess.run([sys.executable, str(path), "--help"], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"channels", result.stdout)


class RemoteSetupTests(unittest.TestCase):
    def setUp(self):
        self.fixture = credential_fixture.CredentialTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_target_rejects_options_shell_injection_and_implicit_roles(self):
        for kwargs in ({"ssh": "-oProxyCommand=oops"}, {"ssh": "user@host;touch x"},
                       {"container": "x;bad"}, {"roles": ("tx",)}, {"port": True}, {"python": "-c"}):
            with self.assertRaises(ValueError):
                RemoteTarget(**dict({"ssh": "user@host"}, **kwargs))
        target = RemoteTarget("user@host", container="dxan", python="/home/user/Python 3/bin/python3")
        command = remote_command(target)
        self.assertIn("podman exec -i dxan", command)
        self.assertIn("NetworkMode", command)
        args = ssh_arguments(target, forward=18081)
        self.assertIn("StrictHostKeyChecking=yes", args)
        self.assertIn("127.0.0.1:18081:127.0.0.1:18081", args)

    def test_install_payload_reuses_exact_selected_role_and_target_has_no_secrets(self):
        fixture = self.fixture
        target = RemoteTarget("user@host", port=28081, client="python")
        raw = install_payload(target, name="default", root=fixture.authority, hold=False)
        payload = json.loads(raw)
        self.assertEqual([b["access"] for b in payload["credentials"]], ["read"])
        secret = (fixture.gateway_config.parent / "read.secret").read_text().strip()
        self.assertEqual(payload["credentials"][0]["token"], secret)
        self.assertNotIn(secret, remote_command(target))
        self.assertNotIn(secret, " ".join(ssh_arguments(target)))
        path = fixture.authority / "remote.json"
        save_target(path, target)
        self.assertEqual(load_target(path), target)
        self.assertNotIn(secret, path.read_text())
        before = fixture.gateway_config.read_bytes()
        install_payload(target, name="default", root=fixture.authority, hold=True)
        self.assertEqual(fixture.gateway_config.read_bytes(), before)

    def test_native_payload_is_bounded_binary_plus_private_metadata_and_extended_timeout(self):
        target = RemoteTarget("user@host")
        binary = b"\x7fELF\n\x00\xff" * 10
        with patch("uart_gateway.remote_install.build_client", return_value=binary):
            raw = install_payload(target, name="default", root=self.fixture.authority, hold=True)
        header, remainder = raw.split(b"\n", 1)
        magic, length, digest = header.split()
        import hashlib
        self.assertEqual((magic, int(length), digest.decode()), (b"UART_NATIVE_V1", len(binary), hashlib.sha256(binary).hexdigest()))
        self.assertEqual(remainder[:len(binary)], binary)
        metadata = json.loads(remainder[len(binary):])
        self.assertEqual((metadata["hold"], metadata["name"]), (True, "company"))
        self.assertEqual([b["access"] for b in metadata["credentials"]], ["read"])
        self.assertIn("ConnectTimeout=180", ssh_arguments(target))
        self.assertNotIn("python3", remote_command(target))
        path = self.fixture.authority / "native-remote.json"
        save_target(path, target)
        old = json.loads(path.read_bytes()); old.pop("client"); old.pop("connect_timeout")
        path.write_text(json.dumps(old))
        self.assertEqual(load_target(path), target)

    def test_binary_preflight_rejects_dynamic_elf_and_wrong_architecture(self):
        binary = bytearray(120); binary[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<HH", binary, 16, 2, 62)
        struct.pack_into("<Q", binary, 32, 64)
        struct.pack_into("<HH", binary, 54, 56, 1)
        struct.pack_into("<I", binary, 64, 1)
        path = self.fixture.root / "native-elf"
        with patch.dict("os.environ", {"UART_GATEWAY_CLIENT_BINARY": str(path)}):
            path.write_bytes(binary); self.assertEqual(build_client(), binary)
            struct.pack_into("<I", binary, 64, 3); path.write_bytes(binary)
            with self.assertRaisesRegex(ValueError, "動態"):
                build_client()
            struct.pack_into("<H", binary, 18, 183); path.write_bytes(binary)
            with self.assertRaisesRegex(ValueError, "amd64"):
                build_client()

    def test_device_change_and_added_uart_keep_tokens_deployment_and_tx_boundary(self):
        from uart_gateway.local_profile import LocalChannel
        from uart_gateway.serial_backend import AdapterSelector
        fixture = self.fixture
        before = {p.name: p.read_bytes() for p in fixture.gateway_config.parent.iterdir() if p.name.endswith(".secret") or p.name == "client.json"}
        selected = (LocalChannel("ch_uart_1", "新的板子", AdapterSelector(port="COM8")),
                    LocalChannel("ch_uart_2", "第二路", AdapterSelector(port="COM9")))
        update_devices(fixture.authority, "default", selected)
        changed = toml(fixture.gateway_config)
        self.assertEqual([c["adapter"]["port"] for c in changed["channels"]], ["COM8", "COM9"])
        self.assertTrue(all(p["channels"] == ["ch_uart_1", "ch_uart_2"] for p in changed["auth"]["principals"]))
        self.assertEqual(before, {p.name: p.read_bytes() for p in fixture.gateway_config.parent.iterdir() if p.name in before})

    def test_setup_existing_connection_cancel_and_retry_do_not_regenerate_secrets(self):
        fixture = self.fixture
        before = {p.name: p.read_bytes() for p in fixture.gateway_config.parent.iterdir()}
        answers = ["user@host", "-", "R", "22", "company", "18081", ""]
        with patch("uart_gateway.remote_setup.ask", side_effect=answers), \
                patch("uart_gateway.remote_setup.check_idle", side_effect=TimeoutError("timed out")) as idle, \
                patch("uart_gateway.remote_setup.trust_target"), patch("uart_gateway.remote_setup.install_remote", new=AsyncMock(side_effect=ValueError("測試失敗"))), \
                patch("uart_gateway.remote_setup.initialize") as initialize, patch("sys.stdout", io.StringIO()):
            with self.assertRaisesRegex(ValueError, "測試失敗"):
                setup_remote(fixture.authority, "default")
        initialize.assert_not_called()
        idle.assert_not_called()
        self.assertEqual(before, {p.name: p.read_bytes() for p in fixture.gateway_config.parent.iterdir() if p.name in before})
        self.assertEqual(load_target(fixture.gateway_config.parent / "remote.json").ssh, "user@host")

    def test_first_setup_defaults_to_read_and_cancel_never_initializes(self):
        from uart_gateway.local_profile import LocalChannel
        from uart_gateway.serial_backend import AdapterSelector
        selected = (LocalChannel("ch_uart_1", "第一路", AdapterSelector(port="COM8")),)
        root = self.fixture.root / "fresh"
        answers = ["1", "user@host", "-", "R", "22", "company", "18081", ""]
        with patch("uart_gateway.remote_setup.ask", side_effect=answers), \
                patch("uart_gateway.remote_setup.select_devices", return_value=selected), \
                patch("uart_gateway.remote_setup.trust_target"), \
                patch("uart_gateway.remote_setup.install_remote", new=AsyncMock(return_value=True)), patch("sys.stdout", io.StringIO()):
            self.assertEqual(setup_remote(root, "default"), 0)
        from uart_gateway.client.connection import read_profile
        self.assertEqual(set(read_profile(root, "default")["credentials"]), {"read", "manage"})
        target = load_target(root / "connections/default/remote.json")
        self.assertEqual(target.roles, ("read",))
        cancelled = self.fixture.root / "cancelled"
        with patch("uart_gateway.remote_setup.ask", side_effect=["1", EOFError()]), \
                patch("uart_gateway.remote_setup.select_devices", return_value=selected), \
                patch("uart_gateway.remote_setup.trust_target") as trust:
            with self.assertRaises(EOFError):
                setup_remote(cancelled, "default")
        trust.assert_not_called()
        self.assertFalse(cancelled.exists())

    def test_new_channel_does_not_expand_tx_and_removing_tx_channel_is_rejected(self):
        from uart_gateway.auth_setup import initialize
        from uart_gateway.local_launcher import _gateway_profile
        from uart_gateway.local_profile import LocalChannel
        from uart_gateway.serial_backend import AdapterSelector
        selected = (LocalChannel("ch_uart_1", "第一路", AdapterSelector(port="COM8")),
                    LocalChannel("ch_uart_2", "第二路", AdapterSelector(port="COM9")))
        root = self.fixture.root / "tx-scope"
        path = initialize(self.fixture.source, directory=root, enable_tx=True, tx_channels=["ch_uart_1"])
        token = path.with_name("tx.secret").read_bytes()
        update_devices(root, "default", selected)
        self.assertEqual(toml(path)["auth"]["principals"][-1]["channels"], ["ch_uart_1"])
        self.assertEqual(path.with_name("tx.secret").read_bytes(), token)
        source = self.fixture.root / "dual.toml"
        source.write_text(_gateway_profile(selected, 18081), encoding="utf-8")
        other = self.fixture.root / "tx-second"
        path = initialize(source, directory=other, enable_tx=True, tx_channels=["ch_uart_2"])
        original = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "TX 授權"):
            update_devices(other, "default", selected[:1])
        self.assertEqual(path.read_bytes(), original)

    def test_setup_with_an_existing_gateway_does_not_probe_tcp_or_change_local_configuration(self):
        fixture = self.fixture
        before = {p.name: p.read_bytes() for p in fixture.gateway_config.parent.iterdir()}
        answers = ["user@host", "-", "R", "22", "company", "18081", ""]
        with patch("uart_gateway.remote_setup.ask", side_effect=answers), \
                patch("socket.create_connection", side_effect=TimeoutError("timed out")) as connect, \
                patch("uart_gateway.remote_setup.check_idle", side_effect=TimeoutError("timed out")) as idle, \
                patch("uart_gateway.remote_setup.trust_target"), \
                patch("uart_gateway.remote_setup.install_remote", new=AsyncMock(return_value=True)) as install, \
                patch("sys.stdout", io.StringIO()):
            self.assertEqual(setup_remote(fixture.authority, "default"), 0)
        connect.assert_not_called()
        idle.assert_not_called()
        install.assert_awaited_once()
        self.assertEqual(before, {p.name: p.read_bytes() for p in fixture.gateway_config.parent.iterdir() if p.name in before})

    def test_port_probe_distinguishes_busy_and_free_without_connecting_and_releases_socket(self):
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            with patch("socket.create_connection", side_effect=TimeoutError("timed out")) as connect:
                with self.assertRaisesRegex(GatewayPortUnavailable, str(port)):
                    check_idle({"network": {"port": port}})
            connect.assert_not_called()
        # 從未建立 TCP Session，關閉測試 Listener 後可立即綁定同一 Port。
        check_idle({"network": {"port": port}})
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", port))

    def test_port_probe_reports_unexpected_socket_error_without_claiming_idle(self):
        import errno
        with patch("socket.socket") as factory:
            factory.return_value.__enter__.return_value.bind.side_effect = OSError(errno.ENETDOWN, "network down")
            with self.assertRaisesRegex(GatewayPortUnavailable, "尚未啟動 Gateway"):
                check_idle({"network": {"port": 18081}})
        factory.return_value.__exit__.assert_called_once()

    def test_remote_menu_returns_after_preflight_failure_and_can_enter_setup(self):
        args = SimpleNamespace(command="remote", connections_dir=self.fixture.authority, connection="default",
                               access=None, token_file=None, token_env=None, host_explicit=False, port_explicit=False)
        with patch("sys.platform", "win32"), patch("sys.stdin.isatty", return_value=True), \
                patch("sys.stdout.isatty", return_value=True), patch("builtins.print") as messages, \
                patch("uart_gateway.remote_setup.load_target", return_value=RemoteTarget("user@host")), \
                patch("uart_gateway.remote_setup.ask", side_effect=["1", "2", EOFError()]), \
                patch("uart_gateway.remote_setup.start_remote", side_effect=GatewayPortUnavailable("本機 Port 被使用")) as start, \
                patch("uart_gateway.remote_setup.setup_remote", return_value=0) as setup:
            self.assertEqual(run_remote_entry(args), 0)
        start.assert_called_once()
        setup.assert_called_once_with(self.fixture.authority, "default")
        self.assertTrue(any("本機 Port 被使用" in str(call) for call in messages.call_args_list))

    def test_remote_menu_does_not_hide_gateway_cleanup_failure(self):
        from uart_gateway.local_launcher import LauncherCleanupError
        args = SimpleNamespace(command="remote", connections_dir=self.fixture.authority, connection="default",
                               access=None, token_file=None, token_env=None, host_explicit=False, port_explicit=False)
        with patch("sys.platform", "win32"), patch("sys.stdin.isatty", return_value=True), \
                patch("sys.stdout.isatty", return_value=True), patch("builtins.print"), \
                patch("uart_gateway.remote_setup.load_target", return_value=RemoteTarget("user@host")), \
                patch("uart_gateway.remote_setup.ask", return_value="1"), \
                patch("uart_gateway.remote_setup.start_remote", side_effect=LauncherCleanupError("清理不明")):
            with self.assertRaisesRegex(LauncherCleanupError, "清理不明"):
                run_remote_entry(args)


class RemoteTransportTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, code, *, cancel=False):
        from uart_gateway.remote_install import open_remote
        processes = []
        spawn = asyncio.create_subprocess_exec

        async def local(*args, **kwargs):
            process = await spawn(sys.executable, "-c", code, **kwargs)
            processes.append(process)
            return process

        with patch("uart_gateway.remote_install.asyncio.create_subprocess_exec", local):
            task = asyncio.create_task(open_remote(RemoteTarget("host.invalid"), b"{}\n"))
            if cancel:
                from tests.fakes import eventually
                await eventually(lambda: bool(processes))
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            else:
                with self.assertRaises(ValueError):
                    await task
        self.assertTrue(processes)
        self.assertTrue(all(p.returncode is not None for p in processes))

    async def test_oversized_control_output_is_bounded_and_child_stops(self):
        await self.exercise("import sys; sys.stdin.buffer.readline(); sys.stdout.write('x'*70000); sys.stdout.flush()")

    async def test_cancel_before_ready_reaps_owned_ssh_process(self):
        await self.exercise("import sys,time; sys.stdin.buffer.readline(); time.sleep(30)", cancel=True)


def toml(path):
    import tomllib
    return tomllib.loads(path.read_text(encoding="utf-8"))
