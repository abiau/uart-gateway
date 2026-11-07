"""有界的離線 UTF-8 字面搜尋；跨連續區塊，不跨世代或缺口。"""

import asyncio
from collections import deque
from pathlib import Path
import threading

from .protocol.codec import binary_decode
from .recording_info import inspect_capture
from .recording_selection import RXSelection
from .recording_text import visible


class SearchCancelled(Exception):
    """搜尋取消；不能宣稱完成整份檔案檢查。"""


def search_capture(directory: Path, keyword: str, *, selection: RXSelection | None = None,
                   max_results: int = 200, stop: threading.Event | None = None) -> dict:
    if not keyword or len(keyword.encode("utf-8")) > 4096 or not keyword.isprintable():
        raise ValueError("關鍵字須為可顯示文字，長度 1 至 4096 UTF-8 bytes；採區分大小寫的字面搜尋。")
    if type(max_results) is not int or not 1 <= max_results <= 1000:
        raise ValueError("搜尋結果上限必須為 1 至 1000。")
    selection = selection or RXSelection()
    needle = keyword.encode("utf-8")
    matches, limited = [], False
    tail, context, offset, next_match = b"", None, 0, 0
    spans = deque()
    selected_chunks = selected_bytes = 0

    def field(value):
        text = str(value)
        return text[:128] + ("…" if len(text) > 128 else "")

    def visit(record):
        nonlocal tail, context, offset, next_match, limited, selected_chunks, selected_bytes
        if stop is not None and stop.is_set():
            raise SearchCancelled
        if record is None or record["type"] in ("gap", "channel_state"):
            # 狀態邊界採保守斷開；不把前後資料拼成不存在的命中。
            tail, context = b"", None
            spans.clear()
            return
        if record["type"] != "rx":
            return
        if not selection.accepts(record):
            tail, context = b"", None
            spans.clear()
            return
        payload = binary_decode(record["data_b64"])
        selected_chunks += 1
        selected_bytes += len(payload)
        if limited:
            return
        current = (record["stream_epoch"], record.get("binding_revision"))
        start, end = int(record["offset"]), int(record["next_offset"])
        if current != context or start != offset:
            tail, next_match = b"", start
            spans.clear()
        spans.append((start, start + len(payload), field(record.get("timestamp_utc", "未提供"))))
        combined = tail + payload
        base = start - len(tail)
        position = max(0, next_match - base)
        while (found := combined.find(needle, position)) >= 0:
            if len(matches) >= max_results:
                limited = True
                break
            begin = base + found
            snippet = combined[max(0, found - 48): min(len(combined), found + min(len(needle), 128) + 48)]
            matches.append({"epoch": field(current[0]), "timestamp_utc": field(record.get("timestamp_utc", "未提供")),
                            "first_timestamp_utc": next(stamp for left, right, stamp in spans if left <= begin < right),
                            "offset": begin, "next_offset": begin + len(needle),
                            "preview": visible(snippet.decode("utf-8", errors="backslashreplace"))})
            position = found + len(needle)
            next_match = base + position
        tail = combined[-(len(needle) - 1):] if len(needle) > 1 else b""
        while spans and spans[0][1] <= start + len(payload) - len(tail):
            spans.popleft()
        context, offset = current, end

    result = inspect_capture(directory, on_record=visit)
    result["search"] = {"matches": matches, "limited": limited,
                        "selected_chunks": selected_chunks, "selected_bytes": selected_bytes}
    return result


async def search(directory: Path, keyword: str, *, selection: RXSelection, max_results: int = 200) -> dict:
    stop = threading.Event()

    async def scan() -> dict:
        try:
            return await asyncio.to_thread(search_capture, directory, keyword,
                                           selection=selection, max_results=max_results, stop=stop)
        except SearchCancelled:
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
            exc.add_note(f"搜尋清理另有錯誤：{cleanup}")
        raise
