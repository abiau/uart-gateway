"""工作台的無秘密偏好與本次程序回報；不猜測最新錄製。"""

from dataclasses import dataclass, field
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import tempfile


@dataclass(frozen=True)
class StartChoice:
    config: Path = Path("examples/gateway.com3.toml")
    recording: str = "inherit"
    profile: Path | None = None

    def __post_init__(self):
        if self.recording not in ("inherit", "on", "off"):
            raise ValueError("錄製偏好僅接受沿用、啟用或停用。")
        if not str(self.config).isprintable() or len(str(self.config)) > 4096:
            raise ValueError("設定檔路徑不合法。")
        if self.profile is not None and (not str(self.profile).isprintable() or len(str(self.profile)) > 4096):
            raise ValueError("端點配置路徑不合法。")


def load_choice(path: Path) -> StartChoice:
    if path.is_symlink():
        raise ValueError("偏好檔不得是符號連結。")
    if not path.exists():
        return StartChoice()
    try:
        with path.open("rb") as stream:
            raw = stream.read(8193)
        if len(raw) > 8192:
            raise ValueError
        value = json.loads(raw)
        if (not isinstance(value, dict) or not {"version", "config", "recording"} <= set(value)
                or set(value) - {"version", "config", "recording", "profile"}
                or type(value["version"]) is not int or value["version"] != 1
                or not isinstance(value["config"], str) or not value["config"]
                or not isinstance(value["recording"], str)
                or ("profile" in value and (not isinstance(value["profile"], str) or not value["profile"]))):
            raise ValueError
        return StartChoice(Path(value["config"]), value["recording"], Path(value["profile"]) if "profile" in value else None)
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        raise ValueError("無法讀取工作台偏好；本次使用預設且不覆寫該檔，請檢查檔案格式與權限。") from exc


def save_choice(path: Path, choice: StartChoice) -> None:
    # 不覆寫其他格式的檔案，不保存 Token、秘密內容、UART 資料或搜尋結果。
    load_choice(path)
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            value = {"version": 1, "config": str(choice.config.absolute()), "recording": choice.recording}
            if choice.profile is not None:
                value["profile"] = str(choice.profile.absolute())
            json.dump(value, stream, ensure_ascii=False)
            stream.write("\n")
        temporary.replace(path)
    except OSError as exc:
        raise ValueError("無法保存上次設定；本次仍可啟動，請確認偏好檔目錄與權限。") from exc
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def choice_config(choice: StartChoice):
    if choice.profile is None:
        yield choice.config
        return
    from .local_profile import load_profile, resolve_profile
    from .local_launcher import _gateway_profile
    channels = resolve_profile(load_profile(choice.profile))
    with tempfile.TemporaryDirectory(prefix="uart-workbench-") as temporary:
        path = Path(temporary) / "gateway.toml"
        path.write_text(_gateway_profile(channels, 18081), encoding="utf-8")
        yield path


@dataclass
class WorkbenchSession:
    capture_channels: tuple[Path, ...] = ()
    search_result: dict | None = field(default=None, repr=False)

    def capture_ready(self, root: Path, instance: str, channels: tuple) -> None:
        self.capture_channels = tuple(root / ("capture-" + instance) / c.id for c in channels)

    def search_ready(self, result: dict) -> None:
        self.search_result = result
