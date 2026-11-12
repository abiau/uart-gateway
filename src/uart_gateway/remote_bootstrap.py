"""由 Windows 經已驗證的 SSH 執行；僅安裝受控 Client 與明確角色。"""

import asyncio
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import stat
import sys
import tempfile
import zipfile

MAX_ARCHIVE = 2 * 1024 * 1024
MAX_ENVELOPE = 3 * 1024 * 1024
MARKER = "# UART Gateway 管理的 Client 入口 v1"


class InstallError(ValueError):
    """只允許固定錯誤階段離開受保護的安裝路徑。"""


def safe_directory(path: Path, *, private=False) -> None:
    if path.is_symlink():
        raise ValueError
    path.mkdir(mode=0o700 if private else 0o755, exist_ok=True)
    info = path.stat()
    if info.st_uid != os.getuid() or not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
        raise ValueError
    if private:
        path.chmod(0o700)


def install_file(path: Path, data: bytes, mode: int, *, launcher=False, immutable=False) -> None:
    if path.is_symlink():
        raise ValueError
    if path.exists():
        info = path.stat()
        if info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or info.st_size > MAX_ARCHIVE:
            raise ValueError
        old = path.read_bytes()
        if launcher and MARKER.encode() not in old[:512]:
            raise ValueError
        if old == data:
            path.chmod(mode)
            return
        if immutable:
            raise ValueError
    descriptor, temporary = tempfile.mkstemp(prefix=".uart-install-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def install(payload: dict) -> tuple[Path, bool]:
    if os.name != "posix" or sys.version_info < (3, 12):
        raise InstallError("RUNTIME")
    if not isinstance(payload, dict) or set(payload) != {"version", "archive", "sha256", "credentials", "name", "port", "hold"}:
        raise ValueError
    if type(payload["version"]) is not int or payload["version"] != 1 or type(payload["hold"]) is not bool:
        raise ValueError
    archive = base64.b64decode(payload["archive"], validate=True)
    if len(archive) > MAX_ARCHIVE or hashlib.sha256(archive).hexdigest() != payload["sha256"]:
        raise ValueError
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        if "__main__.py" not in package.namelist() or len(package.infolist()) > 256 or sum(f.file_size for f in package.infolist()) > 8 * MAX_ARCHIVE:
            raise ValueError
    if not isinstance(payload["credentials"], list) or not 1 <= len(payload["credentials"]) <= 3:
        raise ValueError
    home = Path.home()
    for path in (home / ".local", home / ".local" / "share", home / ".local" / "share" / "uart-gateway",
                 home / ".local" / "share" / "uart-gateway" / "client", home / ".local" / "bin"):
        safe_directory(path, private=path.name == "client")
    directory = home / ".local" / "share" / "uart-gateway" / "client"
    target = directory / (payload["sha256"] + ".pyz")
    # 套件使用不可變版本路徑，更新入口不會改寫其他 Client 正在使用的 ZIP。
    with os.scandir(directory) as entries:
        count = 0
        for entry in entries:
            count += 1
            if count > 8:
                raise ValueError
    if count >= 8 and not target.exists():
        raise InstallError("VERSIONS")
    launcher = home / ".local" / "bin" / "uart"
    script = ("#!/bin/sh\n" + MARKER + "\nexec " + shlex.quote(sys.executable) + " " + shlex.quote(str(target)) + ' "$@"\n').encode()
    # 先檢查既有入口，避免把 pip 或使用者自己的 uart 覆蓋掉。
    if launcher.exists() or launcher.is_symlink():
        info = launcher.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022
                or info.st_size > 4096 or MARKER.encode() not in launcher.read_bytes()[:512]):
            raise InstallError("ENTRY")
    install_file(target, archive, 0o600, immutable=True)
    sys.path.insert(0, str(target))
    from uart_gateway.auth_setup import import_credential, select_default
    from uart_gateway.client.connection import connection_name, endpoint
    name = connection_name(payload["name"])
    endpoint("127.0.0.1", payload["port"])
    try:
        for bundle in payload["credentials"]:
            import_credential(json.dumps(bundle).encode(), name=name, port=payload["port"])
        select_default(name)
    except (ValueError, OSError):
        raise InstallError("CREDENTIALS") from None
    install_file(launcher, script, 0o700, launcher=True)
    in_path = str(launcher.parent) in os.environ.get("PATH", "").split(os.pathsep)
    return launcher, in_path


def main() -> None:
    try:
        raw = sys.stdin.buffer.readline(MAX_ENVELOPE + 1)
        if len(raw) > MAX_ENVELOPE or not raw.endswith(b"\n"):
            raise ValueError
        payload = json.loads(raw)
        launcher, in_path = install(payload)
        if payload["hold"]:
            from uart_gateway.client import Client
            async def verify():
                async with await Client.connect_auto(payload["name"]) as client:
                    await client.list_channels()
            try:
                asyncio.run(verify())
            except Exception:
                raise InstallError("AUTH") from None
        print("UART_REMOTE_READY " + json.dumps({"path_ready": in_path}), flush=True)
        if payload["hold"]:
            # EOF 只結束本次 SSH 工作階段，不操作 UART。
            while sys.stdin.buffer.read(1):
                pass
    except Exception as exc:
        # stdin 包含秘密；遠端錯誤不回印輸入、例外參數或 traceback。
        code = str(exc) if isinstance(exc, InstallError) else "FILES"
        print("UART_REMOTE_FAILED_" + code, flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
