"""離線檢查 RX 錄製；只讀檔案，不連線或操作 UART。"""

import hashlib
from collections.abc import Callable
import json
from pathlib import Path
import re

from .protocol.codec import binary_decode
from .protocol.errors import ProtocolError


def inspect_capture(directory: Path, *, on_record: Callable[[dict | None], None] | None = None) -> dict:
    try:
        files = []
        for path in directory.glob("*.jsonl"):
            files.append(path)
            if len(files) > 32768:
                raise ValueError("錄製分段數量超過本格式上限。")
        files.sort()
        if not files or sum(path.stat().st_size for path in files) > 1073741824:
            raise ValueError("找不到有效錄製分段，或檔案總量超過本格式上限。")
    except OSError as exc:
        raise ValueError("無法列出錄製檔，請確認目錄與讀取權限。") from exc
    integrity = "no_known_gap"
    issues = []
    identity = None
    epoch, offset, seq = None, 0, 0
    byte_count = rx_count = 0
    final = False

    def issue(message: str, status: str = "incomplete") -> None:
        nonlocal integrity
        if on_record is not None:
            on_record(None)
        if integrity != "incomplete":
            integrity = status
        if len(issues) < 32 and message not in issues:
            issues.append(message)

    def uint(value) -> int:
        if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or len(value) > 20:
            raise ValueError("位移或序號格式不合法")
        result = int(value)
        if result > 2**64 - 1:
            raise ValueError("位移或序號超出上限")
        return result

    for index, path in enumerate(files, 1):
        if path.name != f"{index:06d}.jsonl" or not re.fullmatch(r"[0-9]{6}\.jsonl", path.name):
            issue("錄製分段缺少或編號不連續。")
        digest, header, footer = hashlib.sha256(), False, False
        try:
            with path.open("rb") as file:
                while raw := file.readline(65537):
                    if len(raw) > 65536 or not raw.endswith(b"\n"):
                        footer, final = False, False
                        issue("存在超限或未完成的 JSON 行。", "unknown")
                        break
                    record = json.loads(raw)
                    if not isinstance(record, dict) or footer:
                        raise ValueError("Footer 之後仍有資料或紀錄型別錯誤")
                    kind = record["type"]
                    if not header:
                        if (kind != "segment_start" or type(record["recording_format_version"]) is not int
                                or record["recording_format_version"] != 1 or record["segment"] != index):
                            raise ValueError("分段 Header 或版本不合法")
                        current = tuple(record[key] for key in ("capture_id", "gateway_instance_id", "channel_id"))
                        if identity is not None and current != identity:
                            raise ValueError("不同 Capture 的分段被混合")
                        identity, header = current, True
                    elif kind == "segment_end":
                        footer = True
                        valid_digest = record["sha256"] == digest.hexdigest()
                        if not valid_digest:
                            issue("分段 SHA-256 不符，檔案可能損壞。")
                        if type(record["final"]) is not bool:
                            raise ValueError("最終標記不合法")
                        final = record["final"] and valid_digest
                        if final and index != len(files):
                            issue("最終收尾後仍有額外分段。")
                        status = record["integrity"]
                        if status not in ("no_known_gap", "unknown", "incomplete"):
                            raise ValueError("完整性狀態不合法")
                        if status != "no_known_gap":
                            issue("錄製器已標示資料不完整或連續性未知。", status)
                    elif kind == "rx":
                        payload = binary_decode(record["data_b64"])
                        start, end, sequence = uint(record["offset"]), uint(record["next_offset"]), uint(record["seq"])
                        current_epoch = record["stream_epoch"]
                        if not isinstance(current_epoch, str) or not current_epoch or not 1 <= len(payload) <= 4096:
                            raise ValueError("RX 世代或負載不合法")
                        if epoch != current_epoch:
                            epoch, offset, seq = current_epoch, 0, 0
                        if start != offset or sequence != seq or end != start + len(payload):
                            issue("RX 位移、序號或長度不連續。")
                        offset, seq = end, sequence + 1
                        byte_count += len(payload)
                        rx_count += 1
                    elif kind == "gap":
                        issue("錄製包含已知資料缺口。")
                    elif kind == "channel_state":
                        if record["channel"]["state"] in ("lost", "faulted"):
                            issue("來源裝置曾失去連線或故障，連續性未知。", "unknown")
                    else:
                        raise ValueError("未支援的錄製紀錄類型")
                    if kind != "segment_end":
                        digest.update(raw)
                    if on_record is not None:
                        on_record(record)
        except (OSError, ValueError, KeyError, TypeError, ProtocolError, RecursionError):
            footer = False
            issue("錄製資料無法完整讀取或格式錯誤。")
        if not footer:
            final = False
            issue("缺少分段 Footer，可能非正常關閉。", "unknown")
    if not final:
        issue("缺少最終收尾標記，不能確認 Capture 正常結束。", "unknown")
    return {"integrity": integrity, "closed_cleanly": final, "segments": len(files),
            "rx_chunks": rx_count, "rx_bytes": byte_count, "issues": issues}
