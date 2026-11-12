"""明確選用的公司 SSH 驗證；僅使用臨時讀取憑證及 Fake Serial。"""

import asyncio
import hashlib
import os
import secrets
import unittest

from uart_gateway.fake_serial import FakeSerial
from uart_gateway.remote_install import RemoteTarget, install_payload, remote_command, ssh_arguments, stop_ssh
from uart_gateway.server.app import load
from tests.fakes import eventually
from tests.unit import test_credentials as credential_fixture


@unittest.skipUnless(os.environ.get("UART_GO_SSH_TARGET"), "需要明確設定 UART_GO_SSH_TARGET；不操作既有 UART")
class GoRemoteTests(unittest.IsolatedAsyncioTestCase):
    async def test_isolated_native_install_and_replay_over_real_reverse_ssh(self):
        fixture = credential_fixture.CredentialTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        gateway, _, _ = load(fixture.gateway_config, fake=False)
        device = FakeSerial(echo=False); gateway.channels["ch_uart_1"].port = device
        local = await gateway.start(port=0)
        target = RemoteTarget(os.environ["UART_GO_SSH_TARGET"], port=45000 + secrets.randbelow(15000))
        payload = install_payload(target, name="default", root=fixture.authority, hold=False)
        # 同一 SSH 內建立、使用及清除測試 HOME；不改原預設連線或已安裝 Client。
        script = '''set -eu
uart_test_home=$(mktemp -d "$HOME/.uart-go-test.XXXXXXXX")
cleanup() { case "$uart_test_home" in "$HOME"/.uart-go-test.*) rm -rf -- "$uart_test_home" ;; *) exit 99 ;; esac; }
trap cleanup 0
trap 'exit 1' 1 2 15
export XDG_CONFIG_HOME="$uart_test_home/.config"
''' + "HOME=\"$uart_test_home\" " + remote_command(target).removeprefix("exec ") + '''
export HOME="$uart_test_home"
"$HOME/.local/bin/uart" --version
"$HOME/.local/bin/uart" channels
"$HOME/.local/bin/uart" watch ch_uart_1 --replay --duration 2 --text
sha256sum "$HOME"/.local/share/uart-gateway/client-go/*
uname -m; uname -r; cat /etc/os-release
'''
        # cleanup 的根目錄需保留原 HOME，而非最後一段的測試 HOME。
        script = script.replace('uart_test_home=$(mktemp', 'uart_original_home=$HOME\nuart_test_home=$(mktemp').replace('in "$HOME"/.uart-go-test.*)', 'in "$uart_original_home"/.uart-go-test.*)')
        process = None
        try:
            device.inject(b"GO-REMOTE-FAKE-RX\n")
            await eventually(lambda: gateway.channels["ch_uart_1"].offset > 0)
            process = await asyncio.create_subprocess_exec(*ssh_arguments(target, forward=local)[:-1],
                "-o", "BatchMode=yes", target.ssh, script,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await asyncio.wait_for(process.communicate(payload), target.connect_timeout + 120)
            self.assertEqual(process.returncode, 0, err.decode(errors="replace")[:2000])
            self.assertIn(b"UART_REMOTE_READY", out)
            self.assertIn(b"0.2.0-go", out)
            self.assertIn(b"GO-REMOTE-FAKE-RX", out)
            header, rest = payload.split(b"\n", 1); length = int(header.split()[1])
            digest = hashlib.sha256(rest[:length]).hexdigest().encode()
            self.assertIn(digest, out)
            self.assertEqual(device.write_count, 0)
            await eventually(lambda: not gateway.sessions)
            # 僅輸出非秘密平台資訊；不列 profile、環境或憑證。
            print(out.decode(errors="replace"))
        finally:
            if process is not None: await stop_ssh(process)
            await gateway.close()
