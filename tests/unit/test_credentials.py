"""受保護憑證來源與一次性部署；使用測試暫存目錄，不配置使用者真實秘密。"""

import json
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from uart_gateway.auth_setup import initialize, export_credential, import_credential, select_default
from uart_gateway.client import ConnectionSettings, resolve_connection
from uart_gateway.client.connection import read_profile
from uart_gateway.client.credentials import TokenSource, explicit_source
from uart_gateway.protected_files import read_private, write_private, windows_acl
from uart_gateway.server.app import load


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.authority = self.root / "authority"
        self.client = self.root / "client"
        self.source = self.root / "source.toml"
        self.source.write_bytes(Path("examples/gateway.com3.rx-reopen.toml").read_bytes())
        self.gateway_config = initialize(self.source, directory=self.authority)

    def export(self, role="read"):
        return export_credential(directory=self.authority, access=role)

    def test_init_uses_two_stable_roles_no_tx_and_source_is_unchanged(self):
        self.assertEqual(self.source.read_bytes(), Path("examples/gateway.com3.rx-reopen.toml").read_bytes())
        gateway, host, port = load(self.gateway_config, fake=False)
        self.assertEqual((host, port), ("127.0.0.1", 18081))
        self.assertEqual([p.grants["ch_uart_1"] for p in gateway.principals],
                         [frozenset({"read"}), frozenset({"read", "configure"})])
        self.assertNotEqual(gateway.principals[0].token, gateway.principals[1].token)
        before = {p: p.read_bytes() for p in self.gateway_config.parent.iterdir()}
        with self.assertRaisesRegex(ValueError, "已存在"):
            initialize(self.source, directory=self.authority)
        self.assertEqual(before, {p: p.read_bytes() for p in self.gateway_config.parent.iterdir()})
        profile = read_profile(self.authority, "default")
        for principal in gateway.principals:
            self.assertNotIn(principal.token, self.gateway_config.read_text())
            self.assertNotIn(principal.token, json.dumps(profile))

    def test_export_import_idempotence_default_selection_and_role_separation(self):
        reader, manager = self.export(), self.export("manage")
        import_credential(read_private(reader, 16384), directory=self.client, name="company", port=28081)
        settings = resolve_connection(directory=self.client)
        self.assertEqual((settings.host, settings.port, settings.access, settings.name), ("127.0.0.1", 28081, "read", "company"))
        with self.assertRaisesRegex(ValueError, "未配置"):
            resolve_connection(directory=self.client, access="manage")
        import_credential(read_private(manager, 16384), directory=self.client, name="company", port=28081)
        import_credential(read_private(reader, 16384), directory=self.client, name="company", port=28081)
        admin = resolve_connection(directory=self.client, access="manage")
        self.assertNotEqual(settings.source.read(), admin.source.read())
        select_default("company", directory=self.client)
        self.assertEqual(resolve_connection(directory=self.client).access, "read")
        self.assertNotIn(settings.source.read(), repr(settings))

    def test_manager_only_profile_never_supplies_a_missing_reader(self):
        import_credential(read_private(self.export("manage"), 16384), directory=self.client)
        with patch.dict(os.environ, {"UART_GATEWAY_TOKEN": "x" * 43}):
            with self.assertRaisesRegex(ValueError, "未配置"):
                resolve_connection(directory=self.client)
            self.assertEqual(resolve_connection(directory=self.client, access="manage").access, "manage")

    def test_fixed_profile_precedes_stale_environment_and_invalid_profile_never_falls_back(self):
        with patch.dict(os.environ, {"UART_GATEWAY_TOKEN": "x" * 43}):
            settings = resolve_connection(directory=self.authority)
            self.assertNotEqual(settings.source.read(), "x" * 43)
            (self.gateway_config.parent / "client.json").write_text("broken")
            with self.assertRaisesRegex(ValueError, "格式"):
                resolve_connection(directory=self.authority)
            with self.assertRaises(ValueError):
                resolve_connection(directory=self.authority, name="missing")

    def test_different_deployment_or_changed_existing_secret_never_overwrites(self):
        raw = read_private(self.export(), 16384)
        import_credential(raw, directory=self.client)
        secret = self.client / "connections/default/read.secret"
        original = secret.read_bytes()
        bundle = json.loads(raw)
        for field, value in (("deployment_id", "f" * 32), ("token", "x" * 43)):
            changed = dict(bundle, **{field: value})
            with self.assertRaisesRegex(ValueError, "不同|已有"):
                import_credential(json.dumps(changed).encode(), directory=self.client)
            self.assertEqual(secret.read_bytes(), original)

    def test_malformed_oversized_bundle_or_traversal_has_no_secret_in_exception(self):
        token = "s" * 43
        for data in (b"bad", b"x" * 16385, json.dumps({"token": token}).encode(), b"[]"):
            with self.assertRaises(ValueError) as error:
                import_credential(data, directory=self.client)
            self.assertNotIn(token, str(error.exception))
        with self.assertRaises(ValueError):
            import_credential(read_private(self.export(), 16384), name="../other", directory=self.client)
        self.assertFalse((self.root / "other").exists())

    @unittest.skipIf(os.name == "nt", "POSIX 權限另於 Windows ACL 測試替身覆蓋")
    def test_unsafe_mode_symlink_and_nonregular_sources_are_rejected(self):
        path = self.gateway_config.parent / "read.secret"
        path.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "600"):
            resolve_connection(directory=self.authority).source.read()
        # 舊明確來源保留相容，不將其誤標為固定受保護來源。
        self.assertTrue(explicit_source(token_file=path).read())
        path.chmod(0o600)
        link = self.root / "symlink"
        link.symlink_to(path)
        with self.assertRaisesRegex(ValueError, "符號連結"):
            read_private(link, 1024)
        fifo = self.root / "pipe"
        os.mkfifo(fifo, 0o600)
        with self.assertRaisesRegex(ValueError, "一般檔案"):
            read_private(fifo, 1024)

    def test_explicit_missing_source_and_unsafe_profile_destination_do_not_fallback(self):
        with self.assertRaises(ValueError):
            ConnectionSettings("127.0.0.1", 18081, explicit_source(), access="administrator")
        repository_target = Path.cwd() / "credential-test-must-not-exist"
        with self.assertRaisesRegex(ValueError, "版本庫"):
            initialize(self.source, directory=repository_target)
        self.assertFalse(repository_target.exists())
        with patch.dict(os.environ, {"UART_GATEWAY_TOKEN": "x" * 43}):
            with self.assertRaises(ValueError):
                explicit_source(token_env="").read()
            with self.assertRaises(ValueError):
                resolve_connection(name="default", token_env="UART_GATEWAY_TOKEN", directory=self.authority)
            with self.assertRaises(ValueError):
                resolve_connection(directory=self.authority, host="example.com")
            with self.assertRaises(ValueError):
                resolve_connection(token_file=self.root / "missing").source.read()

    def test_token_file_is_read_again_and_never_cached_in_settings(self):
        settings = resolve_connection(directory=self.authority)
        initial = settings.source.read()
        path = self.gateway_config.parent / "read.secret"
        path.write_text("x" * 43)
        self.assertNotEqual(initial, settings.source.read())
        self.assertNotIn("x" * 43, repr(settings))

    def test_failed_initialization_removes_only_its_new_profile(self):
        destination = self.root / "failed"
        original = write_private
        def write(path, data, **kwargs):
            if path.name == "client.json":
                raise OSError("測試磁碟失敗")
            return original(path, data, **kwargs)
        with patch("uart_gateway.auth_setup.write_private", side_effect=write):
            with self.assertRaises(OSError):
                initialize(self.source, directory=destination)
        self.assertFalse((destination / "connections/default").exists())
        self.assertFalse((destination / "default-connection").exists())
        self.assertTrue(self.gateway_config.exists())

    def test_failed_management_import_preserves_existing_reader_and_metadata(self):
        import_credential(read_private(self.export(), 16384), directory=self.client)
        directory = self.client / "connections/default"
        before = {path.name: path.read_bytes() for path in directory.iterdir()}
        original = write_private
        def write(path, data, **kwargs):
            if path.name == "client.json":
                raise OSError("測試寫入失敗")
            return original(path, data, **kwargs)
        bundle = read_private(self.export("manage"), 16384)
        with patch("uart_gateway.auth_setup.write_private", side_effect=write):
            with self.assertRaises(OSError):
                import_credential(bundle, directory=self.client)
        self.assertEqual(before, {path.name: path.read_bytes() for path in directory.iterdir()})

    def test_real_cli_setup_and_import_never_print_tokens(self):
        other = self.root / "cli"
        command = [sys.executable, "-m", "uart_gateway.cli", "--connections-dir", str(other), "auth"]
        result = subprocess.run(command + ["init", "--gateway-config", str(self.source)], text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        secret = (other / "connections/default/read.secret").read_text().strip()
        self.assertNotIn(secret, result.stdout + result.stderr)
        exported = export_credential(directory=other)
        imported = subprocess.run([sys.executable, "-m", "uart_gateway.cli", "--connections-dir", str(self.client),
                                   "auth", "import", "--stdin"], input=exported.read_bytes(), capture_output=True, timeout=10)
        self.assertEqual(imported.returncode, 0, imported.stderr)
        self.assertNotIn(secret.encode(), imported.stdout + imported.stderr)

    def test_explicit_default_port_still_overrides_a_different_profile_port(self):
        from uart_gateway.cli import main
        import_credential(read_private(self.export(), 16384), directory=self.client, port=28081)
        with patch("sys.argv", ["uart", "--connections-dir", str(self.client), "--port", "18081", "channels"]), \
                patch("asyncio.open_connection", new=AsyncMock(side_effect=ConnectionRefusedError)) as connect, \
                patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                main()
        self.assertEqual(result.exception.code, 1)
        self.assertEqual(connect.await_args.args[:2], ("127.0.0.1", 18081))

    def test_sdk_import_does_not_load_cli_server_serial_or_read_credentials(self):
        code = '''import sys
from unittest.mock import patch
with patch("pathlib.Path.open", side_effect=AssertionError("匯入不應讀取憑證")):
    from uart_gateway.client import Client
assert "uart_gateway.cli" not in sys.modules
assert "uart_gateway.server.app" not in sys.modules
assert "serial" not in sys.modules
'''
        result = subprocess.run([sys.executable, "-c", code], text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)


class WindowsPrivateFileTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "需要 Windows 實際 ACL；Linux 只驗證替身")
    def test_actual_windows_private_directory_and_file_acl(self):
        from uart_gateway.protected_files import private_directory
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "private"
            private_directory(directory)
            secret = directory / "test.secret"
            write_private(secret, b"test-only")
            self.assertEqual(read_private(secret, 1024), b"test-only")
            windows_acl(directory)
            windows_acl(secret)

    def test_acl_is_applied_before_secret_content_and_failures_are_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "private" / "test.secret"
            def acl(target, *, protect=False):
                if target == path:
                    self.assertEqual(path.read_bytes(), b"")
                    self.assertTrue(protect)
            with patch("uart_gateway.protected_files.WINDOWS", True), patch("uart_gateway.protected_files.windows_acl", side_effect=acl):
                write_private(path, b"sensitive")
            with patch("uart_gateway.protected_files.WINDOWS", True), \
                    patch("uart_gateway.protected_files.windows_acl", side_effect=ValueError("ACL 不符")):
                with self.assertRaises(ValueError):
                    read_private(path, 1024)

    def test_powershell_uses_static_script_and_path_environment_only(self):
        with patch("uart_gateway.protected_files.subprocess.run", return_value=Mock(returncode=0)) as run:
            windows_acl(Path("literal $(do-not-execute).secret"), protect=True)
        argv = run.call_args.args[0]
        self.assertNotIn("literal", " ".join(argv))
        self.assertIn("UART_GATEWAY_PRIVATE_PATH", run.call_args.kwargs["env"])
        with patch("uart_gateway.protected_files.subprocess.run", return_value=Mock(returncode=3)):
            with self.assertRaisesRegex(ValueError, "ACL"):
                windows_acl(Path("blocked"))
