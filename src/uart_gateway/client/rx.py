"""共用訂閱快照核對與接收 Cursor；不渲染終端或操作 Serial。"""

from uart_gateway.protocol.codec import binary_decode
from uart_gateway.protocol.errors import ProtocolError
from uart_gateway.protocol.messages import uint
from .transport import LocalDataLoss


class StreamContextChanged(ValueError):
    """快照或訂閱脈絡不符，不能將不同來源顯示在同一資料流。"""


async def subscribe_checked(client, channel_id: str, *, replay: bool = False, expected_context=None) -> dict:
    if expected_context is not None and client.instance != expected_context[0]:
        raise StreamContextChanged("Gateway 已重新啟動，請按 R 更新清單後重新選擇。")
    if replay or expected_context is not None:
        channel = await client.get_channel(channel_id)
        if expected_context is not None and (channel["state"] != "ready" or
                (client.instance, channel["binding"]["revision"], channel["stream_epoch"]) != expected_context):
            raise StreamContextChanged("所選通道狀態或資料流已改變，請按 R 更新清單後重新選擇。")
    if replay:
        if channel["stream_epoch"] is None:
            raise ValueError("通道沒有可讀資料流，請先查看 channels 的 last_error")
        sub = await client.subscribe(channel_id, epoch=channel["stream_epoch"], offset=channel["rx"]["earliest_offset"])
    else:
        sub = await client.subscribe(channel_id)
    if expected_context is not None and (client.instance, sub["binding_revision"], sub["stream_epoch"]) != expected_context:
        error = StreamContextChanged("訂閱時資料流已改變，未顯示 RX；請按 R 更新清單後重新選擇。")
        try:
            await client.unsubscribe(sub["subscription_id"])
        except Exception as exc:
            error.add_note(f"取消失效訂閱另有錯誤：{type(exc).__name__}")
        raise error
    return sub


class RXCursor:
    def __init__(self, channel_id: str, subscription: dict):
        self.channel_id, self.subscription = channel_id, subscription
        self.next_offset = uint(subscription["start_offset"])

    def receive(self, event: dict) -> bytes | None:
        try:
            if event["channel_id"] != self.channel_id:
                raise StreamContextChanged("收到非選定通道的事件，監看已停止。")
            kind, data = event["event"], event["data"]
            if kind not in ("rx", "gap") or data["subscription_id"] != self.subscription["subscription_id"]:
                return None
            if kind == "gap":
                raise LocalDataLoss("收到資料缺口，該窗格已停止接收；請明確重新監看。")
            if (event["binding_revision"], event["stream_epoch"]) != (
                    self.subscription["binding_revision"], self.subscription["stream_epoch"]):
                raise StreamContextChanged("事件資料流已變更，未將新舊世代混入窗格。")
            payload = binary_decode(data["data_b64"])
            start, end = uint(data["offset"]), uint(data["next_offset"])
            if not payload or start != self.next_offset or end != start + len(payload):
                raise LocalDataLoss("接收位移或長度不連續，該窗格已停止接收。")
            if data["delivery"] not in ("replay", "live"):
                raise ProtocolError("INVALID_FRAME", "接收模式不合法。")
            self.next_offset = end
            return payload
        except (KeyError, TypeError):
            raise ProtocolError("INVALID_FRAME", "接收事件格式不合法。") from None
