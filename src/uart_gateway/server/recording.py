"""Gateway 端只讀 RX 錄製；有界交接，磁碟失敗不阻塞 Reader。"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path

from uart_gateway.core.models import utc_now
from uart_gateway.protocol.messages import identifier

LOG = logging.getLogger(__name__)
FOOTER_RESERVE = 8192
BATCH_ITEMS = 64
BATCH_BYTES = 65536


class RecordingCapacityError(OSError):
    """容量確定不足，現有分段仍保留收尾空間。"""


@dataclass(frozen=True)
class RecordingConfig:
    directory: Path
    queue_bytes: int = 1048576
    segment_bytes: int = 67108864
    max_capture_bytes: int = 1073741824

    def __post_init__(self) -> None:
        if not self.directory.is_absolute():
            raise ValueError("錄製目錄必須是絕對路徑。")
        for name, low, high in (("queue_bytes", 65536, 16777216),
                                ("segment_bytes", 65536, 67108864),
                                ("max_capture_bytes", 65536, 1073741824)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"錄製參數 {name} 超出合法範圍。")
        if self.max_capture_bytes < self.segment_bytes:
            raise ValueError("錄製總容量不得小於分段容量。")


def encode_record(record: dict) -> bytes:
    return (json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")


async def disk_io(function, *args, **kwargs):
    """取消不能留下仍在操作檔案的執行緒。"""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


class Recorder:
    def __init__(self, config: RecordingConfig, instance: str, channel_id: str,
                 changed: Callable[[dict], None]):
        identifier(channel_id)
        self.config, self.instance, self.channel_id, self.changed = config, instance, channel_id, changed
        self.directory = config.directory / f"capture-{instance}" / channel_id
        self.state, self.integrity, self.reason = "recording", "no_known_gap", None
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=1024)
        self.used = 0
        self.pending = 0
        self.task: asyncio.Task | None = None
        self.closing = False
        self.file = None
        self.segment = self.segment_used = self.total_used = 0
        self.digest = hashlib.sha256()
        self.channel: dict = {}
        self.expected_epoch: str | None = None
        self.expected_offset = 0

    @property
    def health(self) -> dict:
        return {"state": self.state, "integrity": self.integrity}

    def fail(self, code: str, message: str) -> None:
        if self.state == "failed":
            return
        self.state, self.integrity, self.reason = "failed", "incomplete", code
        LOG.error("通道 %s 錄製失敗：%s（%s）；RX 繼續，錄製不完整。", self.channel_id, message, code)
        self.changed({**self.health, "reason_code": code})

    async def start(self, channel: dict) -> None:
        self.channel = channel
        try:
            await disk_io(self._start)
        except asyncio.CancelledError:
            await disk_io(self._close_file)
            raise
        except OSError:
            self.fail("RECORDING_IO_FAILED", "無法建立錄製檔，請確認目錄與寫入權限")
            try:
                await asyncio.to_thread(self._close_file)
            except OSError:
                LOG.error("通道 %s 無法關閉錄製檔。", self.channel_id)
            return
        LOG.warning("通道 %s RX 錄製位置：%s", self.channel_id, self.directory)
        self.task = asyncio.create_task(self._run())

    def _start(self) -> None:
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=False)
        self._open_segment()

    def _header(self, segment: int) -> bytes:
        return encode_record({"type": "segment_start", "recording_format_version": 1,
            "capture_id": f"{self.instance}:{self.channel_id}", "gateway_instance_id": self.instance,
            "channel_id": self.channel_id, "segment": segment, "timestamp_utc": utc_now(),
            "channel": self.channel})

    def _open_segment(self, header: bytes | None = None) -> None:
        self.segment += 1
        self.segment_used = 0
        self.digest = hashlib.sha256()
        if header is None:
            header = self._header(self.segment)
        if (len(header) + FOOTER_RESERVE > self.config.segment_bytes or
                self.total_used + len(header) + FOOTER_RESERVE > self.config.max_capture_bytes):
            raise OSError("錄製已達本次容量上限")
        descriptor = os.open(self.directory / f"{self.segment:06d}.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.file = os.fdopen(descriptor, "wb", buffering=0)
        self._raw_write(header)

    def _raw_write(self, raw: bytes, *, hashed: bool = True) -> None:
        # 不重試結果不明的部分寫入，避免在檔案中重複資料。
        if self.file.write(raw) != len(raw):
            raise OSError("錄製檔只有部分內容寫入")
        self.segment_used += len(raw)
        self.total_used += len(raw)
        if hashed:
            self.digest.update(raw)

    def _footer(self, final: bool) -> bytes:
        return encode_record({"type": "segment_end", "final": final, "integrity": self.integrity,
            "reason_code": self.reason, "timestamp_utc": utc_now(), "sha256": self.digest.hexdigest()})

    def _finish_segment(self, *, final: bool) -> None:
        footer = self._footer(final)
        footer_offset = self.segment_used
        try:
            self._raw_write(footer, hashed=False)
            self.file.flush()
            os.fsync(self.file.fileno())
        except OSError:
            # 已知刷新失敗時，盡可能移除看似正常的 Footer；磁碟全失效時仍以外部健康狀態為準。
            try:
                self.file.truncate(footer_offset)
            except OSError:
                LOG.error("通道 %s 無法移除未確認刷新的 Footer，請以錄製失敗訊息為準。", self.channel_id)
            raise
        self._close_file()

    def _write(self, raw: bytes, snapshot: dict | None) -> None:
        if snapshot is not None:
            self.channel = snapshot
        required = len(raw) + FOOTER_RESERVE
        if self.segment_used + required > self.config.segment_bytes:
            header = self._header(self.segment + 1)
            if (len(header) + required > self.config.segment_bytes or
                    self.total_used + len(self._footer(False)) + len(header) + required > self.config.max_capture_bytes):
                raise RecordingCapacityError("錄製已達本次容量上限")
            self._finish_segment(final=False)
            self._open_segment(header)
        if self.total_used + required > self.config.max_capture_bytes:
            raise RecordingCapacityError("錄製已達本次容量上限")
        self._raw_write(raw)
        self.file.flush()

    def _close_file(self) -> None:
        if self.file is not None:
            try:
                self.file.close()
            finally:
                self.file = None

    def put(self, record: dict, *, snapshot: dict | None = None) -> None:
        if self.closing or self.state == "failed":
            return
        raw = encode_record(record)
        if self.pending >= self.queue.maxsize or self.used + len(raw) > self.config.queue_bytes:
            self.fail("RECORDING_OVERFLOW", "磁碟持續跟不上接收速度，錄製佇列已滿")
            return
        self.used += len(raw)
        self.pending += 1
        self.queue.put_nowait((raw, snapshot))

    def channel_state(self, channel: dict) -> None:
        if channel["state"] in ("lost", "faulted") and self.state != "failed":
            self.state, self.integrity = "degraded", "unknown" if self.integrity != "incomplete" else "incomplete"
            self.changed({**self.health, "reason_code": None})
        channel = {**channel, "recording": self.health}
        self.put({"type": "channel_state", "timestamp_utc": utc_now(), "channel": channel}, snapshot=channel)

    def rx(self, record: dict) -> None:
        epoch, offset = record["stream_epoch"], int(record["offset"])
        expected = self.expected_offset if epoch == self.expected_epoch else 0
        if offset != expected:
            self.integrity = "incomplete"
            if self.state != "failed":
                self.state = "degraded"
                self.changed({**self.health, "reason_code": None})
            self.put({"type": "gap", "stream_epoch": epoch, "from_offset": str(expected),
                      "to_offset": str(offset), "timestamp_utc": utc_now()})
        self.expected_epoch, self.expected_offset = epoch, int(record["next_offset"])
        self.put({"type": "rx", **record})

    def _write_batch(self, items: list[tuple[bytes, dict | None]]) -> None:
        for raw, snapshot in items:
            self._write(raw, snapshot)

    async def _run(self) -> None:
        try:
            while (item := await self.queue.get()) is not None:
                items = [item]
                size = len(item[0])
                stopping = False
                # 只收現有項目，不等待湊批；執行中的項目繼續計入配額。
                while len(items) < BATCH_ITEMS and size < BATCH_BYTES and not self.queue.empty():
                    item = self.queue.get_nowait()
                    if item is None:
                        stopping = True
                        break
                    items.append(item)
                    size += len(item[0])
                try:
                    await disk_io(self._write_batch, items)
                finally:
                    self.used -= size
                    self.pending -= len(items)
                if stopping:
                    break
            if self.file is not None:
                await disk_io(self._finish_segment, final=True)
        except RecordingCapacityError:
            self.fail("RECORDING_IO_FAILED", "本次錄製已達容量上限")
            try:
                await disk_io(self._finish_segment, final=True)
            except OSError:
                LOG.error("通道 %s 達容量上限後無法收尾。", self.channel_id)
        except OSError:
            self.fail("RECORDING_IO_FAILED", "寫入、刷新或分段失敗，請檢查磁碟空間與本次容量上限")
        finally:
            try:
                await disk_io(self._close_file)
            except OSError:
                self.fail("RECORDING_IO_FAILED", "關閉錄製檔失敗")
            while not self.queue.empty():
                self.queue.get_nowait()
            self.used = 0
            self.pending = 0

    async def close(self) -> None:
        if self.closing:
            if self.task is not None:
                await asyncio.shield(self.task)
            return
        self.closing = True
        if self.task is not None:
            if not self.task.done():
                await self.queue.put(None)
            await asyncio.shield(self.task)
