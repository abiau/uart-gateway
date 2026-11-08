"""有界 JSON Lines 與任意二進位負載編碼。"""

import asyncio
import base64
import binascii
import json
import math

from .errors import ProtocolError

MAX_FRAME_BYTES = 65536


def _pairs(items: list[tuple]) -> dict:
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("重複欄位")
        result[key] = value
    return result


def _depth(raw: str, maximum: int) -> None:
    depth = 0
    quoted = escaped = False
    for char in raw:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > maximum:
                raise ValueError("巢狀深度超限")
        elif char in "]}":
            depth -= 1


def _values(value: object) -> None:
    if isinstance(value, str):
        value.encode("utf-8", errors="strict")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("非法數字")
    elif isinstance(value, dict):
        for key, child in value.items():
            _values(key)
            _values(child)
    elif isinstance(value, list):
        for child in value:
            _values(child)


def decode_frame(raw: bytes, maximum: int = MAX_FRAME_BYTES, depth: int = 16) -> dict:
    if len(raw) > maximum:
        raise ProtocolError("FRAME_TOO_LARGE", "訊框超過大小上限。")
    try:
        if not raw.endswith(b"\n") or raw.endswith(b"\r\n") or b"\n" in raw[:-1]:
            raise ValueError("分界錯誤")
        text = raw[:-1].decode("utf-8")
        _depth(text, depth)
        value = json.loads(text, object_pairs_hook=_pairs)
        if not isinstance(value, dict):
            raise ValueError("根物件錯誤")
        _values(value)
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ProtocolError("INVALID_FRAME", "訊框格式不合法。") from exc


def encode_frame(message: dict, maximum: int = MAX_FRAME_BYTES) -> bytes:
    try:
        raw = (json.dumps(message, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":")) + "\n").encode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise ProtocolError("INVALID_FRAME", "訊框無法編碼。") from exc
    if len(raw) > maximum:
        raise ProtocolError("FRAME_TOO_LARGE", "訊框超過大小上限。")
    return raw


async def read_frame(reader: asyncio.StreamReader, maximum: int = MAX_FRAME_BYTES,
                     depth: int = 16) -> dict:
    message, _ = await read_sized_frame(reader, maximum, depth)
    return message


async def read_sized_frame(reader: asyncio.StreamReader, maximum: int = MAX_FRAME_BYTES,
                           depth: int = 16) -> tuple[dict, int]:
    try:
        raw = await reader.readuntil(b"\n")
    except asyncio.LimitOverrunError as exc:
        raise ProtocolError("FRAME_TOO_LARGE", "訊框超過大小上限。") from exc
    except asyncio.IncompleteReadError as exc:
        if not exc.partial:
            raise EOFError from exc
        raise ProtocolError("INVALID_FRAME", "連線在訊框完成前結束。") from exc
    return decode_frame(raw, maximum, depth), len(raw)


def binary_encode(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")


def binary_decode(value: object, maximum: int = 4096) -> bytes:
    if not isinstance(value, str):
        raise ProtocolError("INVALID_PARAMS", "二進位負載必須使用 Base64。")
    if len(value) > 4 * ((maximum + 2) // 3):
        raise ProtocolError("PAYLOAD_TOO_LARGE", "負載超過上限。")
    try:
        payload = base64.b64decode(value, validate=True)
        if not payload or binary_encode(payload) != value:
            raise ValueError("不是標準 Base64")
    except (ValueError, binascii.Error) as exc:
        raise ProtocolError("INVALID_PARAMS", "Base64 負載不合法。") from exc
    if len(payload) > maximum:
        raise ProtocolError("PAYLOAD_TOO_LARGE", "負載超過上限。")
    return payload
