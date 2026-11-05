"""本機只讀配置：只保存名稱與裝置條件，不保存憑證或工作階段。"""

from dataclasses import dataclass, replace
import json
from pathlib import Path
import tomllib

from .protocol.messages import identifier
from .serial_backend import AdapterSelector, list_adapters, port_key


@dataclass(frozen=True)
class LocalChannel:
    id: str
    label: str
    adapter: AdapterSelector


def validate_channels(channels: tuple[LocalChannel, ...]) -> None:
    if not 1 <= len(channels) <= 2:
        raise ValueError("本機配置目前支援 1 至 2 條 UART。")
    for channel in channels:
        identifier(channel.id)
        if (not isinstance(channel.label, str) or not channel.label.strip()
                or len(channel.label.encode("utf-8")) > 128 or not channel.label.isprintable()):
            raise ValueError("通道名稱須為 1 至 128 UTF-8 bytes 的可顯示文字。")
        port = channel.adapter.port
        if not port or not port.isprintable():
            raise ValueError("本機配置必須包含有效的 Serial 端點名稱。")
    for values, message in (
        ([c.id for c in channels], "通道 ID 重複。"),
        ([c.label for c in channels], "通道名稱重複。"),
        ([port_key(c.adapter.port) for c in channels], "兩條通道不能使用相同端點。"),
    ):
        if len(set(values)) != len(values):
            raise ValueError(message)


def channel_tables(channels: tuple[LocalChannel, ...], *, gateway: bool = False) -> str:
    """產生原載入器使用的 TOML 通道欄位。"""
    parts = []
    for channel in channels:
        parts.append("\n[[channels]]\n")
        for key in ("id", "label"):
            parts.append(f"{key} = {json.dumps(getattr(channel, key), ensure_ascii=False)}\n")
        if gateway:
            parts.append("auto_open_rx = true\nauto_reconnect = false\n")
        parts.append("[channels.adapter]\n")
        for key, value in vars(channel.adapter).items():
            if value is not None:
                parts.append(f"{key} = {json.dumps(value, ensure_ascii=False)}\n")
        if gateway:
            parts.append("[channels.serial]\ndtr = false\nrts = false\n")
    return "".join(parts)


def save_profile(path: Path, channels: tuple[LocalChannel, ...]) -> None:
    validate_channels(channels)
    content = "version = 1\n" + channel_tables(channels)
    # 明確指定新檔案才保存；既有配置不會因重新選擇而被覆蓋。
    try:
        with path.open("x", encoding="utf-8") as file:
            file.write(content)
    except FileExistsError as exc:
        raise ValueError("配置檔已存在，請指定新的檔名；未覆蓋原配置。") from exc
    except OSError as exc:
        raise ValueError("無法保存本機配置，請確認目錄存在、寫入權限及磁碟空間；請檢查檔案是否完整。") from exc


def load_profile(path: Path) -> tuple[LocalChannel, ...]:
    try:
        with path.open("rb") as file:
            raw = file.read(65537)
    except OSError as exc:
        raise ValueError("無法讀取本機配置檔，請確認路徑及讀取權限。") from exc
    if len(raw) > 65536:
        raise ValueError("本機配置檔不得超過 64 KiB。")
    try:
        config = tomllib.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("本機配置不是有效的 UTF-8 TOML。") from exc
    if (set(config) != {"version", "channels"} or type(config["version"]) is not int
            or config["version"] != 1 or not isinstance(config["channels"], list)):
        raise ValueError("本機配置必須包含 version = 1 與 channels；不接受認證或其他設定。")
    channels = []
    for item in config["channels"]:
        if (not isinstance(item, dict) or set(item) != {"id", "label", "adapter"}
                or not isinstance(item["adapter"], dict)
                or set(item["adapter"]) - set(AdapterSelector.__dataclass_fields__)):
            raise ValueError("本機通道僅接受 id、label 與 adapter 裝置條件。")
        channels.append(LocalChannel(item["id"], item["label"], AdapterSelector(**item["adapter"])))
    result = tuple(channels)
    validate_channels(result)
    return result


def resolve_profile(channels: tuple[LocalChannel, ...]) -> tuple[LocalChannel, ...]:
    """有序號才允許重找 COM；所有其他已存條件仍取交集。"""
    validate_channels(channels)
    devices = list_adapters()
    resolved = []
    for channel in channels:
        selector = channel.adapter
        if selector.serial_number:
            selector = replace(selector, port=None)
        try:
            actual = selector.resolve(devices)
            # 配置重用必須真的枚舉到；不使用明確端點的 Backend 回退。
            if not any(device.port and port_key(device.port) == port_key(actual.port) for device in devices):
                raise ValueError("目前枚舉清單中沒有此端點")
        except (OSError, ValueError) as exc:
            raise ValueError(f"[{channel.id}／{channel.label}] 裝置無法唯一核對，請用 --select 重新選擇。原因：{exc}") from exc
        adapter = AdapterSelector(port=actual.port,
            vid=actual.vid if actual.pid is not None else None,
            pid=actual.pid if actual.vid is not None else None,
            serial_number=actual.serial_number, location=actual.location)
        resolved.append(replace(channel, adapter=adapter))
    result = tuple(resolved)
    validate_channels(result)
    return result
