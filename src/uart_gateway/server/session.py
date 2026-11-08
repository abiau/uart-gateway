"""有界輸出；控制訊息優先，訂閱資料輪流送出。"""

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
import logging
import time

from uart_gateway.core.models import DomainError, Event
from uart_gateway.protocol.codec import encode_frame
from uart_gateway.protocol.errors import ProtocolError

LOG = logging.getLogger(__name__)
WRITER_TURN_FRAMES = 8
WRITER_TURN_BYTES = 16384


@dataclass
class Replay:
    events: deque[Event]
    encode: Callable[[Event], dict]
    deadline: float
    used: int


def replay_cost(event: Event) -> int:
    # 原始 bytes 與事件／容器引用的固定預留；不預先建立 Base64／JSON 副本。
    return len(event.data.get("payload", b"")) + 512


class Session:
    def __init__(self, session_id: str, writer: asyncio.StreamWriter, *, frame_bytes: int = 65536,
                 queue_bytes: int = 1048576, subscription_bytes: int = 262144,
                 replay_bytes: int = 8388608, replay_timeout: float = 15):
        self.id, self.writer = session_id, writer
        self.frame_bytes, self.maximum, self.sub_max = frame_bytes, queue_bytes, subscription_bytes
        self.control: deque[bytes] = deque()
        self.data: dict[str, deque[bytes]] = {}
        self.order: deque[str] = deque()
        self.sub_used: dict[str, int] = {}
        self.used = 0
        self.replays: dict[str, Replay] = {}
        self.replay_used = 0
        self.replay_max, self.replay_timeout = replay_bytes, replay_timeout
        self.wakeup = asyncio.Event()
        self.closed = False
        self.task: asyncio.Task | None = None

    def send(self, message: dict) -> None:
        if self.closed:
            return
        raw = encode_frame(message, self.frame_bytes)
        sub_id = message.get("data", {}).get("subscription_id") if message.get("event") == "rx" else None
        if self.used + len(raw) > self.maximum or (sub_id and self.sub_used.get(sub_id, 0) + len(raw) > self.sub_max):
            LOG.warning("工作階段 %s：即時／控制輸出佇列超限，關閉此連線；其他連線繼續。", self.id)
            self.abort()
            return
        if sub_id:
            if sub_id not in self.data:
                self.data[sub_id] = deque()
                if sub_id not in self.replays:
                    self.order.append(sub_id)
                self.sub_used[sub_id] = 0
            self.data[sub_id].append(raw)
            self.sub_used[sub_id] += len(raw)
        else:
            self.control.append(raw)
        self.used += len(raw)
        self.wakeup.set()

    def replay(self, sub_id: str, events: list[Event], encode: Callable[[Event], dict]) -> None:
        """保留有界歷史快照，由單一 Writer 按網路進度逐筆編碼及傳送。"""
        if not events:
            return
        cost = sum(replay_cost(event) for event in events)
        if self.closed or sub_id in self.replays or self.replay_used + cost > self.replay_max:
            raise DomainError("QUEUE_FULL", "重播快照配額不足，未建立訂閱；請減少同連線的重播訂閱。")
        self.replays[sub_id] = Replay(deque(events), encode, time.monotonic() + self.replay_timeout, cost)
        self.replay_used += cost
        if sub_id not in self.data:
            self.order.append(sub_id)
        self.wakeup.set()

    def drop_subscription(self, sub_id: str) -> None:
        self.used -= self.sub_used.pop(sub_id, 0)
        self.data.pop(sub_id, None)
        replay = self.replays.pop(sub_id, None)
        if replay is not None:
            self.replay_used -= replay.used
            replay.events.clear()
            replay.used = 0
        self.order = deque(sid for sid in self.order if sid != sub_id)

    def abort(self) -> None:
        self.closed = True
        self.control.clear()
        self.data.clear()
        self.sub_used.clear()
        for replay in self.replays.values():
            replay.events.clear()
            replay.used = 0
        self.replays.clear()
        self.order.clear()
        self.used = self.replay_used = 0
        self.writer.close()
        self.wakeup.set()

    async def run(self) -> None:
        frames = sent = 0
        try:
            while not self.closed:
                await self.wakeup.wait()
                self.wakeup.clear()
                while (self.used or self.replays) and not self.closed:
                    if self.control:
                        raw = self.control.popleft()
                        self.used -= len(raw)
                    else:
                        sid = self.order.popleft()
                        replay = self.replays.get(sid)
                        if replay is not None:
                            if time.monotonic() >= replay.deadline:
                                LOG.warning("工作階段 %s：歷史重播等待超時，關閉連線並釋放快照。", self.id)
                                self.abort()
                                break
                            event = replay.events.popleft()
                            cost = replay_cost(event)
                            replay.used -= cost
                            self.replay_used -= cost
                            raw = encode_frame(replay.encode(event), self.frame_bytes)
                            del event
                            if not replay.events:
                                del self.replays[sid]
                        else:
                            raw = self.data[sid].popleft()
                            self.sub_used[sid] -= len(raw)
                            self.used -= len(raw)
                            if not self.data[sid]:
                                del self.data[sid]
                                del self.sub_used[sid]
                        if sid in self.replays or sid in self.data:
                            self.order.append(sid)
                    self.writer.write(raw)
                    async with asyncio.timeout(5):
                        await self.writer.drain()
                    frames += 1
                    sent += len(raw)
                    if frames == WRITER_TURN_FRAMES or sent >= WRITER_TURN_BYTES:
                        frames = sent = 0
                        # drain 在輸出緩衝未滿時不會讓出執行權。
                        await asyncio.sleep(0)
        except (ConnectionError, OSError, TimeoutError, ProtocolError):
            self.abort()

    async def close(self) -> None:
        self.abort()
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        try:
            await self.writer.wait_closed()
        except (ConnectionError, OSError):
            pass
