"""pySerial 裝置實作；匯入本模組不開啟 COM 或載入 pySerial。"""

from dataclasses import dataclass, replace
import errno
import importlib
import os
import re
import threading

from .core.models import AdapterInfo, SerialSettings


class SerialBackendError(OSError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def port_key(port: str) -> str:
    value = port.removeprefix("\\\\.\\")
    if re.fullmatch(r"COM[0-9]+", value, re.IGNORECASE):
        return "COM" + str(int(value[3:]))
    return os.path.realpath(port) if os.name == "posix" else port.casefold()


@dataclass(frozen=True)
class AdapterSelector:
    port: str | None = None
    vid: int | None = None
    pid: int | None = None
    serial_number: str | None = None
    location: str | None = None

    def __post_init__(self) -> None:
        if not any(value is not None for value in (self.port, self.vid, self.serial_number, self.location)):
            raise ValueError("必須明確指定 Adapter 條件，不能自動選第一個裝置")
        for name, maximum in (("port", 256), ("serial_number", 128), ("location", 256)):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not 1 <= len(value.encode("utf-8")) <= maximum):
                raise ValueError(f"Adapter {name} 不合法")
        if (self.vid is None) != (self.pid is None):
            raise ValueError("VID／PID 必須成對提供")
        for value in (self.vid, self.pid):
            if value is not None and (type(value) is not int or not 0 <= value <= 65535):
                raise ValueError("VID／PID 必須是 0 至 65535 的整數")

    def resolve(self, devices: list[AdapterInfo]) -> AdapterInfo:
        matches = [device for device in devices if all(
            wanted is None or (port_key(device.port) == port_key(wanted) if name == "port" and device.port
                               else getattr(device, name) == wanted)
            for name, wanted in vars(self).items())]
        if len(matches) > 1:
            raise SerialBackendError("AMBIGUOUS_ADAPTER", "有多個 Adapter 符合條件，請指定唯一裝置。")
        if matches:
            return replace(matches[0], match_status="unique" if matches[0].serial_number else "unverified")
        # 明確端點可能不在枚舉清單（例如虛擬端點）；只在沒有其他條件時允許直接開啟。
        if self.port and all(value is None for value in (self.vid, self.pid, self.serial_number, self.location)):
            return AdapterInfo(port=self.port)
        raise SerialBackendError("DEVICE_MISSING", "找不到符合全部條件的 Adapter。")


def _serial_module():
    try:
        return importlib.import_module("serial")
    except ImportError as exc:
        raise SerialBackendError("SERIAL_IO_FAILED", "尚未安裝 pySerial，請安裝專案的 [server] 選用依賴。") from exc


@dataclass(frozen=True)
class AdapterDetails:
    """本機顯示資訊；描述不加入 Core 模型或線上快照。"""

    adapter: AdapterInfo
    description: str | None = None


def list_adapter_details() -> list[AdapterDetails]:
    _serial_module()
    try:
        ports = importlib.import_module("serial.tools.list_ports").comports()
        return sorted((AdapterDetails(AdapterInfo(port=p.device, vid=p.vid, pid=p.pid,
                        serial_number=p.serial_number, location=p.location),
                        getattr(p, "description", None)) for p in ports),
                      key=lambda info: info.adapter.port or "")
    except OSError as exc:
        raise SerialBackendError("SERIAL_IO_FAILED", "無法枚舉 Serial 裝置。") from exc


def list_adapters() -> list[AdapterInfo]:
    return [entry.adapter for entry in list_adapter_details()]


def _open_error(exc: OSError) -> SerialBackendError:
    code, winerror, message = getattr(exc, "errno", None), getattr(exc, "winerror", None), str(exc)
    if (code in (errno.EACCES, errno.EBUSY, errno.EPERM, errno.EAGAIN) or winerror in (5, 32)
            or "PermissionError" in message or "exclusively lock port" in message):
        return SerialBackendError("PORT_BUSY", "無法獨占開啟端點：可能被其他程式占用或權限不足，請先關閉 MobaXterm 等程式的 COM 連線。")
    if code in (errno.ENOENT, errno.ENODEV) or winerror in (2, 3) or "FileNotFoundError" in message:
        return SerialBackendError("DEVICE_MISSING", "指定的 Serial 端點不存在。")
    return SerialBackendError("SERIAL_IO_FAILED", "無法開啟 Serial，請檢查裝置、Driver 與 UART 設定。")


_reservations: set[str] = set()
_reservation_lock = threading.Lock()


class PySerialBackend:
    def __init__(self, selector: AdapterSelector, settings: SerialSettings):
        self.selector, self.settings = selector, settings
        self._serial = None
        self._key: str | None = None
        self._info = AdapterInfo()
        self._identity: AdapterInfo | None = None

    def describe(self) -> AdapterInfo:
        """僅讀快照，不在 Event Loop 枚舉或呼叫 Driver。"""
        return self._info

    def open(self) -> None:
        self._open()

    def reopen_rx(self) -> None:
        self._open(self._identity)

    def set_settings(self, settings: SerialSettings) -> None:
        if self._serial is not None:
            raise RuntimeError("變更設定前必須關閉 Serial Handle。")
        self.settings = settings

    def _open(self, expected: AdapterInfo | None = None) -> None:
        if self._serial is not None:
            raise SerialBackendError("PORT_BUSY", "此 Backend 尚有未釋放的 Handle，請先關閉。")
        module = _serial_module()
        info = self.selector.resolve(list_adapters())
        if expected is not None:
            names = ("vid", "pid", "serial_number") if expected.serial_number else ("vid", "pid", "location")
            if any(getattr(expected, name) is not None and
                   getattr(expected, name) != getattr(info, name) for name in names):
                raise SerialBackendError("DEVICE_MISSING", "Adapter 身分與前次成功開啟不符，未開啟端點；請核對 USB／COM 與原設定。")
        self._info = info
        key = port_key(self._info.port)
        with _reservation_lock:
            if key in _reservations:
                raise SerialBackendError("PORT_BUSY", "另一個 Channel 已使用同一個實體端點。")
            _reservations.add(key)
            self._key = key
        try:
            settings = self.settings
            kwargs = {"port": None, "baudrate": settings.baudrate, "bytesize": settings.bytesize,
                      "parity": {"none": "N", "even": "E", "odd": "O", "mark": "M", "space": "S"}[settings.parity],
                      "stopbits": settings.stopbits, "timeout": settings.read_timeout_ms / 1000,
                      "write_timeout": settings.write_timeout_ms / 1000,
                      "rtscts": settings.flow_control == "rtscts",
                      "xonxoff": settings.flow_control == "xonxoff", "dsrdtr": False}
            if os.name == "posix":
                kwargs["exclusive"] = True
            handle = module.Serial(**kwargs)
            self._serial = handle
            # 先設定控制線意圖再 open；仍不能保證 Driver 沒有瞬間脈衝。
            handle.dtr, handle.rts = settings.dtr, settings.rts
            handle.port = self._info.port
            handle.open()
            self._identity = info
        except (OSError, ValueError) as exc:
            try:
                self.close()
            except OSError:
                pass
            if isinstance(exc, OSError):
                raise _open_error(exc) from exc
            raise SerialBackendError("SERIAL_IO_FAILED", "Driver 不接受指定的 Serial 設定。") from exc

    def read(self, size: int) -> bytes:
        if self._serial is None:
            raise SerialBackendError("DEVICE_MISSING", "Serial 端點已關閉。")
        try:
            return self._serial.read(size)
        except OSError as exc:
            self._info = replace(self._info, match_status="missing")
            raise SerialBackendError("DEVICE_MISSING", "Serial 接收失敗，裝置可能已拔除。") from exc

    def write(self, data: bytes) -> int:
        if self._serial is None:
            raise SerialBackendError("DEVICE_MISSING", "Serial 端點已關閉。")
        try:
            return self._serial.write(data)
        except _serial_module().SerialTimeoutException as exc:
            raise TimeoutError("Serial 寫入逾時，可能已送出部分資料。") from exc
        except OSError as exc:
            raise SerialBackendError("SERIAL_IO_FAILED", "Serial 寫入失敗，結果不明。") from exc

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except OSError as exc:
                # 失敗時保留 Handle 與端點預留，禁止另一個 Channel 接手。
                raise SerialBackendError("SERIAL_IO_FAILED", "Serial Handle 關閉失敗。") from exc
            self._serial = None
        with _reservation_lock:
            if self._key is not None:
                _reservations.discard(self._key)
                self._key = None
