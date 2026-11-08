"""明確選用的模擬裝置；不會開啟實體 COM。"""

from collections import deque
import queue
import threading

from .core.models import AdapterInfo, SerialSettings


class FakeSerial:
    def __init__(self, *, echo: bool = True):
        self.echo = echo
        self.rx: queue.Queue[bytes] = queue.Queue(maxsize=64)
        self.writes: deque[bytes] = deque(maxlen=256)
        self.write_count = 0
        self.write_result: int | None = None
        self.write_error: Exception | None = None
        self.write_gate: threading.Event | None = None
        self.write_entered = threading.Event()
        self.opened = False
        self.settings = SerialSettings()

    def set_settings(self, settings: SerialSettings) -> None:
        self.settings = settings

    def interrupt_read(self) -> None:
        try:
            self.rx.put_nowait(b"")
        except queue.Full:
            # 已有資料可喚醒讀取。
            pass

    def open(self) -> None:
        while not self.rx.empty():
            self.rx.get_nowait()
        self.opened = True

    def reopen_rx(self) -> None:
        self.open()

    def inject(self, data: bytes) -> None:
        if not self.opened:
            raise OSError("模擬裝置尚未開啟")
        if not 1 <= len(data) <= 4096:
            raise ValueError("模擬輸入必須為 1 至 4096 bytes")
        self.rx.put_nowait(bytes(data))

    def read(self, size: int) -> bytes:
        if not self.opened:
            raise OSError("模擬裝置已關閉")
        try:
            data = self.rx.get(timeout=self.settings.read_timeout_ms / 1000)
        except queue.Empty:
            return b""
        if len(data) > size:
            raise OSError("模擬讀取大小不足")
        return data

    def write(self, data: bytes) -> int:
        self.write_entered.set()
        if self.write_gate is not None and not self.write_gate.wait(timeout=1):
            raise TimeoutError("模擬寫入逾時")
        if not self.opened:
            raise OSError("模擬裝置已關閉")
        self.write_count += 1
        self.writes.append(bytes(data))
        if self.write_error is not None:
            raise self.write_error
        accepted = len(data) if self.write_result is None else self.write_result
        if self.echo and accepted:
            try:
                self.inject(data[:accepted])
            except queue.Full as exc:
                raise OSError("模擬接收緩衝已滿") from exc
        return accepted

    def close(self) -> None:
        self.opened = False

    def describe(self) -> AdapterInfo:
        return AdapterInfo()
