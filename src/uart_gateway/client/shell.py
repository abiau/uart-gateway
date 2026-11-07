"""已登入 POSIX Shell 的單次執行；租約／RX 證據與 TX 結果分開。"""

import asyncio
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import re
from uuid import uuid4

from uart_gateway.protected_files import read_private, write_private
from uart_gateway.protocol.codec import binary_encode
from uart_gateway.protocol.errors import ProtocolError, RemoteError
from uart_gateway.protocol.messages import identifier, uint
from .rx import RXCursor, subscribe_checked
from .sdk import TxUncertain
from .shell_marker import ShellMarkers, shell_payload
from .tx_receipts import TxJournal, TxReceipt
from .tx_workflow import TxTarget, TxWorkflow

ERRORS = (OSError, ValueError, ProtocolError, RemoteError, TimeoutError)


class ShellFailure(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ShellAuthorization:
    generation: str
    instance: str
    principal: str
    channel: dict
    newline: str


class ShellTargets:
    def __init__(self, directory: Path):
        self.directory = directory

    def path(self, channel_id: str) -> Path:
        identifier(channel_id)
        return self.directory / (channel_id + ".json")

    def save(self, value: ShellAuthorization) -> None:
        encoded = json.dumps(asdict(value), ensure_ascii=False).encode()
        if len(encoded) > 8192:
            raise ValueError("Shell 目標授權超過大小上限，未保存。")
        write_private(self.path(value.channel["channel_id"]), encoded, replace=True)

    def load(self, channel_id: str) -> ShellAuthorization:
        try:
            raw = json.loads(read_private(self.path(channel_id), 8192))
            if set(raw) != {"generation", "instance", "principal", "channel", "newline"}:
                raise ValueError
            value = ShellAuthorization(**raw)
            if not re.fullmatch(r"[0-9a-f]{32}", value.generation) or value.newline not in ("lf", "cr", "crlf"):
                raise ValueError
            identifier(value.instance)
            identifier(value.principal)
            c = value.channel
            required = {"channel_id", "label", "target_note", "binding", "stream_epoch", "adapter", "serial"}
            if (not isinstance(c, dict) or set(c) != required or not isinstance(c["label"], str) or
                    c["target_note"] is not None and not isinstance(c["target_note"], str) or
                    not isinstance(c["binding"], dict) or not isinstance(c["serial"], dict) or
                    c["adapter"] is not None and not isinstance(c["adapter"], dict)):
                raise ValueError
            identifier(value.channel["stream_epoch"])
            uint(value.channel["binding"]["revision"])
            if value.channel["channel_id"] != channel_id or value.channel["binding"]["status"] != "confirmed":
                raise ValueError
            return value
        except (FileNotFoundError, ValueError, TypeError, KeyError, RecursionError, ProtocolError) as exc:
            raise ShellFailure("SHELL_NOT_READY", "尚無有效的 Shell 目標授權；請由人執行 uart shell-ready 核對。") from exc


@dataclass
class ExecutionResult:
    receipt: TxReceipt
    parser: ShellMarkers | None
    reason: str | None = None
    continuous: bool = True
    receipt_saved: bool = True

    def as_dict(self) -> dict:
        meta = self.receipt.execution
        available = self.parser is not None and self.parser.started
        output = self.parser.output if available else None
        return {"execution_id": self.receipt.tx_id, "channel_id": self.receipt.channel_id,
            "gateway_instance_id": self.receipt.instance, "state": meta["state"], "exit_code": meta["exit_code"],
            "output_text": output.decode("utf-8", errors="backslashreplace") if output is not None else None,
            "output_b64": binary_encode(output) if output is not None else None,
            "output_complete": bool(available and self.parser.done and self.continuous and not self.parser.truncated),
            "output_truncated": self.parser.truncated if available else meta["output_truncated"],
            "observed_output_bytes": meta["output_bytes"], "returned_bytes": len(output) if output is not None else 0,
            "output_scope": "uart_between_markers", "background_output_possible": True,
            "may_still_be_running": meta["state"] in ("pending", "unknown"),
            "reason_code": self.reason or meta["reason_code"], "receipt_saved": self.receipt_saved,
            "tx_result": self.receipt.result, "tx_query": self.receipt.last_query}

    @property
    def process_exit_code(self) -> int:
        if self.receipt.execution["state"] == "completed":
            return 0 if self.receipt.execution["exit_code"] == 0 else 1
        return 2


class ShellCancelled(asyncio.CancelledError):
    def __init__(self, result: ExecutionResult):
        super().__init__("Shell 等待已中止；不能撤回已開始的命令。")
        self.result = result


class ShellRunner:
    def __init__(self, connect, journal: TxJournal, targets: ShellTargets):
        self.connect, self.journal, self.targets = connect, journal, targets
        self.tx = TxWorkflow(connect, journal)
        self.last: TxReceipt | None = None
        self.busy = False

    async def inspect(self, channel_id: str) -> TxTarget:
        async with await self.connect() as client:
            snapshot = await client.get_channel(channel_id)
            instance = client.instance
        return await self.tx.prepare(snapshot, instance)

    async def authorize(self, target: TxTarget, newline: str) -> ShellAuthorization:
        """呼叫者必須已人工核對接線、已登入 Shell 與換行；本方法不送探測命令。"""
        if newline not in ("lf", "cr", "crlf"):
            raise ValueError("請明確指定 Shell 換行方式。")
        confirmed = await self.tx.acknowledge(target)
        compact = {key: confirmed.channel[key] for key in ("channel_id", "label", "target_note", "binding", "stream_epoch", "adapter", "serial")}
        authorization = ShellAuthorization(uuid4().hex, target.instance, target.principal, compact, newline)
        await asyncio.to_thread(self.targets.save, authorization)
        return authorization

    async def _current(self, client, authorization):
        if client.auth["principal_id"] != authorization.principal:
            raise ShellFailure("PRINCIPAL_CHANGED", "Shell 授權屬於另一個身分，請由人重新核對。")
        current = await self.tx.check(client, authorization.channel, authorization.instance)
        if current["binding"]["status"] != "confirmed":
            raise ShellFailure("TARGET_CHANGED", "目標不再 Confirm；不自動恢復。")
        return current

    async def _collect(self, client, subscription, parser, *, lease=None, replay_only=False):
        cursor = RXCursor(subscription["channel_id"], subscription)
        watermark = uint(subscription["replay_until_offset"])
        while True:
            if replay_only and cursor.next_offset >= watermark:
                return
            event = await client.next_event()
            data = cursor.receive(event)
            if data is not None:
                parser.feed(data)
                if parser.done:
                    return
            elif event["event"] == "subscription_state" and event["data"]["subscription_id"] == subscription["subscription_id"]:
                raise ShellFailure("STREAM_ENDED", "資料流已停止，無法證明命令完成。")
            elif event["event"] == "channel_state":
                c = event["data"]["channel"]
                if c["state"] != "ready" or (c["binding"]["revision"], c["stream_epoch"]) != (subscription["binding_revision"], subscription["stream_epoch"]):
                    raise ShellFailure("CONTEXT_CHANGED", "命令期間目標或世代已改變。")
            elif event["event"] == "lease_revoked" and lease is not None and event["data"]["fencing_token"] == lease.token:
                raise ShellFailure("LEASE_LOST", "租約已撤銷，不會重新取得或重送。")
            await asyncio.sleep(0)

    async def _lease_watch(self, lease):
        while lease.active and lease.failure is None:
            await asyncio.sleep(0.05)
        raise ShellFailure("LEASE_LOST", "租約續約失敗，不會重新取得或重送。")

    async def _write(self, client, lease, payload, receipt, timeout, observed):
        try:
            observed["result"] = await lease.write_bytes(payload, tx_id=receipt.tx_id)
            if observed["result"]["state"] == "pending":
                observed["result"] = await client.wait_tx(receipt.channel_id, receipt.tx_id, timeout=min(5, timeout))
        except (TxUncertain, TimeoutError, ConnectionError, ProtocolError):
            return
        except RemoteError as exc:
            if exc.code != "INTERNAL_ERROR":
                observed["result"] = {"tx_id": receipt.tx_id, "channel_id": receipt.channel_id,
                    "gateway_instance_id": receipt.instance, "state": "not_accepted", "submitted_bytes": None,
                    "accepted_bytes": 0, "io_started": False, "reason_code": exc.code, "status_origin": "local"}

    async def _save_outcome(self, receipt, parser, reason, continuous, observed=None):
        saved = True
        if observed is not None:
            try:
                await self.tx.update(receipt, observed)
            except ERRORS:
                saved = False
        meta = receipt.execution
        # 查詢失敗不能抹除先前的完成證據；新的完整標記仍可結案未知操作。
        if meta["state"] not in ("completed", "not_sent"):
            if receipt.result["state"] in ("rejected", "not_accepted"):
                meta.update(state="not_sent", exit_code=None, reason_code=receipt.result["reason_code"])
            elif parser.done and continuous:
                meta.update(state="completed", exit_code=parser.exit_code, reason_code=None)
            else:
                meta.update(state="unknown", exit_code=None, reason_code=reason or "COMPLETION_NOT_OBSERVED")
            meta.update(output_bytes=parser.observed_bytes, output_truncated=parser.truncated)
        if parser.done and continuous:
            reason = None
        try:
            await asyncio.to_thread(self.journal.save, receipt)
        except ERRORS:
            saved = False
        if not saved:
            reason = "RECEIPT_SAVE_FAILED"
        return ExecutionResult(receipt, parser, reason, continuous, saved)

    async def execute(self, channel_id: str, command: str, *, timeout: float = 10, maximum: int = 65536) -> ExecutionResult:
        if self.busy:
            raise ShellFailure("CLIENT_BUSY", "此執行器已有命令進行中，未排入第二次傳送。")
        self.busy = True
        try:
            return await self._execute(channel_id, command, timeout=timeout, maximum=maximum)
        finally:
            self.busy = False

    async def _execute(self, channel_id, command, *, timeout, maximum):
        if not math.isfinite(timeout) or not 0.1 <= timeout <= 300:
            raise ValueError("命令等待須為 0.1～300 秒。")
        self.last = None
        authorization = await asyncio.to_thread(self.targets.load, channel_id)
        execution_id = uuid4().hex
        parser = ShellMarkers(execution_id, maximum)
        payload = shell_payload(command, execution_id, authorization.newline)
        cancelled = False
        async with await self.connect() as client:
            current = await self._current(client, authorization)
            if len(payload) > client.hello["limits"]["max_tx_bytes"]:
                raise ValueError("命令含包裝超過 Gateway 單次 TX 上限，不會送出。")
            async with await client.acquire(current) as lease:
                subscription = None
                try:
                    expected = (authorization.instance, current["binding"]["revision"], current["stream_epoch"])
                    subscription = await subscribe_checked(client, channel_id, expected_context=expected)
                    receipt = TxReceipt(execution_id, channel_id, client.instance, authorization.principal, execution={
                        "authorization_id": authorization.generation, "state": "pending", "reason_code": None, "exit_code": None,
                        "stream_epoch": current["stream_epoch"], "binding_revision": current["binding"]["revision"],
                        "start_offset": subscription["start_offset"], "output_limit": maximum, "output_bytes": 0, "output_truncated": False})
                    await asyncio.to_thread(self.journal.save, receipt, create=True)
                    self.last = receipt
                    observed, reason, continuous = {}, None, True
                    collector = asyncio.create_task(self._collect(client, subscription, parser, lease=lease))
                    writer = asyncio.create_task(self._write(client, lease, payload, receipt, timeout, observed))
                    watcher = asyncio.create_task(self._lease_watch(lease))
                    tasks = [collector, writer, watcher]
                    pending = set(tasks)
                    try:
                        async with asyncio.timeout(timeout):
                            while not collector.done():
                                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                                for task in done:
                                    await task
                                if writer.done() and observed.get("result", {}).get("state") in ("rejected", "not_accepted"):
                                    break
                            if collector.done():
                                await collector
                    except asyncio.CancelledError:
                        reason, cancelled = "CANCELLED", True
                    except TimeoutError:
                        reason = "COMMAND_TIMEOUT"
                    except ERRORS as exc:
                        reason = getattr(exc, "code", "CONNECTION_LOST")
                        continuous = False
                    finally:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                    result = await self._save_outcome(receipt, parser, reason, continuous, observed.get("result"))
                finally:
                    if subscription is not None:
                        try:
                            await client.unsubscribe(subscription["subscription_id"])
                        except ERRORS:
                            pass  # 連線亦會關閉；不把清理失敗當成命令成功證據。
        if cancelled:
            raise ShellCancelled(result)
        return result

    async def status(self, execution_id: str, *, wait: float = 0) -> ExecutionResult:
        if not math.isfinite(wait) or not 0 <= wait <= 300:
            raise ValueError("查詢等待須為 0～300 秒。")
        receipt = next((r for r in await asyncio.to_thread(self.journal.list) if r.tx_id == execution_id), None)
        if receipt is None or receipt.execution is None:
            raise ShellFailure("EXECUTION_NOT_FOUND", "找不到此 Shell 執行收據；不會重新傳送。")
        parser = ShellMarkers(execution_id, receipt.execution["output_limit"])
        self.last = receipt
        reason, continuous, cancelled = None, True, False
        try:
            async with await self.connect() as client:
                if client.auth["principal_id"] != receipt.principal:
                    raise ShellFailure("PRINCIPAL_CHANGED", "請使用原 TX 身分查詢。")
                await self.tx.update(receipt, await client.tx_status(receipt.channel_id, execution_id, instance=receipt.instance), queried=True)
                if client.instance != receipt.instance:
                    raise ShellFailure("CONTEXT_CHANGED", "Gateway 已重新啟動，無法還原原次輸出。")
                subscription = None
                try:
                    async with asyncio.timeout(5):
                        subscription = await client.subscribe(receipt.channel_id, epoch=receipt.execution["stream_epoch"], offset=receipt.execution["start_offset"])
                        if (subscription["binding_revision"], subscription["stream_epoch"]) != (
                                receipt.execution["binding_revision"], receipt.execution["stream_epoch"]):
                            raise ShellFailure("CONTEXT_CHANGED", "原資料流已改變，無法還原輸出。")
                        await self._collect(client, subscription, parser, replay_only=True)
                    if wait and not parser.done:
                        async with asyncio.timeout(wait):
                            await self._collect(client, {**subscription, "start_offset": subscription["replay_until_offset"]}, parser)
                finally:
                    if subscription is not None:
                        try:
                            await client.unsubscribe(subscription["subscription_id"])
                        except ERRORS:
                            pass  # 只查詢，不會因此重送命令。
        except asyncio.CancelledError:
            reason, continuous, cancelled = "CANCELLED", False, True
        except ERRORS as exc:
            reason, continuous = getattr(exc, "code", "QUERY_UNAVAILABLE"), False
        result = await self._save_outcome(receipt, parser, reason, continuous)
        if cancelled:
            raise ShellCancelled(result)
        return result
