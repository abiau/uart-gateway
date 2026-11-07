"""使用者私有檔案：POSIX 權限／擁有者，以及 Windows 使用者 ACL。"""

import os
from pathlib import Path
import secrets
import stat
import sys

WINDOWS = sys.platform == "win32"


def windows_acl(path: Path, *, protect: bool = False) -> None:
    from .windows_acl import check_acl

    try:
        check_acl(str(path.absolute()), protect=protect)
    except OSError as exc:
        raise ValueError(f"Windows 無法設定或讀取 ACL（錯誤 {exc.winerror}），請檢查檔案權限。") from None


def private_directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("憑證目錄不得是符號連結。")
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if WINDOWS:
            windows_acl(path, protect=True)
        else:
            info = path.stat()
            if info.st_uid != os.getuid() or not stat.S_ISDIR(info.st_mode):
                raise ValueError("憑證目錄擁有者不是目前使用者。")
            path.chmod(0o700)
    except OSError:
        raise ValueError("無法建立使用者私有憑證目錄，請確認位置與權限。") from None


def read_private(path: Path, maximum: int) -> bytes:
    if path.is_symlink():
        raise ValueError("憑證與連線設定不得是符號連結。")
    try:
        if WINDOWS:
            windows_acl(path)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("憑證來源必須是一般檔案。")
            if not WINDOWS and (info.st_uid != os.getuid() or info.st_mode & 0o077):
                raise ValueError("憑證來源須屬目前使用者，且限擁有者讀寫（chmod 600）。")
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError("憑證或連線設定超過大小上限。")
        return data
    except OSError:
        raise ValueError("無法讀取憑證來源，請先初始化或確認檔案權限。") from None


def write_private(path: Path, data: bytes, *, replace: bool = False) -> None:
    """先保護目錄／空檔再寫入內容；預設絕不覆蓋既有憑證。"""
    private_directory(path.parent)
    temporary = path.with_name(path.name + ".tmp-" + secrets.token_hex(8)) if replace else path
    created = False
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as stream:
            if WINDOWS:
                windows_acl(temporary, protect=True)
            if stream.write(data) != len(data):
                raise OSError("部分寫入")
            stream.flush()
        if replace:
            if path.exists():
                read_private(path, 65536)
            temporary.replace(path)
    except FileExistsError:
        if created:
            temporary.unlink(missing_ok=True)
        raise ValueError("憑證或設定已存在；未覆蓋，也未重新產生遠端憑證。") from None
    except BaseException:
        if created:
            temporary.unlink(missing_ok=True)
        raise
