"""有界 TX 收據；每個操作獨立檔案，不保存命令、Payload 或憑證。"""

from dataclasses import asdict, dataclass, field
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat

from uart_gateway.protected_files import private_directory, write_private
from uart_gateway.protocol.messages import identifier
from uart_gateway.protocol.messages import uint

RESULT_FIELDS = {"state", "submitted_bytes", "accepted_bytes", "io_started", "reason_code", "status_origin"}
FINAL = {"written_to_driver", "rejected", "not_accepted"}


def known_result(result: dict) -> bool:
    return (result["status_origin"] == "record" and result["state"] != "pending") or result["state"] == "not_accepted"


@dataclass
class TxReceipt:
    tx_id: str
    channel_id: str
    instance: str
    principal: str
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="milliseconds"))
    result: dict = field(default_factory=lambda: {"state": "unknown", "status_origin": "local",
        "reason_code": "RESPONSE_UNCONFIRMED", "submitted_bytes": None, "accepted_bytes": None, "io_started": None})
    last_query: dict | None = None
    execution: dict | None = None


class TxJournal:
    def __init__(self, directory: Path):
        self.directory = directory

    def path(self, receipt: TxReceipt) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", receipt.tx_id):
            raise ValueError("TX 收據識別不合法。")
        return self.directory / ("tx-" + receipt.tx_id + ".json")

    def list(self) -> list[TxReceipt]:
        if not self.directory.exists() and not self.directory.is_symlink():
            return []
        if self.directory.is_symlink():
            raise ValueError("TX 收據目錄不得是符號連結。")
        paths = []
        with os.scandir(self.directory) as entries:
            for count, entry in enumerate(entries):
                if count >= 256:
                    raise ValueError("TX 收據目錄超過掃描上限；請先封存舊檔，不會傳送 UART 資料。")
                if not re.fullmatch(r"tx-[0-9a-f]{32}\.json", entry.name):
                    continue
                paths.append(Path(entry.path))
        if len(paths) < 8:
            rows = [self._read(path) for path in paths]
        else:
            # 每份收據獨立驗證，重疊 Windows 冷檔案的開啟等待。
            with ThreadPoolExecutor(max_workers=4) as executor:
                rows = list(executor.map(self._read, paths))
        return sorted(rows, key=lambda r: (r.created, r.tx_id), reverse=True)

    def _read(self, path: Path) -> TxReceipt:
        try:
            if path.is_symlink():
                raise ValueError
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            with os.fdopen(os.open(path, flags), "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError
                raw = stream.read(4097)
            if len(raw) > 4096:
                raise ValueError
            value = json.loads(raw)
            required = {"tx_id", "channel_id", "instance", "principal", "created", "result"}
            if not required <= set(value) or set(value) - required - {"last_query", "execution"}:
                raise ValueError
            row = TxReceipt(**value)
            for item in (row.channel_id, row.instance, row.principal):
                identifier(item)
            if self.path(row).name != path.name or not isinstance(row.created, str) or len(row.created) > 40:
                raise ValueError
            datetime.fromisoformat(row.created)
            validate_result(row.result)
            if row.last_query is not None:
                validate_result(row.last_query)
            if row.execution is not None:
                validate_execution(row.execution)
        except (TypeError, ValueError, KeyError, RecursionError):
            raise ValueError("TX 收據損壞或權限不符；請先保留檔案並處理，不會自動重送。") from None
        return row

    def save(self, receipt: TxReceipt, *, create: bool = False) -> None:
        private_directory(self.directory)
        with self.locked():
            self._save(receipt, create=create)

    @contextmanager
    def locked(self):
        path = self.directory / ".journal.lock"
        if path.is_symlink():
            raise ValueError("TX 收據鎖不得是符號連結。")
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "r+b", buffering=0) as stream:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("TX 收據鎖必須為一般檔案。")
            try:
                if os.name == "nt":
                    import msvcrt
                    if os.fstat(descriptor).st_size == 0:
                        stream.write(b"\0")
                    stream.seek(0)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ValueError("另一個工作台正在保存 TX 收據；本次未重試，請稍後再操作。") from None
            yield

    def _save(self, receipt: TxReceipt, *, create: bool) -> None:
        for value in (receipt.channel_id, receipt.instance, receipt.principal):
            identifier(value)
        if not isinstance(receipt.created, str) or len(receipt.created) > 40:
            raise ValueError("TX 收據時間不合法。")
        datetime.fromisoformat(receipt.created)
        if receipt.execution is not None:
            validate_execution(receipt.execution)
        if create:
            rows = self.list()
            if receipt.execution is not None:
                for row in rows:
                    if (row.execution is not None and row.execution["state"] in ("pending", "unknown") and
                            (row.channel_id, row.instance, row.principal, row.execution["authorization_id"]) ==
                            (receipt.channel_id, receipt.instance, receipt.principal, receipt.execution["authorization_id"])):
                        raise ValueError("前次 Shell 命令尚未結案；請 exec-status 查詢，或由人確認 Shell 恢復後重新 shell-ready。")
            # 只淘汰已知終態；不明／pending 收據保留供人工核對。
            count = len(rows)
            for row in rows[63:]:
                if row.result["state"] in FINAL and (row.execution is None or row.execution["state"] in ("completed", "not_sent")):
                    self.path(row).unlink()
                    count -= 1
            if count >= 128:
                raise ValueError("未結案的 TX 收據已達上限，請先查詢／封存；本次未傳送。")
        validate_result(receipt.result)
        if receipt.last_query is not None:
            validate_result(receipt.last_query)
        if receipt.execution is not None:
            validate_execution(receipt.execution)
        path = self.path(receipt)
        if create and (path.exists() or path.is_symlink()):
            raise ValueError("TX 收據識別已存在；本次未送出。")
        if not create:
            previous = self._read(path) if path.exists() or path.is_symlink() else None
            if previous is not None:
                if (previous.channel_id, previous.instance, previous.principal) != (receipt.channel_id, receipt.instance, receipt.principal):
                    raise ValueError("既有收據的操作身分不符，未覆蓋。")
                if known_result(previous.result):
                    if known_result(receipt.result) and receipt.result != previous.result:
                        raise ValueError("收據終態互相矛盾，保留原檔，不推論成功。")
                    receipt.result = previous.result
                if receipt.last_query is None:
                    receipt.last_query = previous.last_query
                if previous.execution is not None:
                    if receipt.execution is None:
                        receipt.execution = previous.execution
                    elif any(previous.execution[k] != receipt.execution[k] for k in
                            ("authorization_id", "stream_epoch", "binding_revision", "start_offset", "output_limit")):
                        raise ValueError("Shell 收據脈絡不符，未覆蓋。")
                    elif previous.execution["state"] in ("completed", "not_sent"):
                        if (receipt.execution["state"] in ("completed", "not_sent") and
                                (receipt.execution["state"], receipt.execution["exit_code"]) !=
                                (previous.execution["state"], previous.execution["exit_code"])):
                            raise ValueError("Shell 完成證據矛盾，保留原紀錄。")
                        receipt.execution = previous.execution
        encoded = (json.dumps(asdict(receipt), ensure_ascii=False) + "\n").encode()
        if len(encoded) > 4096:
            raise ValueError("TX 收據超過大小上限，未覆寫。")
        write_private(path, encoded, replace=True)


def validate_result(result: dict) -> None:
    if (not isinstance(result, dict) or set(result) != RESULT_FIELDS or
            result["state"] not in {"pending", "written_to_driver", "rejected", "unknown", "not_accepted"} or
            result["status_origin"] not in {"local", "record", "unavailable"}):
        raise ValueError("TX 結果格式不合法。")
    for key in ("submitted_bytes", "accepted_bytes"):
        if result[key] is not None and (type(result[key]) is not int or not 0 <= result[key] <= 4096):
            raise ValueError("TX 結果數量不合法。")
    if result["io_started"] is not None and type(result["io_started"]) is not bool:
        raise ValueError("TX 結果狀態不合法。")
    if result["reason_code"] is not None and (not isinstance(result["reason_code"], str) or not re.fullmatch(r"[A-Z_]{1,64}", result["reason_code"])):
        raise ValueError("TX 結果原因不合法。")
    state = result["state"]
    if state == "written_to_driver" and (result["status_origin"] != "record" or not result["submitted_bytes"] or
            result["accepted_bytes"] != result["submitted_bytes"] or result["io_started"] is not True or result["reason_code"] is not None):
        raise ValueError("TX 成功結果欄位不一致，不能判定成功。")
    if state in {"rejected", "not_accepted"} and (result["io_started"] is not False or result["accepted_bytes"] != 0):
        raise ValueError("TX 拒絕結果欄位不一致，不能宣稱沒有寫入。")
    if state == "pending" and (result["status_origin"] != "record" or not result["submitted_bytes"] or result["accepted_bytes"] is not None):
        raise ValueError("TX 等待結果欄位不一致。")
    if result["status_origin"] == "unavailable" and (state != "unknown" or
            any(result[k] is not None for k in ("submitted_bytes", "accepted_bytes", "io_started"))):
        raise ValueError("查無 TX 紀錄不可推論已拒絕或成功。")


def validate_execution(value: dict) -> None:
    fields = {"authorization_id", "state", "reason_code", "exit_code", "stream_epoch", "binding_revision",
              "start_offset", "output_limit", "output_bytes", "output_truncated"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Shell 收據格式不合法。")
    if not isinstance(value["authorization_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", value["authorization_id"]):
        raise ValueError("Shell 授權識別不合法。")
    identifier(value["stream_epoch"])
    uint(value["binding_revision"])
    uint(value["start_offset"])
    if (value["state"] not in ("pending", "completed", "unknown", "not_sent") or
            type(value["output_limit"]) is not int or not 1 <= value["output_limit"] <= 1048576 or
            type(value["output_bytes"]) is not int or not 0 <= value["output_bytes"] < 2**64 or
            type(value["output_truncated"]) is not bool):
        raise ValueError("Shell 收據狀態或容量不合法。")
    code = value["exit_code"]
    if value["state"] == "completed":
        if type(code) is not int or not 0 <= code <= 255:
            raise ValueError("Shell exit code 不合法。")
    elif code is not None:
        raise ValueError("未完成的命令不可宣稱 exit code。")
    reason = value["reason_code"]
    if reason is not None and (not isinstance(reason, str) or not re.fullmatch(r"[A-Z_]{1,64}", reason)):
        raise ValueError("Shell 收據原因不合法。")
