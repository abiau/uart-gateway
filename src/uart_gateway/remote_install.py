"""固定角色透過 SSH 標準輸入安裝；秘密不進 argv、一般日誌或暫存包。"""

import asyncio
import base64
from dataclasses import asdict, dataclass
import hashlib
import io
import json
from pathlib import Path
import re
import shlex
import subprocess
import zipfile

from .auth_setup import credential_bundle
from .client.connection import connection_name, endpoint
from .protected_files import read_private, write_private

MAX_OUTPUT = 65536


@dataclass(frozen=True)
class RemoteTarget:
    ssh: str
    ssh_port: int = 22
    container: str = ""
    python: str = "python3"
    name: str = "company"
    port: int = 18081
    roles: tuple[str, ...] = ("read",)

    def __post_init__(self):
        if (not isinstance(self.ssh, str) or len(self.ssh) > 255 or
                not re.fullmatch(r"(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9_][A-Za-z0-9_.-]*", self.ssh)):
            raise ValueError("SSH 目的地請使用主機別名或 帳號@主機，不包含其他參數。")
        if type(self.ssh_port) is not int or not 1 <= self.ssh_port <= 65535:
            raise ValueError("SSH Port 須為 1～65535。")
        if not isinstance(self.container, str) or self.container and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", self.container):
            raise ValueError("Container 名稱不合法。")
        if (not isinstance(self.python, str) or len(self.python) > 4096 or not self.python
                or self.python.startswith("-") or not self.python.isprintable()):
            raise ValueError("遠端 Python 請使用執行檔名稱或絕對路徑。")
        connection_name(self.name)
        endpoint("127.0.0.1", self.port)
        if (not isinstance(self.roles, tuple) or not self.roles or "read" not in self.roles
                or len(set(self.roles)) != len(self.roles) or set(self.roles) - {"read", "manage", "tx"}):
            raise ValueError("安裝角色須包含 read；manage／tx 需要明確選用。")


def load_target(path: Path) -> RemoteTarget | None:
    if not path.exists() and not path.is_symlink():
        return None
    try:
        value = json.loads(read_private(path, 8192))
        if not isinstance(value, dict) or set(value) != {"version", *RemoteTarget.__dataclass_fields__} or type(value["version"]) is not int or value.pop("version") != 1:
            raise ValueError
        if not isinstance(value["roles"], list):
            raise ValueError
        value["roles"] = tuple(value["roles"])
        return RemoteTarget(**value)
    except (ValueError, TypeError, RecursionError):
        raise ValueError("遠端啟動設定無法讀取；未覆寫，請檢查私有 remote.json。") from None


def save_target(path: Path, target: RemoteTarget) -> None:
    load_target(path)
    write_private(path, json.dumps({"version": 1, **asdict(target)}, ensure_ascii=False).encode(), replace=True)


def build_client() -> bytes:
    output = io.BytesIO()
    package = Path(__file__).parent
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        files = sorted(package.rglob("*.py"))
        if len(files) > 250:
            raise ValueError("Client 套件檔案數超過安裝上限。")
        for path in files:
            if path.is_symlink() or path.stat().st_size > 1048576:
                raise ValueError("Client 原始碼超過安裝界限或包含符號連結。")
            entry = zipfile.ZipInfo("uart_gateway/" + path.relative_to(package).as_posix(), (2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, path.read_bytes())
        entry = zipfile.ZipInfo("__main__.py", (2026, 1, 1, 0, 0, 0))
        entry.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(entry, "# UART Gateway Client\nfrom uart_gateway.cli import main\nmain()\n")
    data = output.getvalue()
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("Client 安裝包超過 2 MiB。")
    return data


def install_payload(target: RemoteTarget, *, name: str, root: Path, hold: bool) -> bytes:
    archive = build_client()
    credentials = [json.loads(credential_bundle(name=name, access=role, directory=root)) for role in target.roles]
    return (json.dumps({"version": 1, "archive": base64.b64encode(archive).decode(),
                       "sha256": hashlib.sha256(archive).hexdigest(), "credentials": credentials,
                       "name": target.name, "port": target.port, "hold": hold}, separators=(",", ":")) + "\n").encode()


def ssh_arguments(target: RemoteTarget, *, trust=False, forward: int | None = None) -> list[str]:
    result = ["ssh", "-T", "-p", str(target.ssh_port), "-o", "StrictHostKeyChecking=" + ("ask" if trust else "yes"),
              "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"]
    if forward is not None:
        endpoint("127.0.0.1", forward)
        result += ["-o", "ExitOnForwardFailure=yes", "-R", f"127.0.0.1:{target.port}:127.0.0.1:{forward}"]
    return result + [target.ssh]


def trust_target(target: RemoteTarget) -> None:
    # 無秘密的互動步驟，讓 OpenSSH 正常核對主機指紋與登入。
    if subprocess.run([*ssh_arguments(target, trust=True), "true"], check=False).returncode:
        raise ValueError("SSH 登入或主機指紋核對未完成；未傳送憑證。")


def remote_command(target: RemoteTarget) -> str:
    source = Path(__file__).with_name("remote_bootstrap.py").read_text(encoding="utf-8")
    command = shlex.join([target.python, "-c", source])
    if target.container:
        container = shlex.quote(target.container)
        # 僅支援已知 host 網路，不修改容器或額外開放接聽端。
        check = "$(podman inspect --format '{{.HostConfig.NetworkMode}}' " + container + ")"
        return "test " + '"' + check + '" = host || { echo UART_REMOTE_FAILED_NETWORK; exit 42; }; exec podman exec -i ' + container + " " + command
    return "exec " + command


async def stop_ssh(process) -> None:
    if process.stdin:
        process.stdin.close()
    try:
        async with asyncio.timeout(3):
            await process.wait()
    except TimeoutError:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            async with asyncio.timeout(3):
                await process.wait()
        except TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


async def open_remote(target: RemoteTarget, payload: bytes, *, forward: int | None = None):
    """READY 只在安裝完成、且 hold 模式實際認證成功後回傳。"""
    process = await asyncio.create_subprocess_exec(*ssh_arguments(target, forward=forward), remote_command(target),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, limit=MAX_OUTPUT)
    try:
        async with asyncio.timeout(90):
            process.stdin.write(payload)
            await process.stdin.drain()
            consumed = 0
            while True:
                line = await process.stdout.readline()
                consumed += len(line)
                if line.startswith(b"UART_REMOTE_FAILED_"):
                    code = line.strip().removeprefix(b"UART_REMOTE_FAILED_")
                    hint = {b"RUNTIME": "Linux 需要 Python 3.12 以上，請選擇正確的 Python 執行檔。",
                            b"NETWORK": "此 Container 未使用 host 網路；請選主機或原本的 host 網路容器，本工具不修改網路。",
                            b"ENTRY": "~/.local/bin/uart 已有其他程式或不安全的檔案；未覆蓋，請先確認既有入口。",
                            b"VERSIONS": "已保存八個 Client 版本；請在結束所有 Client 後清理 ~/.local/share/uart-gateway/client 的舊版本。",
                            b"CREDENTIALS": "憑證安裝失敗；請確認私有目錄權限及連線名稱。不同部署或相異既有憑證不會被覆蓋，部分已安裝角色可用相同來源重試。",
                            b"AUTH": "Gateway 尚未通過遠端認證／通道查詢；請確認原固定 Gateway 與 SSH 轉送 Port。"}.get(code)
                    raise ValueError(hint or "Client 安裝檔案檢查失敗；請確認 Python 版本、使用者目錄與檔案權限。")
                if not line or consumed > MAX_OUTPUT:
                    raise ValueError("Linux Client 安裝／驗證失敗。請確認 Python 3.12 以上、SSH 登入、容器 host 網路與私有目錄權限；不同部署憑證或既有非本工具的 ~/.local/bin/uart 不會被覆蓋。")
                if line.startswith(b"UART_REMOTE_READY "):
                    status = json.loads(line[len(b"UART_REMOTE_READY "):])
                    if not isinstance(status, dict) or set(status) != {"path_ready"} or type(status["path_ready"]) is not bool:
                        raise ValueError("遠端就緒回覆不合法。")
                    return process, status["path_ready"]
    except BaseException:
        await stop_ssh(process)
        raise


async def install_remote(target: RemoteTarget, *, name: str, root: Path) -> bool:
    payload = await asyncio.to_thread(install_payload, target, name=name, root=root, hold=False)
    process, in_path = await open_remote(target, payload)
    try:
        async with asyncio.timeout(5):
            result = await process.wait()
        if result:
            raise ValueError("遠端安裝未正常完成；請以原固定連線重試，不重新產生 Token。")
        return in_path
    finally:
        await stop_ssh(process)
