"""供 CLI 與未來 TUI 共用的非同步操作介面。"""

import asyncio
from uuid import uuid4

from uart_gateway import __version__
from uart_gateway.protocol.codec import binary_encode
from uart_gateway.protocol.errors import ProtocolError
from .transport import Transport


class TxUncertain(ConnectionError):
    """傳送回覆不明，攜帶可供人工查詢的操作識別。"""

    def __init__(self, tx_id: str, channel_id: str, instance: str):
        super().__init__(f"傳送結果尚未確定，tx_id={tx_id}；請查詢狀態，不得自動重送。")
        self.tx_id, self.channel_id, self.instance = tx_id, channel_id, instance


class Client:
    def __init__(self, transport: Transport, hello: dict, auth: dict):
        self.transport, self.hello, self.auth = transport, hello, auth
        self.instance = hello["gateway_instance_id"]

    @classmethod
    async def connect(cls, host: str, port: int, token: str, *, timeout: float = 5) -> "Client":
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port, limit=65535), timeout)
        transport = Transport(reader, writer, timeout=timeout)
        try:
            hello = await transport.request("hello", {"versions": [{"major": 1, "minor": 0}],
                "client_name": "uart-python", "client_version": __version__})
            if hello.get("version") != {"major": 1, "minor": 0}:
                raise ProtocolError("UNSUPPORTED_VERSION", "Server 選擇了未支援的版本。")
            limits = hello["limits"]
            if not 16384 <= limits["max_frame_bytes"] <= 65536:
                raise ProtocolError("INVALID_FRAME", "Server 訊框限制不合法。")
            transport.maximum = limits["max_frame_bytes"]
            transport.depth = limits["max_json_depth"]
            transport.pending_limit = limits["max_pending_requests"]
            auth = await transport.request("auth.authenticate", {"scheme": "token", "token": token})
            client = cls(transport, hello, auth)
            transport.start_heartbeat(limits["heartbeat_interval_ms"] / 1000)
            return client
        except BaseException:
            await transport.close()
            raise

    async def __aenter__(self) -> "Client":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self.transport.close()

    def expected(self, channel: dict) -> dict:
        return {"gateway_instance_id": self.instance,
                "binding_revision": channel["binding"]["revision"], "stream_epoch": channel["stream_epoch"]}

    async def list_channels(self) -> list[dict]:
        channels, cursor = [], None
        while True:
            page = await self.transport.request("channel.list", {"cursor": cursor})
            channels.extend(page["channels"])
            cursor = page["next_cursor"]
            if cursor is None:
                return channels

    async def get_channel(self, channel_id: str) -> dict:
        return (await self.transport.request("channel.get", {"channel_id": channel_id}))["channel"]

    async def confirm(self, channel: dict, label: str, target_note: str | None = None) -> dict:
        return (await self.transport.request("binding.confirm", {"channel_id": channel["channel_id"],
            "expected": self.expected(channel), "label": label, "target_note": target_note}))["channel"]

    async def rebind(self, channel: dict) -> dict:
        return (await self.transport.request("binding.rebind", {"channel_id": channel["channel_id"],
            "expected": self.expected(channel)}))["channel"]

    async def reopen_rx(self, channel: dict) -> dict:
        if "channel.reopen_rx" not in self.hello.get("capabilities", []):
            raise ProtocolError("UNKNOWN_METHOD", "此 Gateway 未提供單通道 RX 重開，請先更新 Gateway。")
        try:
            return (await self.transport.request("channel.reopen_rx", {"channel_id": channel["channel_id"],
                "expected": self.expected(channel)}))["channel"]
        except (OSError, EOFError, TimeoutError, ProtocolError) as exc:
            raise ConnectionError("RX 重開結果未確認；請重新查詢通道狀態，不要直接重送。") from exc

    async def subscribe(self, channel_id: str, *, epoch: str | None = None,
                        offset: str | None = None, gap_policy: str = "error") -> dict:
        params = {"channel_id": channel_id, "mode": "live" if offset is None else "resume", "gap_policy": gap_policy}
        if offset is not None:
            params.update(stream_epoch=epoch, from_offset=offset)
        return await self.transport.request("rx.subscribe", params)

    async def unsubscribe(self, subscription_id: str) -> bool:
        return (await self.transport.request("rx.unsubscribe", {"subscription_id": subscription_id}))["unsubscribed"]

    async def next_event(self) -> dict:
        return await self.transport.next_event()

    async def acquire(self, channel: dict) -> "LeaseHandle":
        expected = self.expected(channel)
        cid = channel["channel_id"]
        result = await self.transport.request("lease.acquire", {"channel_id": cid, "expected": expected})
        return LeaseHandle(self, cid, expected, result["fencing_token"])

    async def tx_status(self, channel_id: str, tx_id: str, *, instance: str | None = None) -> dict:
        return (await self.transport.request("tx.status", {"channel_id": channel_id,
            "gateway_instance_id": instance or self.instance, "tx_id": tx_id}))["tx"]

    async def wait_tx(self, channel_id: str, tx_id: str, *, timeout: float = 5) -> dict:
        async with asyncio.timeout(timeout):
            while True:
                result = await self.tx_status(channel_id, tx_id)
                if result["state"] != "pending":
                    return result
                await asyncio.sleep(0.02)


class LeaseHandle:
    def __init__(self, client: Client, channel_id: str, expected: dict, token: str):
        self.client, self.channel_id, self.expected, self.token = client, channel_id, expected, token
        self.failure: Exception | None = None
        self.active = True
        self.renew_task: asyncio.Task | None = None

    def _params(self) -> dict:
        return {"channel_id": self.channel_id, "expected": self.expected, "fencing_token": self.token}

    async def __aenter__(self) -> "LeaseHandle":
        self.renew_task = asyncio.create_task(self._renew())
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def _renew(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.client.hello["limits"]["lease_renew_interval_ms"] / 1000)
                await self.client.transport.request("lease.renew", self._params())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.failure, self.active = exc, False

    async def write_bytes(self, payload: bytes, *, tx_id: str | None = None) -> dict:
        if not self.active:
            raise self.failure or RuntimeError("租約已關閉")
        operation_id = tx_id or uuid4().hex
        params = self._params() | {"tx_id": operation_id, "data_b64": binary_encode(payload)}
        try:
            return (await self.client.transport.request("tx.write", params))["tx"]
        except (TimeoutError, OSError, EOFError, ProtocolError) as exc:
            raise TxUncertain(operation_id, self.channel_id, self.client.instance) from exc

    async def close(self) -> None:
        if self.renew_task:
            self.renew_task.cancel()
            await asyncio.gather(self.renew_task, return_exceptions=True)
        if self.active:
            self.active = False
            try:
                await self.client.transport.request("lease.release", self._params())
            except Exception as exc:
                self.failure = exc
