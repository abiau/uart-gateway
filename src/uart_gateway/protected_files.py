"""使用者私有檔案：POSIX 權限／擁有者，以及 Windows 使用者 ACL。"""

import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys

WINDOWS = sys.platform == "win32"

# 使用固定腳本，路徑經環境變數傳入，不組合 Shell 指令或傳送秘密內容。
ACL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$p = $env:UART_GATEWAY_PRIVATE_PATH
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
if ($env:UART_GATEWAY_PRIVATE_ACTION -eq 'protect') {
    if (Test-Path -LiteralPath $p -PathType Container) {
        $acl = New-Object System.Security.AccessControl.DirectorySecurity
        $inherit = [System.Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
    } else {
        $acl = New-Object System.Security.AccessControl.FileSecurity
        $inherit = [System.Security.AccessControl.InheritanceFlags]::None
    }
    $acl.SetOwner($sid)
    $acl.SetAccessRuleProtection($true, $false)
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', $inherit, 'None', 'Allow')
    $acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $p -AclObject $acl
}
$acl = Get-Acl -LiteralPath $p
if ($acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -ne $sid.Value) { exit 2 }
foreach ($rule in $acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])) {
    if ($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -ne $sid.Value) { exit 3 }
}
"""


def windows_acl(path: Path, *, protect: bool = False) -> None:
    environment = dict(os.environ, UART_GATEWAY_PRIVATE_PATH=str(path.absolute()),
                       UART_GATEWAY_PRIVATE_ACTION="protect" if protect else "check")
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ACL_SCRIPT],
                                env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("無法檢查 Windows 憑證 ACL，請確認系統 PowerShell 可用。") from None
    if result.returncode:
        raise ValueError("憑證 ACL 未限制為目前使用者，請重新初始化或修正檔案權限。")


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
