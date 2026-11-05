"""每通道獨立資源、訂閱與單一傳送工作路徑。"""

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import replace
import hashlib
import logging
import threading
import time

from .lease import Lease
from .models import ChannelConfig, Chunk, Context, DomainError, Event, TxRecord, new_id, utc_now
from .ports import SerialPort


class TxStore:
    def __init__(self, instance: str, *, capacity: int = 4096,
                 retention: float = 600, clock: Callable[[], float] = time.monotonic):
        self.instance = instance
        self.capacity = capacity
        self.retention = retention
        self.clock = clock
        self.records: dict[tuple[str, str], TxRecord] = {}

    def lookup(self, principal: str, tx_id: str) -> TxRecord | None:
        key = (principal, tx_id)
        record = self.records.get(key)
        if record is not None and record.expires is not None and self.clock() >= record.expires:
            del self.records[key]
            return None
        return record

    def reserve(self, record: TxRecord) -> None:
        for key, value in list(self.records.items()):
            if value.expires is not None and self.clock() >= value.expires:
                del self.records[key]
        if len(self.records) >= self.capacity:
            raise DomainError("RESULT_CACHE_FULL", "結果快取已滿，未接受傳送。")
        self.records[(record.principal, record.id)] = record

    def finish(self, record: TxRecord, state: str, accepted: int | None, reason: str | None) -> None:
        if record.state != "pending":
            return
        record.state, record.accepted, record.reason = state, accepted, reason
        record.completed = utc_now()
        record.expires = self.clock() + self.retention


class Channel:
    def __init__(self, config: ChannelConfig, port: SerialPort, store: TxStore,
                 emit: Callable[[str, Event], None], *, ring_bytes: int = 4194304,
                 tx_items: int = 16, tx_bytes: int = 65536, lease_ttl: float = 15,
                 clock: Callable[[], float] = time.monotonic,
                 lifecycle_timeout: float = 10, observe: Callable[[Event], None] | None = None):
        self.config, self.port, self.store, self.emit = config, port, store, emit
        self.clock = clock
        self.gate = threading.RLock()
        self.lease = Lease(lease_ttl)
        self.revision = 1
        self.epoch: str | None = None
        self.state = "closed"
        self.confirmed = False
        self.blocked = False
        self.last_error: tuple[str, str] | None = None
        self.running = False
        self.inflight = False
        self.ring: deque[Chunk] = deque()
        self.ring_bytes, self.ring_used = ring_bytes, 0
        self.offset = self.sequence = 0
        self.subscriptions: dict[str, str] = {}
        self.queue: deque[tuple[TxRecord, bytes]] = deque()
        self.tx_items, self.tx_bytes, self.queued_bytes = tx_items, tx_bytes, 0
        self.wakeup = asyncio.Event()
        self.tasks: list[asyncio.Task] = []
        self.lifecycle_timeout = lifecycle_timeout
        self.operation: asyncio.Task | None = None
        self.observe = observe or (lambda event: None)

    async def _device_operation(self, function: Callable[[], None]) -> None:
        if self.operation is not None:
            if not self.operation.done():
                raise DomainError("CHANNEL_BUSY", "前次 Driver 操作尚未收斂。")
            if not self.operation.cancelled():
                self.operation.exception()
        self.operation = asyncio.create_task(asyncio.to_thread(function))
        done, _ = await asyncio.wait({self.operation}, timeout=self.lifecycle_timeout)
        if not done:
            self.last_error = ("SERIAL_IO_FAILED", "Driver 操作等待逾時；尚未保證停止，需人工復原。")
            self.state, self.confirmed, self.epoch = "faulted", False, None
            raise DomainError(*self.last_error)
        self.operation.result()
        self.operation = None

    async def _stop_workers(self) -> None:
        if self.tasks:
            _, pending = await asyncio.wait(self.tasks, timeout=self.lifecycle_timeout)
            if pending:
                self.state, self.confirmed, self.epoch = "faulted", False, None
                self.last_error = ("SERIAL_IO_FAILED", "Serial 工作尚未收斂，不允許重開或交接 Handle。")
                raise DomainError(*self.last_error)
            for task in self.tasks:
                task.result()
        self.tasks.clear()

    @property
    def context(self) -> Context:
        return Context(self.store.instance, self.revision, self.epoch)

    @property
    def earliest(self) -> int:
        return self.ring[0].offset if self.ring else self.offset

    def check_context(self, expected: Context) -> None:
        if expected.instance != self.store.instance:
            raise DomainError("INSTANCE_MISMATCH", "Gateway 已重新啟動。")
        if expected.revision != self.revision:
            raise DomainError("STALE_BINDING", "綁定修訂已改變。")
        if expected.epoch != self.epoch:
            raise DomainError("EPOCH_MISMATCH", "資料流世代已改變。",
                              expected_epoch=expected.epoch, actual_epoch=self.epoch)

    def _check_revision_capacity(self) -> None:
        if self.revision == 2**64 - 1:
            raise DomainError("CHANNEL_BUSY", "綁定修訂已耗盡，需重新啟動。")

    def event(self, kind: str, data: dict, *, context: Context | None = None,
              timestamp: str | None = None) -> Event:
        ctx = context or self.context
        return Event(kind, self.config.id, ctx.revision, ctx.epoch, data,
                     timestamp or utc_now())

    def _end_subscriptions(self, reason: str) -> set[str]:
        recipients = set(self.subscriptions.values())
        for sub_id, session in list(self.subscriptions.items()):
            self.emit(session, self.event("subscription_state", {
                "subscription_id": sub_id, "state": "ended", "reason": reason}))
        self.subscriptions.clear()
        return recipients

    def _new_epoch(self) -> None:
        self.epoch = new_id()
        self.ring.clear()
        self.ring_used = self.offset = self.sequence = 0
        self.observe(self.event("channel_state", {}))

    async def start(self) -> None:
        if self.state != "closed":
            raise DomainError("CHANNEL_BUSY", "請先 Rebind 關閉舊端點，再確認開啟。")
        self.state = "opening"
        self.confirmed = False
        try:
            await self._device_operation(self.port.open)
        except DomainError:
            self.observe(self.event("channel_state", {}))
            raise
        except OSError as exc:
            code = getattr(exc, "code", "SERIAL_IO_FAILED")
            self.state = "lost" if code == "DEVICE_MISSING" else "faulted"
            self.last_error = (code, str(exc) if hasattr(exc, "code") else "裝置開啟失敗。")
            self.observe(self.event("channel_state", {}))
            raise DomainError(*self.last_error) from exc
        self.last_error = None
        self.state, self.running = "ready", True
        self._new_epoch()
        self.tasks = [asyncio.create_task(self._reader()), asyncio.create_task(self._writer())]

    async def stop(self) -> None:
        with self.gate:
            self.running = False
            self.confirmed = False
            self.revoke("server_shutdown", "SERVER_SHUTDOWN")
            self._end_subscriptions("server_shutdown")
            self.wakeup.set()
        try:
            await self._stop_workers()
            await self._device_operation(self.port.close)
            self.state = "closed"
        except (OSError, DomainError):
            self.state = "faulted"
            self.last_error = ("SERIAL_IO_FAILED", "裝置關閉失敗。")
        self.epoch = None
        self.observe(self.event("channel_state", {}))

    def confirm(self, expected: Context, label: str, note: str | None) -> set[str]:
        with self.gate:
            self.check_context(expected)
            self._check_revision_capacity()
            if self.confirmed:
                raise DomainError("INVALID_STATE", "綁定已經確認。")
            if self.state != "ready":
                raise DomainError("CHANNEL_NOT_READY", "裝置尚未就緒。")
            recipients = self._end_subscriptions("epoch_changed")
            self.config = replace(self.config, label=label, target_note=note)
            self.revision += 1
            self.confirmed, self.blocked = True, False
            self._new_epoch()
            return recipients

    async def rebind(self, expected: Context) -> set[str]:
        with self.gate:
            self.check_context(expected)
            self._check_revision_capacity()
            self.expire()
            if self.operation is not None and not self.operation.done():
                raise DomainError("CHANNEL_BUSY", "前次 Driver 操作尚未收斂。")
            if self.lease.owner:
                raise DomainError("LEASE_BUSY", "請先釋放傳送租約。")
            if self.inflight or self.state in ("opening", "draining"):
                raise DomainError("CHANNEL_BUSY", "裝置操作尚未結束。")
            recipients = self._end_subscriptions("epoch_changed")
            self.revision += 1
            self.confirmed = False
            self.state, self.running = "draining", False
            self.wakeup.set()
        await self._stop_workers()
        try:
            await self._device_operation(self.port.close)
        except OSError as exc:
            self.state, self.epoch = "faulted", None
            self.last_error = ("SERIAL_IO_FAILED", "裝置關閉失敗。")
            raise DomainError(*self.last_error) from exc
        self.state, self.epoch = "closed", None
        self.ring.clear()
        self.ring_used = 0
        self.observe(self.event("channel_state", {}))
        return recipients

    def expire(self) -> None:
        with self.gate:
            if self.lease.owner and self.clock() >= self.lease.deadline:
                self.revoke("expired", "LEASE_EXPIRED")

    def revoke(self, reason: str, tx_reason: str) -> None:
        with self.gate:
            owner = self.lease.owner
            self.lease.owner = None
            if owner is not None:
                self.emit(owner, self.event("lease_revoked", {
                    "fencing_token": str(self.lease.token), "reason": reason}))
            while self.queue:
                record, payload = self.queue.popleft()
                self.queued_bytes -= len(payload)
                self._finish(record, "rejected", 0, tx_reason)

    def disconnect(self, session: str) -> None:
        with self.gate:
            self.subscriptions = {sid: owner for sid, owner in self.subscriptions.items() if owner != session}
            if self.lease.owner == session:
                self.revoke("session_closed", "SESSION_CLOSED")

    def _can_write(self) -> None:
        if self.blocked:
            raise DomainError("TX_BLOCKED", "前次寫入結果不明，需人工復原。")
        if self.state != "ready" or not self.epoch:
            raise DomainError("CHANNEL_NOT_READY", "通道尚未就緒。")
        if not self.confirmed:
            raise DomainError("NOT_BOUND", "請先確認目前接線目標。")

    def acquire(self, session: str, expected: Context) -> dict:
        with self.gate:
            self.check_context(expected)
            self.expire()
            self._can_write()
            if self.inflight and self.lease.owner != session:
                raise DomainError("CHANNEL_BUSY", "前次寫入尚未結束。")
            self.lease.acquire(session, self.clock())
            return self.lease_result()

    def lease_result(self) -> dict:
        return {"channel_id": self.config.id, "fencing_token": str(self.lease.token),
                "ttl_ms": int(self.lease.ttl * 1000),
                "remaining_ms": max(0, int((self.lease.deadline - self.clock()) * 1000))}

    def renew(self, session: str, expected: Context, token: int) -> dict:
        with self.gate:
            self.check_context(expected)
            self._can_write()
            self.expire()
            self.lease.renew(session, token, self.clock())
            return self.lease_result()

    def release(self, session: str, expected: Context, token: int) -> dict:
        with self.gate:
            self.check_context(expected)
            self.expire()
            self.lease.check(session, token, self.clock())
            self.revoke("released", "LEASE_RELEASED")
            return {"released": True, "tx_state": "quiescing" if self.inflight else "idle"}

    def subscribe(self, session: str, *, epoch: str | None = None, offset: int | None = None,
                  skip: bool = False) -> tuple[dict, list[Event]]:
        if self.state != "ready":
            raise DomainError("CHANNEL_NOT_READY", "通道尚未就緒。")
        if offset is not None and epoch != self.epoch:
            raise DomainError("EPOCH_MISMATCH", "不能跨世代恢復訂閱。",
                              expected_epoch=epoch, actual_epoch=self.epoch)
        start = self.offset if offset is None else offset
        if start > self.offset:
            raise DomainError("OFFSET_OUT_OF_RANGE", "讀取位置超過資料尾端。")
        if start < self.earliest and not skip:
            raise DomainError("HISTORY_GAP", "所需資料已被淘汰。",
                              earliest_offset=str(self.earliest), next_offset=str(self.offset))
        sub_id = new_id()
        events = []
        if start < self.earliest:
            events.append(self.event("gap", {"subscription_id": sub_id, "reason": "history_evicted",
                "from_offset": str(start), "to_offset": str(self.earliest),
                "continuity": "known_gap", "action": "skip"}))
            start = self.earliest
        for chunk in self.ring:
            if chunk.offset + len(chunk.data) > start:
                events.append(self._rx_event(sub_id, chunk, max(start, chunk.offset), "replay"))
        self.subscriptions[sub_id] = session
        return {"subscription_id": sub_id, "channel_id": self.config.id,
                "binding_revision": str(self.revision), "stream_epoch": self.epoch,
                "start_offset": str(start), "replay_until_offset": str(self.offset),
                "earliest_offset": str(self.earliest)}, events

    def _rx_event(self, sub_id: str, chunk: Chunk, offset: int, delivery: str) -> Event:
        return self.event("rx", {"subscription_id": sub_id, "seq": str(chunk.seq),
            "chunk_offset": str(chunk.offset), "offset": str(offset),
            "next_offset": str(chunk.offset + len(chunk.data)), "delivery": delivery,
            "payload": chunk.data[offset - chunk.offset:]}, timestamp=chunk.timestamp)

    def ingest(self, payload: bytes) -> None:
        if not payload or self.state != "ready":
            return
        if self.offset + len(payload) > 2**64 - 1 or self.sequence == 2**64 - 1:
            self.revoke("epoch_changed", "EPOCH_MISMATCH")
            self._end_subscriptions("epoch_changed")
            self._new_epoch()
        chunk = Chunk(self.offset, self.sequence, payload)
        self.offset += len(payload)
        self.sequence += 1
        self.observe(self.event("rx", {"seq": str(chunk.seq), "offset": str(chunk.offset),
            "next_offset": str(self.offset), "payload": chunk.data}, timestamp=chunk.timestamp))
        self.ring.append(chunk)
        self.ring_used += len(payload) + 256
        while self.ring and self.ring_used > self.ring_bytes:
            self.ring_used -= len(self.ring[0].data) + 256
            self.ring.popleft()
        for sub_id, session in list(self.subscriptions.items()):
            self.emit(session, self._rx_event(sub_id, chunk, chunk.offset, "live"))

    async def _reader(self) -> None:
        while self.running:
            try:
                payload = await asyncio.to_thread(self.port.read, 4096)
                if self.running:
                    self.ingest(payload)
            except OSError:
                if self.running:
                    with self.gate:
                        recipients = self._end_subscriptions("device_lost")
                        if self.lease.owner:
                            recipients.add(self.lease.owner)
                        self.revoke("device_lost", "DEVICE_MISSING")
                        self.confirmed = False
                        self.revision = min(self.revision + 1, 2**64 - 1)
                        self.state, self.epoch, self.running = "lost", None, False
                        self.last_error = ("DEVICE_MISSING", "裝置已失去連線。")
                        self.wakeup.set()
                    self.observe(self.event("channel_state", {}))
                    for session in recipients:
                        self.emit(session, self.event("channel_state", {}))
                break

    def submit(self, principal: str, session: str, expected: Context, token: int,
               tx_id: str, payload: bytes) -> TxRecord:
        fingerprint = hashlib.sha256(repr((self.config.id, expected)).encode() + b"\0" + payload).digest()
        old = self.store.lookup(principal, tx_id)
        if old is not None:
            if old.fingerprint != fingerprint:
                raise DomainError("TX_ID_CONFLICT", "傳送識別已用於其他內容。")
            return old
        with self.gate:
            self.check_context(expected)
            self._can_write()
            self.expire()
            self.lease.check(session, token, self.clock())
            if len(self.queue) >= self.tx_items or self.queued_bytes + len(payload) > self.tx_bytes:
                raise DomainError("QUEUE_FULL", "傳送佇列已滿，未接受傳送。")
            record = TxRecord(tx_id, principal, session, self.config.id, expected, token, fingerprint, len(payload))
            self.store.reserve(record)
            self.queue.append((record, payload))
            self.queued_bytes += len(payload)
            self.wakeup.set()
            return record

    def _perform_write(self, record: TxRecord, payload: bytes) -> tuple[str, int | None, str | None]:
        with self.gate:
            try:
                self.check_context(record.context)
                self._can_write()
                self.lease.check(record.session, record.token, self.clock())
            except DomainError as exc:
                return "rejected", 0, exc.code
            record.started, self.inflight = True, True
        try:
            count = self.port.write(payload)
            if type(count) is not int or not 0 <= count <= len(payload):
                result = ("unknown", None, "SERIAL_WRITE_FAILED")
            elif count != len(payload):
                result = ("unknown", count, "PARTIAL_WRITE")
            else:
                result = ("written_to_driver", count, None)
        except TimeoutError:
            result = ("unknown", None, "SERIAL_WRITE_TIMEOUT")
        except OSError:
            result = ("unknown", None, "SERIAL_WRITE_FAILED")
        except Exception:
            logging.getLogger(__name__).exception("Serial Backend 發生未預期的寫入錯誤")
            result = ("unknown", None, "SERIAL_WRITE_FAILED")
        finally:
            with self.gate:
                self.inflight = False
                # 結果未知時先封鎖，再把完成通知交回事件迴圈。
                if result[0] == "unknown":
                    self.blocked = True
        return result

    def _finish(self, record: TxRecord, state: str, count: int | None, reason: str | None) -> None:
        self.store.finish(record, state, count, reason)
        self.emit(record.session, self.event("tx_result", {"record": record}, context=record.context))

    async def _writer(self) -> None:
        while self.running or self.queue:
            await self.wakeup.wait()
            self.wakeup.clear()
            while self.queue:
                record, payload = self.queue.popleft()
                self.queued_bytes -= len(payload)
                result = await asyncio.to_thread(self._perform_write, record, payload)
                self._finish(record, *result)
                if result[0] == "unknown":
                    self.revoke("tx_unknown", "LEASE_RELEASED")
