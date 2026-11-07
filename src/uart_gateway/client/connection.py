"""固定來源的遠端連線設定；載入與角色核對集中於 SDK。"""

import asyncio
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re

from uart_gateway.protected_files import read_private
from .credentials import TokenSource, explicit_source


def config_directory() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "uart-gateway"
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "uart-gateway"


def connection_name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ValueError("連線名稱須為 1～64 個英數字、底線或連字號。")
    return value


def endpoint(host: str, port: int) -> None:
    if host not in ("127.0.0.1", "::1") or type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("自動憑證連線僅允許 Loopback 與 1～65535 Port；遠端請使用 SSH Tunnel。")


def profile_path(root: Path, name: str) -> Path:
    return root / "connections" / connection_name(name)


def default_name(root: Path) -> str:
    path = root / "default-connection"
    if not path.exists() and not path.is_symlink():
        return "default"
    try:
        return connection_name(read_private(path, 128).decode("utf-8").strip())
    except UnicodeError:
        raise ValueError("預設連線設定無法讀取，請使用 uart auth use 重新選擇。") from None


def read_profile(root: Path, name: str) -> dict:
    directory = profile_path(root, name)
    if directory.is_symlink():
        raise ValueError("連線目錄不得是符號連結。")
    try:
        value = json.loads(read_private(directory / "client.json", 16384))
        required = {"version", "deployment_id", "host", "port", "credentials"}
        if (not isinstance(value, dict) or not required <= value.keys() or
                value.keys() - required - {"gateway_config"} or type(value["version"]) is not int or value["version"] != 1
                or not isinstance(value["deployment_id"], str) or not re.fullmatch(r"[a-f0-9]{32}", value["deployment_id"])
                or not isinstance(value["credentials"], dict) or not value["credentials"]
                or value["credentials"].keys() - {"read", "manage"}):
            raise ValueError("連線設定欄位或版本不合法。")
        endpoint(value["host"], value["port"])
        for reference in value["credentials"].values():
            if not isinstance(reference, str) or len(reference) > 4096 or not reference.startswith(("file:", "env:")):
                raise ValueError("連線設定只能保存憑證參照，不接受 Token 值。")
        if "gateway_config" in value and (not isinstance(value["gateway_config"], str) or len(value["gateway_config"]) > 4096):
            raise ValueError("Gateway 設定路徑不合法。")
        return value
    except (json.JSONDecodeError, UnicodeError, TypeError, RecursionError):
        raise ValueError("連線設定格式不合法，請重新初始化；未切換其他來源。") from None


@dataclass(frozen=True)
class ConnectionSettings:
    host: str
    port: int
    source: TokenSource = field(repr=False)
    access: str | None = None
    name: str | None = None

    def __post_init__(self):
        if self.access not in (None, "read", "manage"):
            raise ValueError("連線模式僅接受 read 或 manage。")
        if self.access is not None:
            endpoint(self.host, self.port)

    async def connect(self, *, timeout: float = 5):
        from .sdk import Client
        token = await asyncio.to_thread(self.source.read)
        client = await Client.connect(self.host, self.port, token, timeout=timeout)
        try:
            if self.access is not None:
                grants = client.auth["grants"]
                allowed = {"read"} if self.access == "read" else {"read", "configure"}
                if (not grants or any(set(g["permissions"]) - allowed for g in grants)
                        or self.access == "manage" and not any("configure" in g["permissions"] for g in grants)):
                    raise ValueError("憑證的實際權限與所選讀取／管理模式不符，已關閉連線；未提權或傳送 UART 指令。")
            return client
        except BaseException:
            await client.close()
            raise


def resolve_connection(*, name: str | None = None, access: str | None = None, directory: Path | None = None,
                       host: str | None = None, port: int | None = None,
                       token_file: str | Path | None = None, token_env: str | None = None) -> ConnectionSettings:
    if access not in (None, "read", "manage"):
        raise ValueError("連線模式僅接受 read 或 manage。")
    if token_file is not None or token_env is not None:
        if name is not None or access is not None:
            raise ValueError("固定連線／模式與明確 Token 來源不能混用。")
        return ConnectionSettings(host or "127.0.0.1", port if port is not None else 18081,
                                  explicit_source(token_file=token_file, token_env=token_env))
    root = (directory or config_directory()).expanduser().absolute()
    selected = connection_name(name) if name is not None else default_name(root)
    candidate = profile_path(root, selected)
    configured = candidate.exists() or candidate.is_symlink() or (root / "default-connection").exists()
    if name is None and access is None and not configured and os.environ.get("UART_GATEWAY_TOKEN"):
        # 相容舊部署；一旦已有固定設定，不能以環境變數遮蔽其錯誤。
        return ConnectionSettings(host or "127.0.0.1", port if port is not None else 18081, explicit_source())
    if not configured:
        raise ValueError("尚未初始化遠端連線，請先使用 uart auth init 或 uart auth import。")
    value = read_profile(root, selected)
    role = access or "read"
    reference = value["credentials"].get(role)
    if reference is None:
        raise ValueError("此連線未配置所選角色的憑證；請明確匯入該角色，不會改用管理憑證。")
    if reference.startswith("file:"):
        path = Path(reference[5:]).expanduser()
        if not path.is_absolute():
            path = candidate / path
        reference = "file:" + str(path)
    destination, number = host or value["host"], port if port is not None else value["port"]
    endpoint(destination, number)
    return ConnectionSettings(destination, number, TokenSource(reference), role, selected)
