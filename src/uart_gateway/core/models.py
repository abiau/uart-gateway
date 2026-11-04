"""領域資料與錯誤；不匯入線上協定。"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def new_id() -> str:
    return uuid4().hex


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class DomainError(Exception):
    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code = code
        self.details = details


@dataclass(frozen=True)
class Context:
    instance: str
    revision: int
    epoch: str | None


@dataclass(frozen=True)
class SerialSettings:
    baudrate: int = 115200
    bytesize: int = 8
    parity: str = "none"
    stopbits: float = 1
    flow_control: str = "none"
    dtr: bool = False
    rts: bool = False
    read_timeout_ms: int = 20
    write_timeout_ms: int = 1000

    def __post_init__(self) -> None:
        ranges = {"baudrate": (300, 4000000), "bytesize": (5, 8),
                  "read_timeout_ms": (1, 1000), "write_timeout_ms": (10, 30000)}
        for name, (low, high) in ranges.items():
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"Serial 參數 {name} 超出合法範圍")
        if self.parity not in ("none", "even", "odd", "mark", "space"):
            raise ValueError("Serial parity 不合法")
        if type(self.stopbits) not in (int, float) or self.stopbits not in (1, 1.5, 2):
            raise ValueError("Serial stopbits 不合法")
        if self.flow_control not in ("none", "rtscts", "xonxoff"):
            raise ValueError("Serial flow_control 不合法")
        if type(self.dtr) is not bool or type(self.rts) is not bool:
            raise ValueError("DTR／RTS 必須是布林值")


@dataclass(frozen=True)
class AdapterInfo:
    port: str | None = None
    vid: int | None = None
    pid: int | None = None
    serial_number: str | None = None
    location: str | None = None
    match_status: str = "unverified"


@dataclass(frozen=True)
class ChannelConfig:
    id: str
    label: str
    target_note: str | None = None
    serial: SerialSettings = field(default_factory=SerialSettings)
    auto_open_rx: bool = True


@dataclass(frozen=True)
class Event:
    kind: str
    channel_id: str
    revision: int
    epoch: str | None
    data: dict[str, Any]
    timestamp: str = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class Chunk:
    offset: int
    seq: int
    data: bytes
    timestamp: str = field(default_factory=utc_now)


@dataclass
class TxRecord:
    id: str
    principal: str
    session: str
    channel_id: str
    context: Context
    token: int
    fingerprint: bytes
    size: int
    state: str = "pending"
    started: bool = False
    accepted: int | None = None
    reason: str | None = None
    completed: str | None = None
    expires: float | None = None
