"""跨次 Capture 保留管理；預覽只讀中繼資料，套用才逐份驗證。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from itertools import islice
import json
from pathlib import Path
import re
import stat
from uuid import uuid4

from .recording_info import _inspect_segments

CAPTURE = re.compile(r"capture-([0-9a-f]{32})")
CHANNEL = re.compile(r"[A-Za-z0-9_-]{1,64}")
VERIFY_WORKERS = 4


@dataclass(frozen=True)
class Capture:
    path: Path
    files: tuple[tuple[str, int, int, int], ...]
    closed: datetime | None

    @property
    def size(self) -> int:
        return sum(file[1] for file in self.files)


def entries(path: Path, limit: int) -> list[Path]:
    found = list(islice(path.iterdir(), limit + 1))
    if len(found) > limit:
        raise ValueError("保留管理掃描達上限，請縮小管理目錄。")
    return sorted(found)


def plain(info: stat.stat_result, expected) -> bool:
    return expected(info.st_mode) and not (
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def ending(files: list[Path], instance: str, channel: str) -> datetime | None:
    try:
        with files[0].open("rb") as stream:
            header = json.loads(stream.readline(65537))
        if (header.get("type"), header.get("recording_format_version"), header.get("capture_id"),
                header.get("gateway_instance_id"), header.get("channel_id"), header.get("segment")) != (
                    "segment_start", 1, f"{instance}:{channel}", instance, channel, 1):
            return None
        with files[-1].open("rb") as stream:
            size = stream.seek(0, 2)
            stream.seek(max(0, size - 8192))
            tail = stream.read(8192)
        footer = json.loads(tail.splitlines()[-1])
        if (not tail.endswith(b"\n") or footer.get("type") != "segment_end" or footer.get("final") is not True
                or footer.get("integrity") not in ("no_known_gap", "unknown")):
            return None
        stamp = datetime.fromisoformat(footer["timestamp_utc"])
        return stamp if stamp.tzinfo is not None else None
    except (AttributeError, KeyError, IndexError, TypeError, ValueError, RecursionError):
        return None


def snapshot(path: Path, instance: str, *, check_closed: bool = True) -> Capture:
    channels = entries(path, 4)
    metadata = []
    stamps = []
    if not channels:
        return Capture(path, (), None)
    for channel in channels:
        if not CHANNEL.fullmatch(channel.name) or not plain(channel.lstat(), stat.S_ISDIR):
            return Capture(path, tuple(metadata), None)
        files = entries(channel, 32768)
        if not files:
            return Capture(path, tuple(metadata), None)
        for index, file in enumerate(files, 1):
            info = file.lstat()
            if file.name != f"{index:06d}.jsonl" or not plain(info, stat.S_ISREG):
                return Capture(path, tuple(metadata), None)
            metadata.append((file.relative_to(path).as_posix(), info.st_size, info.st_mtime_ns, info.st_ino))
        if check_closed:
            stamp = ending(files, instance, channel.name)
            if stamp is not None:
                stamps.append(stamp)
    return Capture(path, tuple(metadata), max(stamps) if len(stamps) == len(channels) else None)


def verify(capture: Capture) -> None:
    segments = {}
    for relative, size, *_ in capture.files:
        channel, _ = relative.split("/", 1)
        files, used = segments.setdefault(channel, ([], 0))
        files.append(capture.path / relative)
        segments[channel] = (files, used + size)
    for files, used in segments.values():
        if used > 1073741824:
            raise ValueError(f"{capture.path.name} 超過錄製檢查容量上限，清理停止。")
        info = _inspect_segments(files)
        if not info["closed_cleanly"] or info["integrity"] == "incomplete":
            raise ValueError(f"{capture.path.name} 未通過檢查，清理停止。")


def retention(directory: Path, *, days: int | None = None, max_bytes: int | None = None,
              apply: str | None = None) -> dict:
    if days is None and max_bytes is None:
        raise ValueError("請指定保留天數或跨次錄製容量上限。")
    if (days is not None and not 1 <= days <= 36500) or (max_bytes is not None and max_bytes < 1):
        raise ValueError("保留天數須為 1 至 36500，容量上限須為正整數。")
    if not plain(directory.lstat(), stat.S_ISDIR):
        raise ValueError("錄製根目錄必須是一般目錄。")
    directory = directory.resolve(strict=True)
    captures = []
    staging = []
    files_seen = 0
    for path in entries(directory, 2000):
        match = CAPTURE.fullmatch(path.name)
        if path.name.startswith(".retention-"):
            staging.append(path.name)
        if match is None:
            continue
        capture = snapshot(path, match[1]) if plain(path.lstat(), stat.S_ISDIR) else Capture(path, (), None)
        files_seen += len(capture.files)
        if files_seen > 65536:
            raise ValueError("分段數量超過保留管理上限。")
        captures.append(capture)
    total = sum(c.size for c in captures)
    remaining = total
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days) if days is not None else None
    closed = sorted((c for c in captures if c.closed is not None and c.closed <= now),
                    key=lambda c: (c.closed, c.path.name))
    selected = []
    for capture in closed:
        expired = cutoff is not None and capture.closed <= cutoff
        oversized = max_bytes is not None and remaining > max_bytes
        if expired or oversized:
            selected.append(capture)
            remaining -= capture.size
    plan = sha256(json.dumps([str(directory), days, max_bytes,
        [(c.path.name, c.files) for c in selected]], separators=(",", ":")).encode()).hexdigest()
    result = {"plan": plan, "observed_bytes": total, "estimated_remaining_bytes": remaining,
              "target_exceeded": max_bytes is not None and remaining > max_bytes,
              "candidates": [{"capture": c.path.name, "bytes": c.size, "closed_at": c.closed.isoformat()} for c in selected],
              "protected": [c.path.name for c in captures if c.closed is None or c.closed > now],
              "staging": staging, "removed": []}
    if apply is None:
        return result
    if apply != plan:
        raise ValueError("清理計畫已改變或識別不符，請重新預覽。")
    if len(selected) == 1:
        verify(selected[0])
    elif selected:
        # Capture 彼此獨立，併行驗證可重疊檔案開啟等待；全部通過後才開始清理。
        with ThreadPoolExecutor(max_workers=VERIFY_WORKERS) as executor:
            for start in range(0, len(selected), VERIFY_WORKERS):
                for _ in executor.map(verify, selected[start:start + VERIFY_WORKERS]):
                    pass
    for capture in selected:
        instance = CAPTURE.fullmatch(capture.path.name)[1]
        channels = {Path(file[0]).parts[0] for file in capture.files}
        if snapshot(capture.path, instance) != capture:
            raise ValueError(f"{capture.path.name} 已變動，清理停止。")
        moved = directory / (".retention-" + uuid4().hex + "-" + capture.path.name)
        capture.path.rename(moved)
        if snapshot(moved, instance, check_closed=False).files != capture.files:
            raise ValueError(f"暫存 Capture {moved.name} 已變動，保留供人工檢查。")
        for relative, *_ in capture.files:
            (moved / relative).unlink()
        for channel in channels:
            (moved / channel).rmdir()
        moved.rmdir()
        result["removed"].append(capture.path.name)
    return result
