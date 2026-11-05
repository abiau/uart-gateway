"""只讀本機啟動器：管理自己的程序，所有 UART 操作經既有 SDK。"""

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading

from .client import Client
from .local_profile import LocalChannel, channel_tables, validate_channels
from .protocol.errors import ProtocolError, RemoteError
from .serial_backend import AdapterSelector, port_key

CHANNEL_ID = "ch_uart_1"


class LauncherError(ValueError):
    code = "LOCAL_START_FAILED"


class LauncherCleanupError(LauncherError):
    code = "LOCAL_CLEANUP_FAILED"


def validate_local_settings(gateway_port: int, startup_timeout: float) -> None:
    if type(gateway_port) is not int or not 1024 <= gateway_port <= 65535:
        raise LauncherError("Gateway Port 必須為 1024 至 65535。")
    if not math.isfinite(startup_timeout) or not 0 < startup_timeout <= 300:
        raise LauncherError("就緒等待時間必須大於零且不超過 300 秒。")


class PortLock:
    """作業系統持有的程序鎖；不刪除鎖檔來避免 inode 競爭。"""

    def __init__(self, port: str, directory: Path | None = None):
        base = (Path(os.environ["LOCALAPPDATA"]) / "uart-gateway" if os.name == "nt" and "LOCALAPPDATA" in os.environ
                else Path(tempfile.gettempdir()) / f"uart-gateway-{getattr(os, 'getuid', lambda: 'local')()}")
        self.directory = directory or base / "locks"
        self.path = self.directory / (hashlib.sha256(port_key(port).encode("utf-8")).hexdigest() + ".lock")
        self.file = None

    def __enter__(self) -> "PortLock":
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            raise LauncherError("無法建立本機啟動鎖，請確認使用者暫存目錄可寫入。") from exc
        self.file = os.fdopen(descriptor, "r+b", buffering=0)
        try:
            if os.fstat(descriptor).st_size == 0:
                self.file.write(b"\0")
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            self.file = None
            raise LauncherError("此端點已有本機啟動器執行中，請使用原視窗；不會重複啟動。") from exc
        return self

    def __exit__(self, *_: object) -> None:
        if self.file is not None:
            self.file.close()
            self.file = None


def _profile(port: str, gateway_port: int, selector: AdapterSelector | None = None) -> str:
    return _gateway_profile((LocalChannel(CHANNEL_ID, "local_uart", selector or AdapterSelector(port=port)),), gateway_port)


def _gateway_profile(channels: tuple[LocalChannel, ...], gateway_port: int) -> str:
    """只有秘密參照；UART 預設仍由原設定載入器提供。"""
    return f'''[network]
bind_host = "127.0.0.1"
port = {gateway_port}
{channel_tables(channels, gateway=True)}
[[auth.principals]]
id = "local_observer"
token_secret_ref = "env:UART_GATEWAY_TOKEN"
channels = {json.dumps([channel.id for channel in channels])}
permissions = ["read"]
'''


def _check_listener(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise LauncherError(f"127.0.0.1:{port} 已被使用，可能已有 Gateway；不會接管或關閉它。請自行處理既有服務，或指定 --gateway-port。") from exc


def process_options(platform: str) -> dict:
    if platform == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)}
    return {"start_new_session": True}


class OwnedGateway:
    """只清理此物件建立的 Popen；沒有依名稱或 Port 終止其他程序。"""

    def __init__(self, config: Path, token: str, *, reporter: Callable[[str], None]):
        self.command = [sys.executable, "-m", "uart_gateway.server.app", "--serial",
                        "--config", str(config), "--shutdown-on-stdin-eof"]
        self.token, self.reporter = token, reporter
        self.process: subprocess.Popen | None = None
        self.logs: deque[str] = deque(maxlen=10)
        self.reader: threading.Thread | None = None

    def start(self) -> None:
        environment = os.environ.copy()
        environment.update(UART_GATEWAY_TOKEN=self.token, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        options = process_options(os.name)
        try:
            self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=environment,
                encoding="utf-8", errors="replace", **options)
        except OSError as exc:
            raise LauncherError("無法啟動 Gateway 子程序，請確認 Python 與專案已正確安裝。") from exc
        self.reader = threading.Thread(target=self._read_logs, daemon=True)
        self.reader.start()

    def _read_logs(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        while line := self.process.stdout.readline(4096):
            line = line.replace(self.token, "[已隱藏憑證]").rstrip("\r\n")
            self.logs.append(line)
            self.reporter(f"Gateway：{line}")

    def stop(self, grace: float = 12) -> None:
        if self.process is None:
            return
        if self.process.stdin is not None:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        try:
            self.process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            self.reporter("Gateway 未在期限內結束，正在停止本次建立的子程序。")
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired as exc:
                    raise LauncherError("無法確認本次 Gateway 已結束，請檢查該程序；不會停止其他程序。") from exc
        if self.reader is not None:
            self.reader.join(timeout=1)
        if self.process.stdout is not None:
            self.process.stdout.close()


def _channel_failure(channel: dict, port: str) -> LauncherError:
    error = channel.get("last_error") or {}
    code = error.get("code", "CHANNEL_NOT_READY")
    if code == "PORT_BUSY":
        return LauncherError(f"{port} 無法獨占開啟：可能仍被 MobaXterm 或其他程式占用，或權限不足。請先關閉該程式的 Serial 連線；啟動器不會強制結束對方。")
    if code == "DEVICE_MISSING":
        return LauncherError(f"找不到 {port}，請確認 USB-UART 已連接及 COM 名稱正確。")
    return LauncherError(f"{port} 尚未就緒（{code}）：{error.get('message', '請檢查裝置、Driver 與 UART 設定。')}")


async def wait_ready(owned: OwnedGateway, port: str, gateway_port: int, timeout: float,
                     selector: AdapterSelector | None = None, *, channel_id: str = CHANNEL_ID,
                     grants: list[dict] | None = None) -> Client:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if owned.process is None or owned.process.poll() is not None:
            raise LauncherError("Gateway 在就緒前已結束，請查看上方啟動訊息。")
        client = None
        handed_off = False
        try:
            async with asyncio.timeout(min(1, deadline - loop.time())):
                client = await Client.connect("127.0.0.1", gateway_port, owned.token)
                channel = await client.get_channel(channel_id)
                expected = grants if grants is not None else [{"channel_id": channel_id, "permissions": ["read"]}]
                if sorted(client.auth["grants"], key=lambda g: g["channel_id"]) != sorted(expected, key=lambda g: g["channel_id"]):
                    raise LauncherError("Gateway 的權限不是預期的只讀設定，已停止本次啟動。")
                if channel["state"] == "ready":
                    actual_port = channel["adapter"].get("port")
                    if not actual_port or port_key(actual_port) != port_key(port):
                        raise LauncherError("Gateway 就緒端點與指定端點不同，已停止本次啟動。")
                    if selector is not None and any(value is not None and channel["adapter"].get(name) != value
                            for name, value in vars(selector).items() if name != "port"):
                        raise LauncherError("Gateway 裝置資訊與所選端點不同，已停止本次啟動，請重新選擇。")
                    settings = channel["serial"]
                    if (settings["dtr"] or settings["rts"] or settings["baudrate"] != 115200
                            or settings["bytesize"] != 8 or settings["parity"] != "none"
                            or settings["stopbits"] != 1 or settings["flow_control"] != "none"):
                        raise LauncherError("Gateway UART 設定與本機只讀模式不符，已停止本次啟動。")
                    handed_off = True
                    return client
                if channel["state"] in ("faulted", "lost", "closed"):
                    raise _channel_failure(channel, port)
        except (OSError, TimeoutError):
            pass
        except (RemoteError, ProtocolError) as exc:
            raise LauncherError("無法完成本次 Gateway 認證或協定協商，不會接管其他 Gateway。") from exc
        finally:
            # 成功回傳時由 launch_local 接手連線；其餘嘗試都立即釋放。
            if client is not None and not handed_off:
                await client.close()
        await asyncio.sleep(0.05)
    raise LauncherError(f"等待 {port} 與 Gateway 就緒逾時，請檢查啟動訊息及裝置。")


async def _exited(owned: OwnedGateway) -> None:
    while owned.process is not None and owned.process.poll() is None:
        await asyncio.sleep(0.05)


async def launch_local(port: str, monitor: Callable[[Client, str], Awaitable[int]], *,
                       gateway_port: int = 18081, startup_timeout: float = 20,
                       lock_directory: Path | None = None,
                       selector: AdapterSelector | None = None,
                       reporter: Callable[[str], None] | None = None) -> int:
    if not isinstance(port, str) or not 1 <= len(port.encode("utf-8")) <= 256 or not port.isprintable():
        raise LauncherError("Serial 端點名稱不合法。")
    validate_local_settings(gateway_port, startup_timeout)
    if selector is not None and (not selector.port or port_key(selector.port) != port_key(port)):
        raise LauncherError("所選裝置的端點與啟動端點不一致。")
    channel = LocalChannel(CHANNEL_ID, "local_uart", selector or AdapterSelector(port=port))

    async def single(clients: tuple[Client, ...], channels: tuple[LocalChannel, ...]) -> int:
        return await monitor(clients[0], channels[0].id)

    return await launch_channels((channel,), single, gateway_port=gateway_port,
        startup_timeout=startup_timeout, lock_directory=lock_directory, reporter=reporter)


async def launch_channels(channels: tuple[LocalChannel, ...],
                          monitor: Callable[[tuple[Client, ...], tuple[LocalChannel, ...]], Awaitable[int]], *,
                          gateway_port: int = 18081, startup_timeout: float = 20,
                          lock_directory: Path | None = None,
                          reporter: Callable[[str], None] | None = None) -> int:
    validate_channels(channels)
    validate_local_settings(gateway_port, startup_timeout)
    report = reporter or (lambda message: print(message, file=sys.stderr, flush=True))
    ports = "、".join(channel.adapter.port for channel in channels)
    with ExitStack() as locks:
        for channel in sorted(channels, key=lambda c: port_key(c.adapter.port)):
            locks.enter_context(PortLock(channel.adapter.port, lock_directory))
        _check_listener(gateway_port)
        with tempfile.TemporaryDirectory(prefix="uart-gateway-local-") as directory:
            profile = Path(directory) / "gateway.toml"
            profile.write_text(_gateway_profile(channels, gateway_port), encoding="utf-8")
            owned = OwnedGateway(profile, secrets.token_urlsafe(32), reporter=report)
            clients: list[Client] = []
            tasks: list[asyncio.Task] = []
            try:
                report(f"正在啟動 {ports} 的只讀 Gateway，等待認證與全部 Channel 就緒……")
                owned.start()
                report(f"本次建立的 Gateway 程序：PID {owned.process.pid}")
                deadline = asyncio.get_running_loop().time() + startup_timeout
                grants = [{"channel_id": c.id, "permissions": ["read"]} for c in channels]
                for channel in channels:
                    options = {} if len(channels) == 1 and channel.id == CHANNEL_ID else {
                        "channel_id": channel.id, "grants": grants}
                    client = await wait_ready(owned, channel.adapter.port, gateway_port,
                        max(0, deadline - asyncio.get_running_loop().time()), channel.adapter, **options)
                    clients.append(client)
                    if client.instance != clients[0].instance:
                        raise LauncherError("監控連線的 Gateway Instance 不一致，已停止本次啟動。")
                report(f"{ports} 已就緒；只讀 RX 監控中，按 Ctrl+C 關閉本次 Gateway。")
                tasks = [asyncio.create_task(monitor(tuple(clients), channels)), asyncio.create_task(_exited(owned))]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                if tasks[1] in done:
                    raise LauncherError("本次 Gateway 已結束，UART 監控停止。")
                return await tasks[0]
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                try:
                    await asyncio.gather(*(client.close() for client in clients))
                finally:
                    try:
                        await asyncio.to_thread(owned.stop)
                    except (OSError, LauncherError) as exc:
                        raise LauncherCleanupError("無法確認本次 Gateway 已清理；請檢查該程序，停止本次啟動，不會建立另一個 Gateway。") from exc
                if owned.process is not None:
                    report("已清理本次啟動的 Gateway；其他程序未受影響。")
