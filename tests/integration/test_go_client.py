"""Go 獨立 Client 與既有 Python Gateway 的契約及 POSIX 生命週期驗證。"""

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import unittest

from uart_gateway.auth_setup import credential_bundle
from uart_gateway.client import Client
from uart_gateway.core.models import ChannelConfig
from uart_gateway.fake_serial import FakeSerial
from uart_gateway.remote_install import RemoteTarget, install_payload, remote_command
from uart_gateway.server.auth import Principal
from uart_gateway.server.tcp import Gateway
from tests.fakes import eventually
from tests.integration.test_shell_exec import FakeShell
from tests.integration import test_workbench as console_fixture
from tests.unit import test_credentials as credential_fixture

BINARY = os.environ.get("UART_GO_BINARY", "")
AVAILABLE = os.name == "posix" and bool(BINARY) and Path(BINARY).is_file()


@unittest.skipUnless(AVAILABLE, "需要 Linux 與 UART_GO_BINARY 指定本輪編譯的 ELF")
class GoClientTests(console_fixture.WorkbenchProcessTests):
    test_browse_check_play_export_with_real_cli_and_no_gateway = None
    test_record_ctrl_c_cleanup_then_return_to_workbench = None

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.device, self.peer = FakeShell(), FakeSerial(echo=False)
        self.tokens = {role: secrets.token_urlsafe(32) for role in ("read", "manage", "tx")}
        self.gateway = Gateway([(ChannelConfig("a", "測試板"), self.device), (ChannelConfig("b", "旁路"), self.peer)],
            tuple(Principal(role, token, {cid: frozenset({"read"} | ({"configure"} if role != "read" else set()) | ({"tx"} if role == "tx" else set())) for cid in ("a", "b")}) for role, token in self.tokens.items()))
        self.port = await self.gateway.start(port=0)
        self.config = self.root / "config"
        for role, token in self.tokens.items():
            bundle = {"version": 1, "deployment_id": "a" * 32, "port": self.port, "access": role, "token": token}
            code, out, err = await self.cli("auth", "import", "--stdin", "--name", "company", data=json.dumps(bundle).encode())
            self.assertEqual(code, 0, err)

    async def asyncTearDown(self):
        await super().asyncTearDown()
        await self.gateway.close()

    def command(self, *args):
        return [BINARY, "--connections-dir", str(self.config), *args]

    async def cli(self, *args, data=None):
        p = await asyncio.create_subprocess_exec(*self.command(*args), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(p.communicate(data), 12)
        finally:
            if p.returncode is None:
                p.kill()
                await p.wait()
        return p.returncode, out, err

    async def ready(self):
        self.start(self.command("shell-ready", "a", "--newline", "lf"))
        await self.expect("輸入 Y 保存本次核對")
        self.answer("y")
        await self.expect("已保存本次 Shell 目標核對")
        self.assertEqual(await asyncio.to_thread(self.process.wait), 0)
        self.assertEqual(self.device.write_count, 0)

    async def test_channels_binary_replay_and_live_multiplex(self):
        code, out, err = await self.cli("channels")
        self.assertEqual(code, 0, err)
        self.assertIn(b'"channel_id":"a"', out)
        raw = b"\x00\xff\x1b[31mboot\r\n"
        self.device.inject(raw)
        await eventually(lambda: self.gateway.channels["a"].offset == len(raw))
        code, out, err = await self.cli("watch", "a", "--replay", "--duration", "1.5")
        self.assertEqual(code, 0, err)
        rx = [json.loads(line) for line in out.splitlines() if json.loads(line).get("event") == "rx"]
        self.assertEqual(b"".join(base64.b64decode(e["data"]["data_b64"]) for e in rx), raw)
        self.assertTrue(all(e["data"]["delivery"] == "replay" for e in rx))
        task = asyncio.create_task(self.cli("watch", "a", "b", "--duration", "1.5", "--text"))
        await eventually(lambda: all(self.gateway.channels[c].subscriptions for c in ("a", "b")))
        self.device.inject(b"FIRST\n"); self.peer.inject(b"SECOND\n")
        code, out, err = await task
        self.assertEqual(code, 0, err)
        self.assertIn(b"FIRST", out); self.assertIn(b"SECOND", out)
        self.assertEqual(self.device.write_count + self.peer.write_count, 0)
        await eventually(lambda: not self.gateway.sessions)

    async def test_manual_shell_authorization_exec_and_query_do_not_resend(self):
        code, _, _ = await self.cli("exec", "a", "uname -a")
        self.assertEqual(code, 1)
        self.assertEqual(self.device.write_count, 0)
        await self.ready()
        code, out, err = await self.cli("exec", "a", "uname -a")
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual((result["state"], result["exit_code"], result["output_text"]), ("completed", 0, "Linux ipcam\r\n"))
        self.assertTrue(result["output_complete"])
        code, out, err = await self.cli("exec-status", result["execution_id"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["output_text"], result["output_text"])
        self.assertEqual(self.device.write_count, 1)
        self.assertIsNone(self.gateway.channels["a"].lease.owner)

    async def test_large_replay_is_exact_and_finishes_before_live(self):
        raw = bytes(range(256)) * 16
        for batch in range(6):
            for _ in range(32): self.device.inject(raw)
            await eventually(lambda: self.gateway.channels["a"].offset == (batch + 1) * 32 * len(raw))
        code, out, err = await self.cli("watch", "a", "--replay", "--duration", "3")
        self.assertEqual(code, 0, err)
        rx = [json.loads(line) for line in out.splitlines() if json.loads(line).get("event") == "rx"]
        self.assertEqual(b"".join(base64.b64decode(e["data"]["data_b64"]) for e in rx), raw * 192)
        self.assertEqual(rx[-1]["data"]["next_offset"], str(len(raw) * 192))
        self.assertEqual(self.device.write_count, 0)

    async def test_timeout_blocks_new_command_then_query_recovers_without_tx(self):
        await self.ready(); self.device.mode = "begin"
        code, out, err = await self.cli("exec", "a", "slow", "--timeout", "0.1")
        self.assertEqual(code, 2, err); result = json.loads(out)
        self.assertEqual(result["state"], "unknown")
        self.assertEqual((await self.cli("exec", "a", "never"))[0], 1)
        self.device.complete()
        await eventually(lambda: self.gateway.channels["a"].offset > 300)
        code, out, err = await self.cli("exec-status", result["execution_id"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["state"], "completed")
        self.assertEqual(self.device.write_count, 1)

    async def test_nonzero_exit_truncation_and_lease_cover_wait(self):
        await self.ready(); self.device.mode = "begin"; self.device.code = 7
        self.device.body = b"x" * 20000
        task = asyncio.create_task(self.cli("exec", "a", "false", "--max-output", "16"))
        await eventually(lambda: self.device.write_count == 1)
        await asyncio.sleep(0.15)
        self.assertIsNotNone(self.gateway.channels["a"].lease.owner)
        self.device.complete()
        code, out, err = await task
        self.assertEqual(code, 1, err); result = json.loads(out)
        self.assertEqual(result["exit_code"], 7)
        self.assertEqual(result["output_text"], "x" * 16)
        self.assertTrue(result["output_truncated"])
        self.assertFalse(result["output_complete"])

    async def test_lease_renews_through_long_shell_wait_and_excludes_second_owner(self):
        from dataclasses import replace
        self.gateway.limits = replace(self.gateway.limits, lease_ttl_ms=600, lease_renew_interval_ms=100)
        self.gateway.channels["a"].lease.ttl = 0.6
        await self.ready(); self.device.mode = "begin"
        task = asyncio.create_task(self.cli("exec", "a", "slow", "--timeout", "4"))
        await eventually(lambda: self.device.write_count == 1)
        initial = self.gateway.channels["a"].lease.deadline
        await eventually(lambda: self.gateway.channels["a"].lease.deadline > initial + 0.7, timeout=3)
        async with await Client.connect("127.0.0.1", self.port, self.tokens["tx"]) as c:
            from uart_gateway.protocol.errors import RemoteError
            with self.assertRaises(RemoteError) as caught:
                await c.acquire(await c.get_channel("a"))
            self.assertEqual(caught.exception.code, "LEASE_BUSY")
        self.device.complete()
        code, out, err = await task
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["state"], "completed")
        self.assertEqual(self.device.write_count, 1)

    async def test_read_role_cannot_upgrade_and_noninteractive_ready_never_confirms(self):
        code, _, err = await self.cli("--access", "read", "shell-ready", "a", "--newline", "lf")
        self.assertEqual(code, 1, err)
        self.assertEqual((await self.cli("shell-ready", "a", "--newline", "lf"))[0], 1)
        self.assertFalse(self.gateway.channels["a"].confirmed)
        self.assertEqual(self.device.write_count, 0)

    async def test_menu_ctrl_c_returns_and_gateway_remains_running(self):
        self.start(self.command())
        await self.expect("輸入一至四個不同編號")
        self.answer("1")
        await self.expect("訂閱已就緒")
        self.device.inject(b"menu-RX\n")
        await self.expect("menu-RX")
        self.process.send_signal(signal.SIGINT)
        await self.expect("監看已停止")
        await self.finish()
        await eventually(lambda: not self.gateway.sessions)
        self.assertTrue(self.gateway.listener.is_serving())

    async def test_binary_send_preview_status_and_partial_write_never_report_success(self):
        for partial in (False, True):
            self.pending.clear(); self.output.clear()
            if partial: self.peer.write_result = 1
            self.start(self.command("send", "b", "00 ff", "--hex", "--newline", "crlf"))
            await self.expect("精確傳送內容：00ff0d0a")
            self.answer("y")
            await self.expect('"tx_id"')
            code = await asyncio.wait_for(asyncio.to_thread(self.process.wait), 6)
            self.assertEqual(code, 2 if partial else 0)
            receipts = list((self.config / "go-receipts/company").glob("*.json"))
            receipt = next(json.loads(p.read_bytes()) for p in receipts if json.loads(p.read_bytes())["tx_result"]["state"] == ("unknown" if partial else "written_to_driver"))
            code, out, err = await self.cli("status", "b", receipt["tx_id"])
            self.assertEqual(code, 2 if partial else 0, err)
            self.assertEqual(self.peer.write_count, 2 if partial else 1)
            self.assertEqual(self.peer.writes[-1], b"\x00\xff\r\n")

    async def test_changed_epoch_refuses_old_shell_authorization(self):
        await self.ready()
        async with await Client.connect("127.0.0.1", self.port, self.tokens["tx"]) as c:
            await c.rebind(await c.get_channel("a"))
        code, _, _ = await self.cli("exec", "a", "never")
        self.assertEqual(code, 1)
        self.assertEqual(self.device.write_count, 0)

    async def test_cancel_pending_shell_keeps_original_receipt_and_never_sends_control_bytes(self):
        await self.ready(); self.device.mode = "begin"
        p = await asyncio.create_subprocess_exec(*self.command("exec", "a", "slow"),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            await eventually(lambda: self.device.write_count == 1)
            p.send_signal(signal.SIGINT)
            out, err = await asyncio.wait_for(p.communicate(), 6)
            self.assertEqual(p.returncode, 2, err)
            result = json.loads(out); self.assertEqual(result["state"], "unknown")
            self.assertEqual(self.device.write_count, 1)
            self.assertIsNone(self.gateway.channels["a"].lease.owner)
        finally:
            if p.returncode is None: p.kill(); await p.wait()


@unittest.skipUnless(AVAILABLE, "需要 Linux 與 UART_GO_BINARY")
class GoInstallTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = credential_fixture.CredentialTests(); self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.home = self.fixture.root / "home"; self.home.mkdir(mode=0o700)
        self.env = dict(os.environ, HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / ".config"), PATH="/usr/bin:/bin")
        self.target = RemoteTarget("host.invalid")
        self.binary_env = os.environ.get("UART_GATEWAY_CLIENT_BINARY")
        os.environ["UART_GATEWAY_CLIENT_BINARY"] = BINARY
        self.addCleanup(self.restore_env)

    def restore_env(self):
        if self.binary_env is None: os.environ.pop("UART_GATEWAY_CLIENT_BINARY", None)
        else: os.environ["UART_GATEWAY_CLIENT_BINARY"] = self.binary_env

    async def install(self, payload=None):
        command = ["sh", "-c", remote_command(self.target)]
        image = os.environ.get("UART_GO_INSTALL_IMAGE")
        if image:
            command = ["podman", "run", "--rm", "-i", "--network", "none",
                "-v", f"{self.home}:/uart-home:rw", "-e", "HOME=/uart-home", "-e", "XDG_CONFIG_HOME=/uart-home/.config",
                "-e", "PATH=/usr/bin:/bin", image, *command]
        p = await asyncio.create_subprocess_exec(*command, env=self.env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(p.communicate(payload or install_payload(self.target, name="default", root=self.fixture.authority, hold=False)), 15)
        return p.returncode, out, err

    async def test_native_installer_without_python_reuses_credentials_and_version(self):
        for _ in range(2):
            code, out, err = await self.install()
            self.assertEqual(code, 0, err); self.assertIn(b"UART_REMOTE_READY", out)
        executable = self.home / ".local/bin/uart"
        command = [str(executable), "--version"]
        if image := os.environ.get("UART_GO_INSTALL_IMAGE"):
            command = ["podman", "run", "--rm", "--network", "none", "-v", f"{self.home}:/uart-home:ro", image, "/uart-home/.local/bin/uart", "--version"]
        p = await asyncio.create_subprocess_exec(*command, env=self.env, stdout=asyncio.subprocess.PIPE)
        out, _ = await p.communicate(); self.assertEqual(p.returncode, 0); self.assertIn(b"0.2.0-go", out)
        original = json.loads(credential_bundle(directory=self.fixture.authority))["token"].encode()
        secret = self.home / ".config/uart-gateway/connections/company/read.secret"
        self.assertEqual(secret.read_bytes().strip(), original)
        self.assertEqual(secret.stat().st_mode & 0o777, 0o600)
        versions = list((self.home / ".local/share/uart-gateway/client-go").iterdir())
        self.assertEqual(len(versions), 1)
        self.assertEqual(hashlib.sha256(versions[0].read_bytes()).hexdigest(), versions[0].name)

    async def test_bad_hash_and_unrelated_launcher_are_preserved(self):
        payload = install_payload(self.target, name="default", root=self.fixture.authority, hold=False)
        header, remainder = payload.split(b"\n", 1)
        magic, length, _ = header.split()
        code, out, _ = await self.install(b" ".join((magic, length, b"0" * 64)) + b"\n" + remainder)
        self.assertNotEqual(code, 0); self.assertNotIn(b"UART_REMOTE_READY", out)
        entry = self.home / ".local/bin/uart"; entry.parent.mkdir(parents=True); entry.write_text("USER OWNED\n"); entry.chmod(0o600)
        code, out, _ = await self.install()
        self.assertNotEqual(code, 0); self.assertIn(b"UART_REMOTE_FAILED_ENTRY", out)
        self.assertEqual(entry.read_text(), "USER OWNED\n")

    async def test_hold_ready_requires_actual_native_sdk_authentication(self):
        from dataclasses import replace
        from uart_gateway.server.app import load
        gateway, _, _ = load(self.fixture.gateway_config, fake=False)
        device = FakeSerial(echo=False); gateway.channels["ch_uart_1"].port = device
        port = await gateway.start(port=0)
        self.target = replace(self.target, port=port)
        try:
            payload = install_payload(self.target, name="default", root=self.fixture.authority, hold=True)
            code, out, err = await self.install(payload)
            self.assertEqual(code, 0, err); self.assertIn(b"UART_REMOTE_READY", out)
            await eventually(lambda: not gateway.sessions)
            self.assertEqual(device.write_count, 0)
        finally: await gateway.close()
