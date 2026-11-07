"""完整 Gateway 設定的薄本機啟動入口；沿用原驗證及程序生命週期。"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
import json
import os
from pathlib import Path
import secrets
import tomllib

from .client import Client
from .local_launcher import launch_channels
from .local_profile import LocalChannel, validate_channels
from .serial_backend import list_adapters
from .server.app import assemble, secret
from .remote_workbench import label


def config_text(config: dict) -> str:
    """只序列化已通過原載入器驗證的設定；認證值僅包含秘密參照。"""
    lines = []

    def table(value, prefix, header=None):
        if header:
            lines.append(header)
        for key, item in value.items():
            if not isinstance(item, dict) and not (isinstance(item, list) and item and isinstance(item[0], dict)):
                lines.append(f"{key} = {json.dumps(item, ensure_ascii=False)}")
        for key, item in value.items():
            name = f"{prefix}.{key}" if prefix else key
            if isinstance(item, dict):
                table(item, name, f"[{name}]")
            elif isinstance(item, list) and item and isinstance(item[0], dict):
                for entry in item:
                    table(entry, name, f"[[{name}]]")

    table(config, "")
    return "\n".join(lines) + "\n"


@dataclass
class LaunchPlan:
    config: str
    channels: tuple[LocalChannel, ...]
    port: int
    token: str = field(repr=False)
    grants: list[dict]
    recording: Path | None
    temporary_token: bool


def prepare_config(path: Path, *, record_directory: Path | None = None, no_record: bool = False) -> LaunchPlan:
    try:
        with path.open("rb") as file:
            raw = file.read(65537)
        if len(raw) > 65536:
            raise ValueError("Gateway 設定不得超過 64 KiB。")
        config = tomllib.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("無法讀取 UTF-8 Gateway 設定，請確認檔案及格式。") from exc
    if record_directory is not None and no_record:
        raise ValueError("啟用及停用錄製不能同時指定。")
    auth = config.get("auth")
    entries = auth.get("principals", []) if isinstance(auth, dict) else []
    if not isinstance(entries, list) or len(entries) != 1:
        raise ValueError("此本機啟動入口目前需要一個認證身分；多身分請沿用獨立 Gateway。")
    if no_record:
        config["recording"] = {"enabled": False}
    elif record_directory is not None:
        existing = config.get("recording", {})
        if not isinstance(existing, dict):
            raise ValueError("錄製設定必須為表格。")
        config["recording"] = {**existing, "enabled": True,
                               "directory": str(record_directory.absolute())}
    temporary = False
    generated = secrets.token_urlsafe(32)

    def resolve(reference):
        nonlocal temporary
        if reference == "env:UART_GATEWAY_TOKEN" and not os.environ.get("UART_GATEWAY_TOKEN"):
            temporary = True
            return generated
        try:
            return secret(reference)
        except OSError as exc:
            raise ValueError("無法讀取 Token 參照，請確認檔案存在與讀取權限；未建立 Gateway。") from exc

    gateway, _, port = assemble(config, fake=False, resolve_secret=resolve)
    principal = gateway.principals[0]
    if set(principal.grants) != set(gateway.channels) or any("tx" in p for p in principal.grants.values()):
        raise ValueError("本機 RX 啟動需授權全部目標通道的 read 或 read＋configure，不接受 tx。")
    selected = []
    for channel, item in zip(gateway.channels.values(), config["channels"], strict=True):
        settings = channel.config.serial
        if (settings.baudrate != 115200 or settings.bytesize != 8 or settings.parity != "none" or
                settings.stopbits != 1 or settings.flow_control != "none" or settings.dtr or settings.rts):
            raise ValueError("本機整合入口目前使用 115200／8N1／無流控、DTR／RTS false；其他設定請沿用獨立 Gateway。")
        if not channel.config.auto_open_rx:
            raise ValueError("整合啟動的目標須設定 auto_open_rx=true。")
        selector = channel.port.selector
        if not selector.port:
            selector = replace(selector, port=selector.resolve(list_adapters()).port)
            item["adapter"]["port"] = selector.port
        selected.append(LocalChannel(channel.config.id, channel.config.label, selector))
    channels = tuple(selected)
    validate_channels(channels)
    recording = config.get("recording", {})
    directory = Path(recording["directory"]) if recording.get("enabled") else None
    grants = [{"channel_id": cid, "permissions": sorted(p)} for cid, p in sorted(principal.grants.items())]
    return LaunchPlan(config_text(config), channels, port, principal.token, grants, directory, temporary)


async def launch_config(path: Path, monitor: Callable[..., Awaitable[int]], *,
                        record_directory: Path | None = None, no_record: bool = False,
                        startup_timeout: float = 20, capture_ready: Callable | None = None) -> int:
    plan = await asyncio.to_thread(prepare_config, path, record_directory=record_directory, no_record=no_record)
    print(f"使用 Gateway 設定：{label(path)}；錄製：{label(plan.recording or '停用')}。", flush=True)
    if plan.temporary_token:
        print("本次使用臨時憑證，僅供此本機工作區使用；結束時清理本次 Gateway。", flush=True)

    async def observe(clients, channels):
        snapshots = []
        for client, channel in zip(clients, channels, strict=True):
            snapshot = await client.get_channel(channel.id)
            if ("channel.reopen_rx" in client.hello.get("capabilities", []) and
                    "configure" in next(g["permissions"] for g in plan.grants if g["channel_id"] == channel.id)):
                snapshot["reopen_rx_available"] = True
            snapshots.append(snapshot)
        instance = clients[0].instance
        await asyncio.gather(*(client.close() for client in clients))

        async def connect():
            return await Client.connect("127.0.0.1", plan.port, plan.token)

        return await monitor(connect, snapshots, instance)

    return await launch_channels(plan.channels, observe, gateway_port=plan.port,
        startup_timeout=startup_timeout, record_directory=plan.recording,
        prepared_config=plan.config, token=plan.token, expected_grants=plan.grants,
        **({"capture_ready": capture_ready} if capture_ready else {}))
