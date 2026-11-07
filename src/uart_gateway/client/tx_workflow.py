"""TX 人工工作流程；共用 SDK，沒有自動重試、Serial 或 UI 依賴。"""

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from uuid import uuid4

from uart_gateway.protocol.errors import ProtocolError, RemoteError
from uart_gateway.protocol.messages import uint
from .sdk import TxUncertain
from .tx_receipts import RESULT_FIELDS, TxJournal, TxReceipt, known_result, validate_result


def encode_payload(text: str, newline: str = "none", encoding: str = "text", maximum: int = 4096) -> bytes:
    if newline not in {"none", "lf", "cr", "crlf"} or encoding not in {"text", "hex"}:
        raise ValueError("請明確選擇文字／Hex 及換行方式。")
    if len(text) > 12288:
        raise ValueError("輸入超過大小上限。")
    if encoding == "text":
        if any(not char.isprintable() for char in text):
            raise ValueError("文字不可含隱藏控制字元；換行請選下方選項，二進位請使用 Hex。")
        payload = text.encode("utf-8")
    else:
        try:
            payload = bytes.fromhex(text)
        except ValueError:
            raise ValueError("Hex 請輸入成對十六進位數字，例如 55 AA 00。") from None
    payload += {"none": b"", "lf": b"\n", "cr": b"\r", "crlf": b"\r\n"}[newline]
    if not 1 <= len(payload) <= min(4096, maximum):
        raise ValueError(f"單次資料須為 1～{min(4096, maximum)} bytes（含換行），不會自動拆分。")
    return payload


def target_key(instance: str, channel: dict) -> tuple:
    adapter = channel.get("adapter") or {}
    return (instance, channel["channel_id"], channel["binding"]["revision"], channel["stream_epoch"],
        channel["label"], channel.get("target_note"), *(adapter.get(k) for k in ("port", "vid", "pid", "serial_number", "location")))


@dataclass(frozen=True)
class TxTarget:
    instance: str
    channel: dict
    principal: str
    maximum: int


class TxWorkflow:
    def __init__(self, connect, journal: TxJournal):
        self.connect, self.journal = connect, journal
        self.acknowledged: set[tuple] = set()
        self.busy = False
        self.last: TxReceipt | None = None
        self.notice = ""

    async def check(self, client, snapshot: dict, instance: str) -> dict:
        cid = snapshot["channel_id"]
        grant = next((g for g in client.auth["grants"] if g["channel_id"] == cid), None)
        if grant is None or not {"read", "configure", "tx"} <= set(grant["permissions"]):
            raise ValueError("此通道未授予完整 TX 操作權限；不會改用其他憑證。")
        current = await client.get_channel(cid)
        if client.instance != instance or target_key(instance, current) != target_key(instance, snapshot):
            raise ValueError("目標或資料流已改變；請更新並重新核對，不會送出。")
        if current["state"] != "ready" or current["tx"]["state"] in ("blocked", "quiescing"):
            raise ValueError("通道尚未就緒或 TX 已封鎖；請先處理狀態，不會自動復原。")
        return current

    async def prepare(self, snapshot: dict, instance: str) -> TxTarget:
        async with await self.connect() as client:
            current = await self.check(client, snapshot, instance)
            return TxTarget(instance, deepcopy(current), client.auth["principal_id"], client.hello["limits"]["max_tx_bytes"])

    def is_acknowledged(self, target: TxTarget) -> bool:
        return (target.principal, *target_key(target.instance, target.channel)) in self.acknowledged

    async def acknowledge(self, target: TxTarget) -> TxTarget:
        if self.busy:
            raise ValueError("TX 操作進行中，未排入第二個操作。")
        self.busy = True
        self.notice = ""
        try:
            async with await self.connect() as client:
                current = await self.check(client, target.channel, target.instance)
                if client.auth["principal_id"] != target.principal:
                    raise ValueError("操作身分已改變，請重新核對目標。")
                if current["binding"]["status"] != "confirmed":
                    try:
                        previous = current
                        current = await client.confirm(previous, previous["label"], previous.get("target_note"))
                        if (current["channel_id"] != previous["channel_id"] or current["state"] != "ready" or
                                current["binding"]["status"] != "confirmed" or
                                uint(current["binding"]["revision"]) != uint(previous["binding"]["revision"]) + 1 or
                                not current["stream_epoch"] or current["stream_epoch"] == previous["stream_epoch"] or
                                any(current.get(k) != previous.get(k) for k in ("adapter", "serial", "label", "target_note"))):
                            raise ProtocolError("INVALID_FRAME", "目標確認回覆不符；請先更新，不授予本工作區 TX 資格。")
                    except (OSError, TimeoutError, ProtocolError, asyncio.CancelledError):
                        self.notice = "目標確認結果未確認；請 R 查詢，不直接重送 Confirm。"
                        raise
                if len(self.acknowledged) >= 4:
                    self.acknowledged.clear()
                self.acknowledged.add((target.principal, *target_key(target.instance, current)))
                return TxTarget(target.instance, deepcopy(current), target.principal, target.maximum)
        finally:
            self.busy = False

    async def update(self, receipt: TxReceipt, result: dict, *, queried: bool = False) -> None:
        if (result.get("tx_id"), result.get("channel_id"), result.get("gateway_instance_id")) != (receipt.tx_id, receipt.channel_id, receipt.instance):
            raise ValueError("TX 結果識別不符；保留不明收據，不繼續判定成功。")
        try:
            filtered = {key: result[key] for key in RESULT_FIELDS}
        except (KeyError, TypeError):
            raise ProtocolError("INVALID_FRAME", "TX 回覆缺少必要欄位，保留待查收據，不重送。") from None
        validate_result(filtered)
        if queried:
            receipt.last_query = filtered
        known = known_result(receipt.result)
        if not (queried and known and filtered["status_origin"] == "unavailable"):
            if known and filtered != receipt.result:
                raise ProtocolError("INVALID_FRAME", "TX 終態與先前收據矛盾，保留原紀錄，不重送。")
            receipt.result = filtered
        await asyncio.to_thread(self.journal.save, receipt)

    async def send(self, target: TxTarget, payload: bytes, *, wait: float = 5) -> TxReceipt:
        if self.busy or not self.is_acknowledged(target):
            raise ValueError("操作進行中或目標未人工核對；本次未送出。")
        if not 1 <= len(payload) <= min(4096, target.maximum):
            raise ValueError("TX 大小不合法。")
        self.busy, self.notice = True, ""
        try:
            async with await self.connect() as client:
                current = await self.check(client, target.channel, target.instance)
                if client.auth["principal_id"] != target.principal or current["binding"]["status"] != "confirmed":
                    raise ValueError("操作身分或確認狀態已改變；未送出。")
                # 開始任何 write 前先持久保存識別；命令內容不進收據。
                lease = await client.acquire(current)
                try:
                    async with lease:
                        receipt = TxReceipt(uuid4().hex, current["channel_id"], client.instance, target.principal)
                        await asyncio.to_thread(self.journal.save, receipt, create=True)
                        self.last = receipt
                        try:
                            pending = await lease.write_bytes(payload, tx_id=receipt.tx_id)
                            await self.update(receipt, pending)
                            await self.update(receipt, await client.wait_tx(receipt.channel_id, receipt.tx_id, timeout=wait))
                        except (TxUncertain, TimeoutError, ConnectionError, ProtocolError):
                            self.notice = "結果未確認；請查詢結果，不重送原指令。"
                        except RemoteError as exc:
                            if exc.code != "INTERNAL_ERROR":
                                receipt.result.update(state="not_accepted", reason_code=exc.code, io_started=False, accepted_bytes=0)
                                await asyncio.to_thread(self.journal.save, receipt)
                            self.notice = "Gateway 未完成傳送要求；請查詢／核對原因，不自動重送。"
                finally:
                    if lease.failure:
                        self.notice += " 租約釋放／續約未確認；已關閉本次連線，不恢復舊租約。"
                return receipt
        except asyncio.CancelledError:
            self.notice = "TX 等待已中止，不能撤回已開始的傳送；請查詢收據，不重送。"
            raise
        finally:
            self.busy = False

    async def query(self, receipt: TxReceipt) -> TxReceipt:
        async with await self.connect() as client:
            if client.auth["principal_id"] != receipt.principal:
                raise ValueError("收據屬於另一個操作身分；請使用原 TX 憑證查詢。")
            result = await client.tx_status(receipt.channel_id, receipt.tx_id, instance=receipt.instance)
            await self.update(receipt, result, queried=True)
        if receipt.last_query["status_origin"] == "unavailable":
            self.notice = "本次查無 Gateway 紀錄；保留原收據，不能推論未執行，不重送。"
        else:
            self.notice = "查詢仍未確定，請人工核對，不重送。" if receipt.result["state"] in ("pending", "unknown") else ""
        return receipt
