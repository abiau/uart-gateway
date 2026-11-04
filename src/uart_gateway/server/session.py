"""有界輸出；控制訊息優先，訂閱資料輪流送出。"""

import asyncio
from collections import deque

from uart_gateway.protocol.codec import encode_frame
from uart_gateway.protocol.errors import ProtocolError


class Session:
    def __init__(self, session_id: str, writer: asyncio.StreamWriter, *, frame_bytes: int = 65536,
                 queue_bytes: int = 1048576, subscription_bytes: int = 262144):
        self.id, self.writer = session_id, writer
        self.frame_bytes, self.maximum, self.sub_max = frame_bytes, queue_bytes, subscription_bytes
        self.control: deque[bytes] = deque()
        self.data: dict[str, deque[bytes]] = {}
        self.order: deque[str] = deque()
        self.sub_used: dict[str, int] = {}
        self.used = 0
        self.wakeup = asyncio.Event()
        self.closed = False
        self.task: asyncio.Task | None = None

    def send(self, message: dict) -> None:
        if self.closed:
            return
        raw = encode_frame(message, self.frame_bytes)
        sub_id = message.get("data", {}).get("subscription_id") if message.get("event") == "rx" else None
        if self.used + len(raw) > self.maximum or (sub_id and self.sub_used.get(sub_id, 0) + len(raw) > self.sub_max):
            self.abort()
            return
        if sub_id:
            if sub_id not in self.data:
                self.data[sub_id] = deque()
                self.order.append(sub_id)
                self.sub_used[sub_id] = 0
            self.data[sub_id].append(raw)
            self.sub_used[sub_id] += len(raw)
        else:
            self.control.append(raw)
        self.used += len(raw)
        self.wakeup.set()

    def drop_subscription(self, sub_id: str) -> None:
        self.used -= self.sub_used.pop(sub_id, 0)
        self.data.pop(sub_id, None)
        self.order = deque(sid for sid in self.order if sid != sub_id)

    def abort(self) -> None:
        self.closed = True
        self.writer.close()
        self.wakeup.set()

    async def run(self) -> None:
        try:
            while not self.closed:
                await self.wakeup.wait()
                self.wakeup.clear()
                while self.used and not self.closed:
                    if self.control:
                        raw = self.control.popleft()
                    else:
                        sid = self.order.popleft()
                        raw = self.data[sid].popleft()
                        self.sub_used[sid] -= len(raw)
                        if self.data[sid]:
                            self.order.append(sid)
                        else:
                            del self.data[sid]
                            del self.sub_used[sid]
                    self.used -= len(raw)
                    self.writer.write(raw)
                    await asyncio.wait_for(self.writer.drain(), timeout=5)
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
