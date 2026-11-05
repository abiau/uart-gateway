"""Gateway 組裝入口；明確選擇模擬或實體 Serial 模式。"""

import argparse
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import threading
import tomllib

from uart_gateway.core.models import ChannelConfig, DomainError, SerialSettings
from uart_gateway.fake_serial import FakeSerial
from uart_gateway.serial_backend import AdapterSelector, PySerialBackend, list_adapters
from uart_gateway.protocol.messages import identifier, text
from uart_gateway.protocol.errors import ProtocolError
from .auth import Principal
from .tcp import Gateway


def _keys(value: object, required: set[str], optional: set[str] = frozenset()) -> None:
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - optional:
        raise ValueError("設定缺漏或包含此切片尚未支援的欄位")


def secret(reference: str) -> str:
    if reference.startswith("env:"):
        value = os.environ.get(reference[4:], "")
    elif reference.startswith("file:"):
        value = Path(reference[5:]).read_text(encoding="utf-8").strip()
    else:
        raise ValueError("秘密參照必須使用 env: 或 file:")
    if not value:
        raise ValueError("未取得認證資料")
    return value


def load(path: Path, *, fake: bool = True) -> tuple[Gateway, str, int]:
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    _keys(config, {"channels", "auth"}, {"network", "gateway"})
    gateway_config = config.get("gateway", {})
    _keys(gateway_config, set(), {"lifecycle_timeout_ms"})
    lifecycle_ms = gateway_config.get("lifecycle_timeout_ms", 10000)
    if type(lifecycle_ms) is not int or not 1000 <= lifecycle_ms <= 60000:
        raise ValueError("生命週期等待期限必須為 1000 至 60000 ms")
    network = config.get("network", {})
    _keys(network, set(), {"bind_host", "port"})
    host, port = network.get("bind_host", "127.0.0.1"), network.get("port", 18081)
    if host != "127.0.0.1" or type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("僅允許 Loopback 與 1024 至 65535 的 Port")
    channels = []
    if not isinstance(config["channels"], list) or not 1 <= len(config["channels"]) <= 4:
        raise ValueError("必須設定 1 至 4 個通道")
    for item in config["channels"]:
        _keys(item, {"id", "label"}, {"target_note", "adapter", "serial", "auto_open_rx", "auto_reconnect"})
        identifier(item["id"])
        text(item["label"], 128)
        if item.get("target_note") is not None:
            text(item["target_note"], 512, 0)
        auto_open = item.get("auto_open_rx", True)
        if type(auto_open) is not bool or type(item.get("auto_reconnect", False)) is not bool:
            raise ValueError("自動開啟／重連設定必須為布林值")
        if item.get("auto_reconnect", False):
            raise ValueError("此版本尚未提供自動重連，請使用 Rebind／Confirm")
        if fake:
            if "adapter" in item or "serial" in item:
                raise ValueError("模擬模式不得包含實體 Adapter 或 Serial 設定")
            settings, backend = SerialSettings(), FakeSerial()
        else:
            _keys(item.get("adapter"), set(), {"port", "vid", "pid", "serial_number", "location"})
            _keys(item.get("serial", {}), set(), set(SerialSettings.__dataclass_fields__))
            selector = AdapterSelector(**item["adapter"])
            settings = SerialSettings(**item.get("serial", {}))
            backend = PySerialBackend(selector, settings)
        channels.append((ChannelConfig(item["id"], item["label"], item.get("target_note"),
                                       settings, auto_open), backend))
    _keys(config["auth"], {"principals"})
    entries = config["auth"]["principals"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= 128:
        raise ValueError("必須設定有限的認證身分")
    principals = []
    for entry in entries:
        _keys(entry, {"id", "token_secret_ref", "channels", "permissions"})
        if (not isinstance(entry["token_secret_ref"], str) or
                not isinstance(entry["channels"], list) or not isinstance(entry["permissions"], list)):
            raise ValueError("認證設定型別不合法")
        for cid in entry["channels"]:
            identifier(cid)
        if not all(isinstance(permission, str) for permission in entry["permissions"]):
            raise ValueError("認證權限型別不合法")
        principals.append(Principal(entry["id"], secret(entry["token_secret_ref"]),
            {cid: frozenset(entry["permissions"]) for cid in entry["channels"]}))
    return Gateway(channels, tuple(principals), lifecycle_timeout=lifecycle_ms / 1000), host, port


async def run(path: Path, *, fake: bool = True, shutdown_on_stdin_eof: bool = False) -> None:
    gateway, host, port = load(path, fake=fake)
    try:
        await gateway.start(host, port)
        mode = "模擬（Fake Serial，未開啟 COM）" if fake else "實體 Serial（尚未 Confirm，不允許 TX）"
        print(f"Gateway 已接聽 {host}:{port}；模式：{mode}。", flush=True)
        shutdown = asyncio.Event()
        if shutdown_on_stdin_eof:
            loop = asyncio.get_running_loop()

            def watch_parent() -> None:
                try:
                    while sys.stdin.buffer.read(1):
                        pass
                finally:
                    try:
                        loop.call_soon_threadsafe(shutdown.set)
                    except RuntimeError:
                        pass

            threading.Thread(target=watch_parent, daemon=True).start()
        await shutdown.wait()
    finally:
        await gateway.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="UART Gateway；預設僅接收，TX 需權限、Confirm 與 Lease")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--fake", action="store_true", help="選擇 Fake Serial")
    modes.add_argument("--serial", action="store_true", help="選擇實體 Serial")
    modes.add_argument("--list-ports", action="store_true", help="只枚舉裝置，不開啟 COM")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--shutdown-on-stdin-eof", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.list_ports:
            print(json.dumps([asdict(info) for info in list_adapters()], ensure_ascii=True))
            return
        if args.config is None:
            parser.error("啟動 Gateway 需要 --config")
        asyncio.run(run(args.config, fake=args.fake, shutdown_on_stdin_eof=args.shutdown_on_stdin_eof))
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, ProtocolError, DomainError) as exc:
        parser.exit(1, f"啟動失敗：{exc}\n")


if __name__ == "__main__":
    main()
