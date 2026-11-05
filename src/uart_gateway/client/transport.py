"""請求配對、有界事件接收與心跳；不重送副作用請求。"""

import asyncio

from uart_gateway.protocol.codec import encode_frame, read_frame
from uart_gateway.protocol.errors import ProtocolError, RemoteError
from uart_gateway.protocol.messages import EVENTS, uint


class LocalDataLoss(ConnectionError):
    """SDK 事件接收超限；連線已終止，必須明確恢復 Cursor。"""


class Transport:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 *, timeout: float = 5, event_bytes: int = 262144):
        self.reader, self.writer, self.timeout = reader, writer, timeout
        self.maximum = 65536
        self.depth = 16
        self.pending_limit = 32
        self.counter = 0
        self.pending: dict[str, asyncio.Future] = {}
        self.send_lock = asyncio.Lock()
        self.events: asyncio.Queue[tuple[dict, int] | None] = asyncio.Queue(maxsize=256)
        self.event_bytes, self.event_used = event_bytes, 0
        self.failure: Exception | None = None
        self.reader_task = asyncio.create_task(self._receive())
        self.heartbeat_task: asyncio.Task | None = None

    async def request(self, method: str, params: dict) -> dict:
        if self.failure:
            raise self.failure
        future = asyncio.get_running_loop().create_future()
        request_id = None
        try:
            async with self.send_lock:
                if self.failure:
                    raise self.failure
                if len(self.pending) >= self.pending_limit:
                    raise RuntimeError("SDK 並行請求已達上限")
                if self.counter == 2**64 - 1:
                    raise RuntimeError("請求序號已耗盡，請重新建立連線")
                self.counter += 1
                request_id = str(self.counter)
                raw = encode_frame({"type": "request", "id": request_id, "method": method, "params": params}, self.maximum)
                self.pending[request_id] = future
                self.writer.write(raw)
                await asyncio.wait_for(self.writer.drain(), self.timeout)
            return await asyncio.wait_for(future, self.timeout)
        finally:
            if request_id is not None:
                self.pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                # drain 失敗時也取走已完成 Future 的例外，避免漏報背景警告。
                future.exception()

    def _fail(self, error: Exception) -> None:
        if self.failure is not None:
            return
        self.failure = error
        self.writer.close()
        for future in self.pending.values():
            if not future.done():
                future.set_exception(error)
        while not self.events.empty():
            self.events.get_nowait()
        self.event_used = 0
        self.events.put_nowait(None)

    async def _receive(self) -> None:
        try:
            while True:
                message = await read_frame(self.reader, self.maximum, self.depth)
                if message.get("type") == "response":
                    request_id = message.get("id")
                    if request_id is None:
                        raise ProtocolError("INVALID_FRAME", "Server 無法解析請求。")
                    number = uint(request_id)
                    if number > self.counter or type(message.get("ok")) is not bool:
                        raise ProtocolError("INVALID_FRAME", "回覆外層不合法。")
                    if message["ok"]:
                        if not isinstance(message.get("result"), dict) or "error" in message:
                            raise ProtocolError("INVALID_FRAME", "成功回覆不合法。")
                    else:
                        error = message.get("error")
                        if (not isinstance(error, dict) or not {"code", "message", "details"} <= error.keys()
                                or not isinstance(error["code"], str) or not isinstance(error["message"], str)
                                or not isinstance(error["details"], dict) or "result" in message):
                            raise ProtocolError("INVALID_FRAME", "錯誤回覆不合法。")
                    future = self.pending.get(request_id)
                    if future is not None and not future.done():
                        if message["ok"]:
                            future.set_result(message["result"])
                        else:
                            future.set_exception(RemoteError(message["error"]))
                elif message.get("type") == "event":
                    if (message.get("event") not in EVENTS or not
                            {"channel_id", "binding_revision", "stream_epoch", "timestamp_utc", "data"} <= message.keys()
                            or not isinstance(message["data"], dict)):
                        raise ProtocolError("INVALID_FRAME", "事件外層不合法。")
                    size = len(encode_frame(message))
                    if self.events.full() or self.event_used + size > self.event_bytes:
                        raise LocalDataLoss("SDK 事件佇列已滿，資料連續性中斷。")
                    self.event_used += size
                    self.events.put_nowait((message, size))
                    # 已緩衝的重播也讓消費者取得執行機會，不放大有界佇列。
                    await asyncio.sleep(0)
                else:
                    raise ProtocolError("INVALID_FRAME", "Server 傳送了不合法訊息。")
        except asyncio.CancelledError:
            raise
        except (EOFError, OSError, ProtocolError, ValueError) as exc:
            self._fail(exc if isinstance(exc, Exception) and not isinstance(exc, EOFError)
                       else ConnectionError("Gateway 已關閉連線。"))

    async def next_event(self) -> dict:
        item = await self.events.get()
        if item is None:
            self.events.put_nowait(None)
            raise self.failure or ConnectionError("連線已關閉。")
        message, size = item
        self.event_used -= size
        return message

    def start_heartbeat(self, interval: float) -> None:
        self.heartbeat_task = asyncio.create_task(self._heartbeat(interval))

    async def _heartbeat(self, interval: float) -> None:
        try:
            while True:
                await asyncio.sleep(interval)
                await self.request("ping", {})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._fail(exc)

    async def close(self) -> None:
        self._fail(ConnectionError("Client 已關閉連線。"))
        tasks = [task for task in (self.reader_task, self.heartbeat_task) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await self.writer.wait_closed()
        except (OSError, ConnectionError):
            pass
