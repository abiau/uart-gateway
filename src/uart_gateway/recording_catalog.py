"""有界錄製預覽；收尾標記不是整份雜湊或完整性驗證。"""

from dataclasses import dataclass
import json
from pathlib import Path

PREVIEW_BYTES = 262144
PREVIEW_FILES = 256
TAIL_BYTES = 65536


@dataclass(frozen=True)
class CapturePreview:
    timestamp: str = "未提供"
    source: str = "未提供"
    footer: bool = False
    epochs: int = 0
    complete_scan: bool = False
    readable: bool = False

    @property
    def description(self) -> str:
        ending = "有最終收尾標記（未驗證）" if self.footer else "未見最終收尾（錄製中或異常）"
        count = str(self.epochs) if self.complete_scan else f"至少 {self.epochs}（預覽上限）"
        return f"時間：{self.timestamp} | 來源：{self.source} | {ending} | Epoch：{count}"


def preview_capture(directory: Path) -> CapturePreview:
    """每份最多 256 個分段、256 KiB 順序資料與 64 KiB 尾端。"""
    timestamp, source, footer = "未提供", "未提供", False
    source_channel, source_port = None, None
    epochs = set()
    complete, readable = True, False
    try:
        files = []
        for index, path in enumerate(directory.iterdir()):
            if index >= PREVIEW_FILES:
                complete = False
                break
            if path.suffix == ".jsonl" and path.is_file() and not path.is_symlink():
                files.append(path)
        files.sort()
        if not files or not complete:
            return CapturePreview(complete_scan=False)
        remaining = PREVIEW_BYTES
        for path in files:
            with path.open("rb") as stream:
                while remaining > 0:
                    raw = stream.readline(min(65537, remaining + 1))
                    if not raw:
                        break
                    remaining -= len(raw)
                    if len(raw) > 65536 or not raw.endswith(b"\n") or remaining < 0:
                        complete = False
                        break
                    row = json.loads(raw)
                    if row["type"] == "segment_start" and not readable:
                        channel = row.get("channel", {})
                        adapter = channel.get("adapter") or {}
                        timestamp = str(row.get("timestamp_utc", "未提供"))[:128]
                        source = f"{str(row.get('channel_id', '未提供'))[:128]}／{str(channel.get('label', '未提供'))[:128]}／{str(adapter.get('port') or '未提供')[:256]}"
                        source_channel = f"{str(row.get('channel_id', '未提供'))[:128]}／{str(channel.get('label', '未提供'))[:128]}"
                        source_port = adapter.get("port")
                        readable = True
                    epoch = row.get("stream_epoch")
                    if row["type"] in ("channel_state", "segment_start"):
                        epoch = row["channel"].get("stream_epoch")
                        # 開機 Header 尚未開啟裝置，端點可能要從第一筆狀態取得。
                        port = (row["channel"].get("adapter") or {}).get("port")
                        if source_channel is not None and not source_port and port:
                            source_port = port
                            source = f"{source_channel}／{str(port)[:256]}"
                    if isinstance(epoch, str) and epoch:
                        epochs.add(epoch)
                if remaining <= 0:
                    complete = False
            if not complete:
                break
        with files[-1].open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - TAIL_BYTES))
            tail = stream.read(TAIL_BYTES)
        if tail.endswith(b"\n"):
            row = json.loads(tail.splitlines()[-1])
            footer = row.get("type") == "segment_end" and row.get("final") is True
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        complete = False
    return CapturePreview(timestamp, source, footer, len(epochs), complete, readable)
