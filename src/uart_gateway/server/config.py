"""第一批切片的有效限制；未支援的設定明確拒絕。"""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Limits:
    max_frame_bytes: int = 65536
    max_json_depth: int = 16
    max_rx_chunk_bytes: int = 4096
    max_tx_bytes: int = 4096
    max_pending_requests: int = 32
    max_subscriptions: int = 16
    max_channel_list_items: int = 16
    rx_ring_bytes: int = 4194304
    max_tx_queue_items: int = 16
    max_tx_queue_bytes: int = 65536
    handshake_timeout_ms: int = 5000
    heartbeat_interval_ms: int = 5000
    idle_timeout_ms: int = 15000
    lease_ttl_ms: int = 15000
    lease_renew_interval_ms: int = 5000
    tx_result_retention_ms: int = 600000
    tx_result_cache_entries: int = 4096

    def wire(self) -> dict:
        return asdict(self)

    def __post_init__(self) -> None:
        for value in asdict(self).values():
            if type(value) is not int or value <= 0:
                raise ValueError("容量與期限必須是正整數")
        if not 16384 <= self.max_frame_bytes <= 65536 or self.max_json_depth > 32:
            raise ValueError("訊框或深度限制不合法")
        if not 1 <= self.max_tx_bytes <= 4096 or self.max_rx_chunk_bytes != 4096:
            raise ValueError("負載大小不合法；此切片的 RX 區塊固定 4096 bytes")
        if self.lease_renew_interval_ms * 3 > self.lease_ttl_ms:
            raise ValueError("租約更新間隔不得超過有效期間三分之一")
        if self.heartbeat_interval_ms * 3 > self.idle_timeout_ms:
            raise ValueError("閒置期限至少為心跳間隔三倍")
