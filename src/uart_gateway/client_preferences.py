"""依固定部署及角色保存無秘密的通道選擇；每次仍須查詢與人工選定。"""

import json
import re
from pathlib import Path

from .protected_files import read_private, write_private


def preference_path(root: Path, settings) -> Path | None:
    deployment = getattr(settings, "deployment_id", None)
    if deployment is None:
        return None
    if not re.fullmatch(r"[a-f0-9]{32}", deployment) or settings.access not in ("read", "manage", "tx"):
        raise ValueError("通道偏好需要有效的固定部署與角色。")
    return root / "client-preferences" / f"{deployment}-{settings.access}.json"


def validate_selection(value: object) -> list[str]:
    if (not isinstance(value, list) or not 1 <= len(value) <= 4
            or any(not isinstance(cid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", cid) for cid in value)
            or len(set(value)) != len(value)):
        raise ValueError("通道偏好須為一至四個不同的 Channel ID。")
    return value


def load_selection(path: Path | None) -> list[str] | None:
    if path is None or not (path.exists() or path.is_symlink()):
        return None
    try:
        value = json.loads(read_private(path, 2048))
        if not isinstance(value, dict) or set(value) != {"version", "channels"} or type(value["version"]) is not int or value["version"] != 1:
            raise ValueError
        return validate_selection(value["channels"])
    except (ValueError, TypeError, RecursionError):
        raise ValueError("無法讀取上次通道選擇；請重新選擇，本次不覆寫有問題的偏好檔。") from None


def save_selection(path: Path | None, channels: list[str]) -> None:
    if path is None:
        return
    load_selection(path)
    data = json.dumps({"version": 1, "channels": validate_selection(channels)}, separators=(",", ":")).encode()
    write_private(path, data, replace=True)
