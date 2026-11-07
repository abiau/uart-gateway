"""離線 RX 區塊篩選；不改變整份錄製的完整性掃描。"""

from dataclasses import dataclass
from datetime import datetime, timezone


def parse_time(value: str) -> datetime:
    if not isinstance(value, str) or len(value) > 64 or "T" not in value:
        raise ValueError("時間請使用含時區的 ISO 8601，例如 2026-11-06T19:05:00Z 或 +08:00。")
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ValueError("時間必須包含日期、時間及 Z 或明確時區，且為有效 ISO 8601。") from exc


@dataclass(frozen=True)
class RXSelection:
    epoch: str | None = None
    from_time: str | None = None
    to_time: str | None = None

    def __post_init__(self):
        if self.epoch is not None and (not 1 <= len(self.epoch) <= 128 or not self.epoch.isprintable()):
            raise ValueError("Epoch 必須是 1 至 128 個可顯示字元。")
        start = parse_time(self.from_time) if self.from_time else None
        end = parse_time(self.to_time) if self.to_time else None
        if start and end and start > end:
            raise ValueError("起始時間不得晚於結束時間。")
        object.__setattr__(self, "_start", start)
        object.__setattr__(self, "_end", end)

    @property
    def active(self) -> bool:
        return any((self.epoch, self.from_time, self.to_time))

    @property
    def description(self) -> str:
        return (f"Epoch={self.epoch or '全部'}；起={self.from_time or '不限'}；迄={self.to_time or '不限'}；"
                "時間兩端包含，以 RX 區塊時間選取整塊；仍檢查整份錄製。")

    def accepts(self, record: dict) -> bool:
        if self.epoch is not None and record["stream_epoch"] != self.epoch:
            return False
        if self._start is None and self._end is None:
            return True
        try:
            stamp = parse_time(record.get("timestamp_utc"))
        except ValueError:
            return False
        return (self._start is None or stamp >= self._start) and (self._end is None or stamp <= self._end)
