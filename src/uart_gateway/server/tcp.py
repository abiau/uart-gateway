"""V1 TCP 派送；所有裝置操作經通道核心。"""

import asyncio
from collections.abc import Callable
import logging
import time

from uart_gateway.core.channel import Channel, TxStore
from uart_gateway.core.models import ChannelConfig, Context, DomainError, Event, new_id, utc_now
from uart_gateway.core.ports import SerialPort
from uart_gateway.protocol.codec import binary_decode, binary_encode, read_frame
from uart_gateway.protocol.errors import ProtocolError
from uart_gateway.protocol.messages import request, uint, validate_params
from .auth import Principal, authenticate
from .config import Limits
from .session import Session
from .views import event_message, snapshot, tx_result, unavailable
from .recording import Recorder, RecordingConfig

LOG = logging.getLogger(__name__)


def context(params: dict) -> Context:
    value = params["expected"]
    return Context(value["gateway_instance_id"], int(value["binding_revision"]), value["stream_epoch"])


class Gateway:
    def __init__(self, channels: list[tuple[ChannelConfig, SerialPort]], principals: tuple[Principal, ...],
                 *, limits: Limits | None = None, clock: Callable[[], float] = time.monotonic,
                 max_clients: int = 8, connection_queue_bytes: int = 1048576,
                 subscription_queue_bytes: int = 262144, lifecycle_timeout: float = 10,
                 recording: RecordingConfig | None = None):
        if not channels or len(channels) > 4 or len({c.id for c, _ in channels}) != len(channels):
            raise ValueError("通道數量或識別不合法")
        if len({c.label for c, _ in channels}) != len(channels) or not principals:
            raise ValueError("通道名稱重複或未設定認證")
        ids = {c.id for c, _ in channels}
        if any(set(p.grants) - ids for p in principals) or len({p.id for p in principals}) != len(principals):
            raise ValueError("認證身分或授權通道不合法")
        if len({p.token for p in principals}) != len(principals):
            raise ValueError("不同身分不得共用 Token")
        self.limits, self.principals, self.clock = limits or Limits(), principals, clock
        self.store = TxStore(new_id(), capacity=self.limits.tx_result_cache_entries,
                             retention=self.limits.tx_result_retention_ms / 1000, clock=clock)
        self.sessions: dict[str, Session] = {}
        self.pending_labels: dict[str, str] = {}
        self.channels = {cfg.id: Channel(cfg, port, self.store, self.notify,
            ring_bytes=self.limits.rx_ring_bytes, tx_items=self.limits.max_tx_queue_items,
            tx_bytes=self.limits.max_tx_queue_bytes, lease_ttl=self.limits.lease_ttl_ms / 1000,
            clock=clock, lifecycle_timeout=lifecycle_timeout, observe=self._observe) for cfg, port in channels}
        self.recorders = {cid: Recorder(recording, self.store.instance, cid,
            lambda health, cid=cid: self._recording_changed(cid, health)) for cid in self.channels} if recording else {}
        self.max_clients = max_clients
        self.connection_queue_bytes, self.subscription_queue_bytes = connection_queue_bytes, subscription_queue_bytes
        self.handlers: set[asyncio.Task] = set()
        self.listener: asyncio.Server | None = None
        self.maintenance: asyncio.Task | None = None

    def notify(self, session_id: str, event: Event) -> None:
        session = self.sessions.get(session_id)
        if session is not None and not session.closed:
            if event.kind == "subscription_state":
                session.drop_subscription(event.data["subscription_id"])
            recorder = self.recorders.get(event.channel_id)
            session.send(event_message(event, self.channels[event.channel_id], recording=recorder.health if recorder else None))

    def snapshot(self, channel: Channel) -> dict:
        recorder = self.recorders.get(channel.config.id)
        return snapshot(channel, recording=recorder.health if recorder else None)

    def _recording_changed(self, cid: str, health: dict) -> None:
        channel = self.channels[cid]
        recipients = set(channel.subscriptions.values())
        if channel.lease.owner:
            recipients.add(channel.lease.owner)
        for session in recipients:
            self.notify(session, channel.event("recording_state", health))

    def _observe(self, event: Event) -> None:
        recorder = self.recorders.get(event.channel_id)
        if recorder is None:
            return
        if event.kind == "rx":
            recorder.rx({"timestamp_utc": event.timestamp, "binding_revision": str(event.revision),
                "stream_epoch": event.epoch, "seq": event.data["seq"], "offset": event.data["offset"],
                "next_offset": event.data["next_offset"], "data_b64": binary_encode(event.data["payload"])})
        elif event.kind == "channel_state":
            recorder.channel_state(self.snapshot(self.channels[event.channel_id]))

    async def start(self, host: str = "127.0.0.1", port: int = 18081) -> int:
        if host != "127.0.0.1":
            raise ValueError("此版本只允許 Loopback 接聽")
        try:
            # 先保留接聽端點，避免 TCP Port 已占用時仍開啟 Serial。
            self.listener = await asyncio.start_server(self._accept, host, port,
                limit=self.limits.max_frame_bytes - 1, start_serving=False, reuse_address=False)
            for cid, recorder in self.recorders.items():
                await recorder.start(self.snapshot(self.channels[cid]))
            for channel in self.channels.values():
                if channel.config.auto_open_rx:
                    try:
                        await channel.start()
                    except DomainError as exc:
                        LOG.warning("通道 %s 開啟失敗：%s", channel.config.id, exc.code)
            await self.listener.start_serving()
        except BaseException:
            await self.close()
            raise
        self.maintenance = asyncio.create_task(self._maintain())
        return self.listener.sockets[0].getsockname()[1]

    async def _maintain(self) -> None:
        while True:
            for channel in self.channels.values():
                channel.expire()
            await asyncio.sleep(0.05)

    async def close(self) -> None:
        if self.listener:
            self.listener.close()
        if self.maintenance:
            self.maintenance.cancel()
            await asyncio.gather(self.maintenance, return_exceptions=True)
        for session in list(self.sessions.values()):
            session.abort()
        if self.handlers:
            await asyncio.gather(*list(self.handlers), return_exceptions=True)
        if self.listener:
            # Python 的 Server.wait_closed 可能等待所有已接受連線；先關閉 Session。
            await self.listener.wait_closed()
        for channel in self.channels.values():
            await channel.stop()
        await asyncio.gather(*(recorder.close() for recorder in self.recorders.values()))

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        self.handlers.add(task)
        if len(self.sessions) >= self.max_clients:
            writer.close()
            await writer.wait_closed()
            self.handlers.discard(task)
            return
        session = Session(new_id(), writer, frame_bytes=self.limits.max_frame_bytes,
            queue_bytes=self.connection_queue_bytes, subscription_bytes=self.subscription_queue_bytes)
        self.sessions[session.id] = session
        session.task = asyncio.create_task(session.run())
        state, principal, previous = "connected", None, 0
        deadline = asyncio.get_running_loop().time() + self.limits.handshake_timeout_ms / 1000
        cursor: tuple[str, list[str]] | None = None
        try:
            while not session.closed:
                req_id = None
                try:
                    timeout = (deadline - asyncio.get_running_loop().time() if principal is None
                               else self.limits.idle_timeout_ms / 1000)
                    message = await asyncio.wait_for(read_frame(reader, self.limits.max_frame_bytes,
                                                                 self.limits.max_json_depth), timeout=max(0, timeout))
                    req = request(message, previous)
                    req_id, previous = req.id, int(req.id)
                    if req.method in ("hello", "auth.authenticate"):
                        if (req.method == "hello" and state != "connected") or (req.method == "auth.authenticate" and state != "negotiated"):
                            raise DomainError("INVALID_STATE", "握手順序不合法。")
                    elif principal is None:
                        raise DomainError("AUTH_REQUIRED", "請先完成握手與認證。")
                    validate_params(req.method, req.params)
                    after: list[tuple[str, Event]] = []
                    if req.method == "hello":
                        if {"major": 1, "minor": 0} not in req.params["versions"]:
                            raise DomainError("UNSUPPORTED_VERSION", "沒有共同協定版本。",
                                              supported_versions=[{"major": 1, "minor": 0}])
                        result = {"version": {"major": 1, "minor": 0}, "gateway_instance_id": self.store.instance,
                            "capabilities": ["rx.replay", "tx.status", "binding.confirm"],
                            "auth_methods": ["token"], "limits": self.limits.wire()}
                        state = "negotiated"
                    elif req.method == "auth.authenticate":
                        principal = authenticate(req.params["token"], self.principals)
                        state = "authenticated"
                        result = {"session_id": session.id, "principal_id": principal.id,
                            "grants": [{"channel_id": cid, "permissions": sorted(perms)}
                                       for cid, perms in sorted(principal.grants.items())]}
                    elif req.method == "channel.list":
                        requested = req.params.get("cursor")
                        if requested is None:
                            remaining = sorted(cid for cid in principal.grants if "read" in principal.grants[cid])
                        elif cursor is not None and requested == cursor[0]:
                            remaining = cursor[1]
                        else:
                            raise DomainError("INVALID_CURSOR", "分頁位置已失效。")
                        count = req.params.get("limit", 16)
                        selected, rest = remaining[:count], remaining[count:]
                        cursor = (new_id(), rest) if rest else None
                        result = {"channels": [self.snapshot(self.channels[cid]) for cid in selected],
                                  "next_cursor": cursor[0] if cursor else None}
                    else:
                        result, after = await self._dispatch(session, principal, req.method, req.params)
                    session.send({"type": "response", "id": req_id, "ok": True, "result": result})
                    for owner, event in after:
                        self.notify(owner, event)
                except (DomainError, ProtocolError) as exc:
                    code = exc.code
                    session.send({"type": "response", "id": req_id, "ok": False,
                        "error": {"code": code, "message": str(exc), "details": getattr(exc, "details", {})}})
                    fatal = code in {"INVALID_FRAME", "FRAME_TOO_LARGE", "INVALID_REQUEST_ID",
                                     "AUTH_REQUIRED", "AUTH_FAILED", "UNSUPPORTED_VERSION"}
                    if fatal or (code == "INVALID_STATE" and (principal is None or req.method in {"hello", "auth.authenticate"})):
                        # 給單一 Writer 有限機會送出最後錯誤；不在 Reader 直接寫入。
                        await asyncio.sleep(0)
                        break
        except (EOFError, TimeoutError, ConnectionError, OSError):
            pass
        except Exception:
            LOG.exception("Gateway 連線處理失敗")
        finally:
            for channel in self.channels.values():
                channel.disconnect(session.id)
            await session.close()
            self.sessions.pop(session.id, None)
            self.handlers.discard(task)

    async def _dispatch(self, session: Session, principal: Principal, method: str,
                        params: dict) -> tuple[dict, list[tuple[str, Event]]]:
        after = []
        if method == "ping":
            return {"server_time_utc": utc_now()}, after
        if method == "rx.unsubscribe":
            sid = params["subscription_id"]
            for channel in self.channels.values():
                if channel.subscriptions.get(sid) == session.id:
                    del channel.subscriptions[sid]
                    session.drop_subscription(sid)
                    return {"unsubscribed": True}, after
            return {"unsubscribed": False}, after
        cid = params["channel_id"]
        permission = "configure" if method.startswith("binding.") else "tx" if method.startswith(("lease.", "tx.")) else "read"
        principal.require(cid, permission)
        channel = self.channels[cid]
        if method == "channel.get":
            result = {"channel": self.snapshot(channel)}
        elif method == "binding.rebind":
            recipients = await channel.rebind(context(params))
            after = [(owner, channel.event("channel_state", {})) for owner in recipients]
            result = {"channel": self.snapshot(channel)}
        elif method == "binding.confirm":
            if channel.state in ("opening", "draining"):
                raise DomainError("CHANNEL_BUSY", "通道的生命週期操作尚未結束。")
            label = params["label"]
            if (any(other.config.id != cid and other.config.label == label for other in self.channels.values())
                    or self.pending_labels.get(label, cid) != cid):
                raise DomainError("INVALID_PARAMS", "顯示名稱與其他通道重複。")
            expected = context(params)
            channel.check_context(expected)
            # 只保留名稱，不持有跨通道 I/O 鎖；避免兩個開啟流程確認同名目標。
            self.pending_labels[label] = cid
            try:
                if channel.state == "closed":
                    await channel.start()
                    expected = channel.context
                recipients = channel.confirm(expected, label, params.get("target_note"))
            finally:
                self.pending_labels.pop(label, None)
            after = [(owner, channel.event("channel_state", {})) for owner in recipients]
            result = {"channel": self.snapshot(channel)}
        elif method == "rx.subscribe":
            count = sum(owner == session.id for c in self.channels.values() for owner in c.subscriptions.values())
            if count >= self.limits.max_subscriptions:
                raise DomainError("QUEUE_FULL", "訂閱數量已達上限。")
            result, events = channel.subscribe(session.id,
                epoch=params.get("stream_epoch"),
                offset=int(params["from_offset"]) if params["mode"] == "resume" else None,
                skip=params.get("gap_policy", "error") == "skip")
            after = [(session.id, event) for event in events]
        elif method == "lease.acquire":
            result = channel.acquire(session.id, context(params))
        elif method == "lease.renew":
            result = channel.renew(session.id, context(params), uint(params["fencing_token"]))
        elif method == "lease.release":
            result = channel.release(session.id, context(params), uint(params["fencing_token"]))
        elif method == "tx.write":
            payload = binary_decode(params["data_b64"], self.limits.max_tx_bytes)
            record = channel.submit(principal.id, session.id, context(params), uint(params["fencing_token"]), params["tx_id"], payload)
            result = {"tx": tx_result(record, self.store)}
        elif method == "tx.status":
            record = (self.store.lookup(principal.id, params["tx_id"])
                      if params["gateway_instance_id"] == self.store.instance else None)
            if record is not None and record.channel_id != cid:
                record = None
            result = {"tx": tx_result(record, self.store) if record else
                      unavailable(cid, params["gateway_instance_id"], params["tx_id"], self.store.instance)}
        else:
            raise DomainError("UNKNOWN_METHOD", "沒有此方法。")
        return result, after
