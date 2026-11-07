"""只讀錄製回看；慢終端直接減慢掃描，保持有界暫存。"""

import asyncio
import codecs
from pathlib import Path
import sys
import threading
from typing import TextIO

from .protocol.codec import binary_decode
from .recording_info import inspect_capture
from .recording_selection import RXSelection
from .recording_text import visible
from .text_renderer import RESET, TerminalRenderer


class PlaybackCancelled(Exception):
    """使用者中止，尚未完成整份檔案檢查。"""


class PlaybackOutputError(Exception):
    """不讓顯示失敗被掃描器誤判成錄製損壞。"""


def play_capture(directory: Path, *, terminal: bool, stop: threading.Event,
                 stdout: TextIO, stderr: TextIO, selection: RXSelection | None = None) -> dict:
    selection = selection or RXSelection()
    selected_chunks = selected_bytes = 0
    decoder = codecs.getincrementaldecoder("utf-8")(errors="backslashreplace")
    renderer = TerminalRenderer() if terminal else None
    epoch, offset, revision = None, 0, None
    first_header = True
    output_failed = False

    def write(stream: TextIO, text: str) -> None:
        nonlocal output_failed
        if not text:
            return
        try:
            if stream.write(text) != len(text):
                raise OSError("只有部分畫面寫入")
            stream.flush()
        except (OSError, UnicodeError) as exc:
            if stream is stdout:
                output_failed = True
            raise PlaybackOutputError("離線回看輸出失敗，未完成檔案檢查。") from exc

    def data(text: str) -> None:
        write(stdout, renderer.feed(text) if renderer else visible(text))

    def boundary() -> None:
        data(decoder.decode(b"", final=True))
        decoder.reset()
        if renderer:
            write(stdout, renderer.finish())

    def note(message: str) -> None:
        # 邊界先清空資料列，避免 CR／BS 把診斷接在舊 UART 行內。
        write(stdout, "\n")
        write(stderr, message + "\n")

    def field(value: object) -> str:
        return visible(str(value)).replace("\n", "\\n").replace("\t", "\\t")

    def visit(record: dict | None) -> None:
        nonlocal epoch, offset, revision, first_header, selected_chunks, selected_bytes
        if stop.is_set():
            raise PlaybackCancelled
        if record is None:
            boundary()
            note("[離線回看] 發現缺口或檔案異常；完整性尚未確認，請查看最後摘要。")
            epoch, offset, revision = None, 0, None
            return
        kind = record["type"]
        if kind == "rx":
            if not selection.accepts(record):
                boundary()
                epoch, offset, revision = None, 0, None
                return
            selected_chunks += 1
            selected_bytes += int(record["next_offset"]) - int(record["offset"])
            current = record["stream_epoch"]
            start, end = int(record["offset"]), int(record["next_offset"])
            binding = record.get("binding_revision")
            if current != epoch or start != offset or binding != revision:
                boundary()
                note(f"[離線資料流] Epoch={field(current)}；位移={start}；"
                     f"時間={field(record.get('timestamp_utc', '未提供'))}")
            epoch, offset, revision = current, end, binding
            data(decoder.decode(binary_decode(record["data_b64"])))
        elif kind == "segment_start" and first_header:
            first_header = False
            channel = record.get("channel", {})
            if not isinstance(channel, dict):
                channel = {}
            note(f"[離線通道] {field(record.get('channel_id', '未提供'))}／{field(channel.get('label', '未提供'))}")
        elif kind == "gap":
            boundary()
            note(f"[資料缺口] {field(record.get('from_offset'))}..{field(record.get('to_offset'))}")
            epoch, offset, revision = None, 0, None
        elif kind == "channel_state":
            channel = record["channel"]
            state = channel.get("state")
            if state != "ready" or channel.get("stream_epoch") != epoch:
                boundary()
                names = {"ready": "就緒", "lost": "裝置遺失", "faulted": "故障", "closed": "已關閉",
                         "opening": "開啟中", "draining": "關閉中"}
                note(f"[錄製當時狀態] {names.get(state, '狀態未提供')}")
                epoch, offset, revision = None, 0, None

    failure: BaseException | None = None
    try:
        if stop.is_set():
            raise PlaybackCancelled
        write(stderr, "離線回看開始；依序快速顯示錄製內容，並非即時 UART。完整性須以最後摘要為準。\n")
        if selection.active:
            write(stderr, "[篩選] " + selection.description + "缺少有效時間的區塊不納入時間篩選。\n")
        result = inspect_capture(directory, on_record=visit)
        result["selected_chunks"], result["selected_bytes"] = selected_chunks, selected_bytes
        if stop.is_set():
            raise PlaybackCancelled
        boundary()
        states = {"no_known_gap": "對已觀測資料無已知缺口", "incomplete": "資料不完整", "unknown": "連續性未知"}
        note(f"[離線回看完成] {states[result['integrity']]}；RX bytes={result['rx_bytes']}；分段={result['segments']}。")
        if selection.active:
            note(f"[選取範圍] RX 區塊={selected_chunks}；RX bytes={selected_bytes}；上方完整性摘要屬整份錄製。")
        for issue in result["issues"]:
            write(stderr, "注意：" + issue + "\n")
        return result
    except BaseException as exc:
        failure = exc
        if isinstance(exc, PlaybackCancelled):
            boundary()
            note("離線回看已中止，未完成整份錄製檔案檢查。")
        raise
    finally:
        if renderer is not None and not output_failed:
            try:
                write(stdout, renderer.finish())
            except PlaybackOutputError as exc:
                if failure is None:
                    raise
                failure.add_note(str(exc))


async def playback(directory: Path, *, terminal: bool, selection: RXSelection | None = None) -> dict:
    if terminal and not sys.stdout.isatty():
        raise ValueError("終端回看需要互動式終端機；輸出到檔案請使用 recording-text。")
    stop = threading.Event()

    async def scan() -> dict:
        try:
            return await asyncio.to_thread(play_capture, directory, terminal=terminal,
                stop=stop, stdout=sys.stdout, stderr=sys.stderr, selection=selection)
        except PlaybackCancelled:
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
            exc.add_note(f"回看清理另有錯誤：{cleanup}")
        raise
    except PlaybackOutputError as exc:
        raise ValueError(str(exc)) from exc
