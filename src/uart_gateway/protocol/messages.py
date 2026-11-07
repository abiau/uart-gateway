"""V1 請求驗證；未知參數不靜默忽略。"""

from dataclasses import dataclass
import re

from .errors import ProtocolError

U64_MAX = 2**64 - 1
EVENTS = {"rx", "gap", "subscription_state", "channel_state", "lease_revoked",
          "tx_result", "recording_state"}


def uint(value: object) -> int:
    if (not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]{0,19}", value)
            or int(value) > U64_MAX):
        raise ProtocolError("INVALID_PARAMS", "整數識別欄位不合法。")
    return int(value)


def identifier(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ProtocolError("INVALID_PARAMS", "識別欄位不合法。")
    return value


def text(value: object, maximum: int, minimum: int = 1) -> str:
    if not isinstance(value, str) or not minimum <= len(value.encode("utf-8")) <= maximum:
        raise ProtocolError("INVALID_PARAMS", "文字欄位長度不合法。")
    return value


def fields(params: object, required: set[str], optional: set[str] = frozenset()) -> None:
    if not isinstance(params, dict) or not required <= params.keys() or params.keys() - required - optional:
        raise ProtocolError("INVALID_PARAMS", "參數缺漏或含未知欄位。")


@dataclass(frozen=True)
class Request:
    id: str
    method: str
    params: dict


def request(message: dict, previous: int) -> Request:
    if (set(message) != {"type", "id", "method", "params"}
            or message["type"] != "request" or not isinstance(message["params"], dict)):
        raise ProtocolError("INVALID_FRAME", "請求外層不合法。")
    try:
        number = uint(message["id"])
        if number <= previous:
            raise ValueError
    except (ProtocolError, ValueError) as exc:
        raise ProtocolError("INVALID_REQUEST_ID", "請求識別必須嚴格遞增。") from exc
    if (not isinstance(message["method"], str) or not message["method"].isascii()
            or not 1 <= len(message["method"]) <= 64):
        raise ProtocolError("INVALID_FRAME", "方法名稱不合法。")
    return Request(message["id"], message["method"], message["params"])


RX_SETTING_FIELDS = frozenset({"baudrate", "bytesize", "parity", "stopbits", "read_timeout_ms", "write_timeout_ms"})


SCHEMAS = {
    "hello": ({"versions", "client_name", "client_version"}, set()),
    "auth.authenticate": ({"scheme", "token"}, set()),
    "ping": (set(), set()),
    "channel.list": (set(), {"limit", "cursor"}),
    "channel.get": ({"channel_id"}, set()),
    "channel.reopen_rx": ({"channel_id", "expected"}, set()),
    "channel.configure_rx": ({"channel_id", "expected", "serial"}, set()),
    "binding.rebind": ({"channel_id", "expected"}, set()),
    "binding.confirm": ({"channel_id", "expected", "label"}, {"target_note"}),
    "rx.subscribe": ({"channel_id", "mode"}, {"stream_epoch", "from_offset", "gap_policy"}),
    "rx.unsubscribe": ({"subscription_id"}, set()),
    "lease.acquire": ({"channel_id", "expected"}, set()),
    "lease.renew": ({"channel_id", "expected", "fencing_token"}, set()),
    "lease.release": ({"channel_id", "expected", "fencing_token"}, set()),
    "tx.write": ({"channel_id", "expected", "fencing_token", "tx_id", "data_b64"}, set()),
    "tx.status": ({"channel_id", "gateway_instance_id", "tx_id"}, set()),
}


def validate_params(method: str, params: dict) -> None:
    if method not in SCHEMAS:
        raise ProtocolError("UNKNOWN_METHOD", "此版本沒有指定的方法。")
    fields(params, *SCHEMAS[method])
    for name in ("channel_id", "subscription_id", "tx_id", "gateway_instance_id"):
        if name in params:
            identifier(params[name])
    if "expected" in params:
        expected = params["expected"]
        fields(expected, {"gateway_instance_id", "binding_revision", "stream_epoch"})
        identifier(expected["gateway_instance_id"])
        uint(expected["binding_revision"])
        if expected["stream_epoch"] is not None:
            identifier(expected["stream_epoch"])
    if "fencing_token" in params:
        uint(params["fencing_token"])
    if method == "channel.configure_rx":
        serial = params["serial"]
        fields(serial, set(), RX_SETTING_FIELDS)
        if not serial:
            raise ProtocolError("INVALID_PARAMS", "請指定至少一項 UART 參數。")
    if method == "hello":
        text(params["client_name"], 64)
        text(params["client_version"], 32)
        versions = params["versions"]
        if not isinstance(versions, list) or not 1 <= len(versions) <= 16:
            raise ProtocolError("INVALID_PARAMS", "版本清單不合法。")
        seen = set()
        for version in versions:
            fields(version, {"major", "minor"})
            pair = (version["major"], version["minor"])
            if any(type(n) is not int or not 0 <= n <= 65535 for n in pair) or pair in seen:
                raise ProtocolError("INVALID_PARAMS", "版本值不合法。")
            seen.add(pair)
    if method == "auth.authenticate":
        if params["scheme"] != "token" or not isinstance(params["token"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", params["token"]):
            raise ProtocolError("INVALID_PARAMS", "認證參數不合法。")
    if method == "channel.list":
        limit = params.get("limit", 16)
        if type(limit) is not int or not 1 <= limit <= 16:
            raise ProtocolError("INVALID_PARAMS", "分頁大小不合法。")
        if params.get("cursor") is not None:
            text(params["cursor"], 256)
    if method == "binding.confirm":
        text(params["label"], 128)
        if params.get("target_note") is not None:
            text(params["target_note"], 512, 0)
    if method == "rx.subscribe":
        if params["mode"] not in ("live", "resume") or params.get("gap_policy", "error") not in ("error", "skip"):
            raise ProtocolError("INVALID_PARAMS", "訂閱模式不合法。")
        if params["mode"] == "resume":
            if not {"stream_epoch", "from_offset"} <= params.keys():
                raise ProtocolError("INVALID_PARAMS", "恢復訂閱缺少位置。")
            identifier(params["stream_epoch"])
            uint(params["from_offset"])
        elif {"stream_epoch", "from_offset"} & params.keys() or params.get("gap_policy", "error") != "error":
            raise ProtocolError("INVALID_PARAMS", "即時訂閱不可指定恢復參數。")
