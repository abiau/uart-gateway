"""錄製工作台的資料流編號、相對時間及命中區段；共用原檢查器。"""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import secrets
import threading

from .recording_info import EpochSummary, inspect_capture
from .recording_selection import RXSelection, parse_time


class NavigationCancelled(Exception):
    """資料流清單取消，不宣稱已完成完整性檢查。"""


def timeline_capture(directory: Path, *, stop: threading.Event | None = None) -> dict:
    summary = EpochSummary()
    first = None
    times = {}

    def visit(record):
        nonlocal first
        if stop and stop.is_set():
            raise NavigationCancelled
        summary.visit(record)
        if record is None or record["type"] == "segment_end":
            return
        try:
            stamp = parse_time(record.get("timestamp_utc"))
        except ValueError:
            return
        if first is None:
            first = stamp
        if record["type"] == "rx":
            epoch = record["stream_epoch"]
            if epoch in summary.epochs and epoch not in times:
                times[epoch] = stamp.isoformat()

    result = inspect_capture(directory, on_record=visit)
    result["summary"] = summary.result(result)
    result["started_at"] = first.isoformat() if first else None
    result["epoch_times"] = times
    return result


async def timeline(directory: Path) -> dict:
    stop = threading.Event()

    async def scan() -> dict:
        try:
            return await asyncio.to_thread(timeline_capture, directory, stop=stop)
        except NavigationCancelled:
            # 工作執行緒正常回應取消，shield 不應將它列為背景故障。
            raise asyncio.CancelledError from None

    task = asyncio.create_task(scan())
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as exc:
        stop.set()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as cleanup:
            exc.add_note(f"資料流清單清理另有錯誤：{cleanup}")
        raise


def time_input(value: str, started_at: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 16 and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
        minutes = float(value)
        if minutes > 525600:
            raise ValueError("相對時間最多為 525600 分鐘。")
        if started_at is None:
            raise ValueError("此錄製缺少可用起點時間，請改用含時區的完整時間。")
        try:
            return (parse_time(started_at) + timedelta(minutes=minutes)).isoformat()
        except OverflowError as exc:
            raise ValueError("相對時間超出可表示的日期範圍。") from exc
    return parse_time(value).isoformat()


def selection_arguments(selection: RXSelection) -> list[str]:
    return [f"{flag}={value}" for flag, value in
            (("--epoch", selection.epoch), ("--from-time", selection.from_time), ("--to-time", selection.to_time)) if value]


def match_selection(match: dict, seconds: int = 30) -> RXSelection:
    if type(seconds) is not int or not 0 <= seconds <= 3600:
        raise ValueError("命中前後秒數必須為 0 至 3600。")
    end = parse_time(match["timestamp_utc"])
    start = parse_time(match.get("first_timestamp_utc", match["timestamp_utc"]))
    try:
        return RXSelection(match["epoch"], (min(start, end) - timedelta(seconds=seconds)).isoformat(),
                           (max(start, end) + timedelta(seconds=seconds)).isoformat())
    except OverflowError as exc:
        raise ValueError("命中時間超出可回看的日期範圍，請改用 F 選取資料流。") from exc


def output_path(root: Path) -> Path:
    # 真正建立檔案仍由原匯出器的 x 模式執行，競爭時也不能覆蓋。
    now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return root / "exports" / f"uart-{now}-{secrets.token_hex(4)}.txt"
