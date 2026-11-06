"""串流匯出可讀文字；共用錄製檢查器，不連線 UART。"""

import codecs
import json
from pathlib import Path

from .protocol.codec import binary_decode
from .recording_info import inspect_capture


def visible(text: str) -> str:
    """僅保留 LF／TAB 與可顯示字元，絕不執行終端控制序列。"""
    return "".join(char if char.isprintable() or char in "\n\t" else
                   char.encode("unicode_escape").decode("ascii") for char in text)


class OutputError(Exception):
    """避免磁碟輸出錯誤被檢查器當成輸入檔損壞。"""


def export_text(directory: Path, output: Path) -> dict:
    if output.suffix.lower() == ".jsonl":
        raise ValueError("文字輸出請使用 .txt 或 .log，避免與原錄製分段混淆。")
    # 先確認輸入目錄，避免參數錯誤時建立空白輸出。
    if not directory.is_dir():
        raise ValueError("找不到錄製 Channel 目錄。")
    try:
        file = output.open("x", encoding="utf-8", newline="\n")
    except FileExistsError as exc:
        raise ValueError("輸出檔已存在，請使用新檔名；未覆蓋既有檔案。") from exc
    except OSError as exc:
        raise ValueError("無法建立文字輸出，請確認目錄與寫入權限。") from exc
    decoder = codecs.getincrementaldecoder("utf-8")(errors="backslashreplace")
    epoch, offset = None, 0
    last_prefix = "[尾端 UTF-8]"

    def write(text: str) -> None:
        try:
            if file.write(text) != len(text):
                raise OSError("部分寫入")
        except OSError as exc:
            raise OutputError from exc

    def lines(prefix: str, text: str) -> None:
        parts = visible(text).split("\n")
        if parts and parts[-1] == "":
            parts.pop()
        for part in parts:
            write(f"{prefix} {part}\n")

    def boundary() -> None:
        tail = decoder.decode(b"", final=True)
        if tail:
            lines(last_prefix + "［未完成字元］", tail)
        decoder.reset()

    def visit(record: dict | None) -> None:
        nonlocal epoch, offset, last_prefix
        if record is None:
            boundary()
            write("[注意] 檢查發現不連續或異常；請以匯出末尾的完整性摘要為準。\n")
            epoch, offset = None, 0
            return
        kind = record["type"]
        if kind == "rx":
            current_epoch = record["stream_epoch"]
            start, end = int(record["offset"]), int(record["next_offset"])
            if current_epoch != epoch or start != offset:
                boundary()
            continued = bool(decoder.getstate()[0])
            epoch, offset = current_epoch, end
            last_prefix = visible(f"[{record.get('timestamp_utc', '時間未提供')} Epoch={epoch} 原區塊={start}..{end}]")
            last_prefix = last_prefix.replace("\n", "\\n").replace("\t", "\\t")
            if continued:
                last_prefix += "［含前區塊續字元］"
            text = decoder.decode(binary_decode(record["data_b64"]))
            if text:
                lines(last_prefix, text)
            else:
                write(last_prefix + " ［UTF-8 字元待續］\n")
        elif kind == "channel_state":
            channel = record["channel"]
            if channel.get("stream_epoch") != epoch or channel.get("state") != "ready":
                boundary()
                epoch, offset = None, 0
            write("[通道狀態] " + json.dumps(record, ensure_ascii=True) + "\n")
        elif kind == "segment_start":
            write("[錄製分段] " + json.dumps(record, ensure_ascii=True) + "\n")
        elif kind == "gap":
            boundary()
            epoch, offset = None, 0
            write("[資料缺口] " + json.dumps(record, ensure_ascii=True) + "\n")

    try:
        with file:
            write("UART RX 可讀文字；匯出中，末尾必須有「匯出完成」才代表匯出已收尾。\n"
                  "原區塊位移不包含右端點；跨區塊字元於完成解碼時顯示並標示續字元。\n"
                  "控制字元與非 UTF-8 bytes 以跳脫形式顯示；此文字檔不是原始 bytes 的替代品。\n")
            result = inspect_capture(directory, on_record=visit)
            boundary()
            status = {"no_known_gap": "對已觀測資料無已知缺口", "unknown": "連續性未知", "incomplete": "資料不完整"}[result["integrity"]]
            write(f"[匯出完成] {status}；RX bytes={result['rx_bytes']}；分段={result['segments']}。\n")
            for issue in result["issues"]:
                write("[注意] " + issue + "\n")
            write("此結果不代表 UART／USB／Driver 前段零遺失。\n")
        return result
    except (OutputError, OSError) as exc:
        raise ValueError("文字輸出失敗；檔案可能只有部分內容，請檢查磁碟空間與權限並使用新檔名重試。") from exc
