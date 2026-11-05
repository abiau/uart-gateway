"""領域資料轉成 V1 線上格式；Core 不知道 Base64 或 TCP。"""

from dataclasses import asdict

from uart_gateway.core.channel import Channel, TxStore
from uart_gateway.core.models import Event, TxRecord
from uart_gateway.protocol.codec import binary_encode


def tx_result(record: TxRecord, store: TxStore) -> dict:
    return {"tx_id": record.id, "channel_id": record.channel_id,
            "gateway_instance_id": record.context.instance, "state": record.state,
            "submitted_bytes": record.size, "accepted_bytes": record.accepted,
            "io_started": record.started, "reason_code": record.reason,
            "completed_at": record.completed,
            "retained_for_ms": None if record.expires is None else max(0, int((record.expires - store.clock()) * 1000)),
            "status_origin": "record"}


def unavailable(channel_id: str, instance: str, tx_id: str, current: str) -> dict:
    return {"tx_id": tx_id, "channel_id": channel_id, "gateway_instance_id": instance,
            "state": "unknown", "submitted_bytes": None, "accepted_bytes": None,
            "io_started": None, "reason_code": "RESULT_UNAVAILABLE" if instance == current else "SERVER_RESTARTED",
            "completed_at": None, "retained_for_ms": 0, "status_origin": "unavailable"}


def snapshot(channel: Channel, *, recording: dict | None = None) -> dict:
    channel.expire()
    return {"channel_id": channel.config.id, "label": channel.config.label,
        "target_note": channel.config.target_note, "state": channel.state,
        "binding": {"revision": str(channel.revision), "status": "confirmed" if channel.confirmed else "unconfirmed"},
        "stream_epoch": channel.epoch,
        "adapter": asdict(channel.port.describe()),
        "serial": asdict(channel.config.serial),
        "serial_source": "applied" if channel.state == "ready" else "configured",
        "rx": {"earliest_offset": str(channel.earliest) if channel.epoch else None,
               "next_offset": str(channel.offset) if channel.epoch else None},
        "tx": {"state": "blocked" if channel.blocked else
               "leased" if channel.lease.owner else "quiescing" if channel.inflight else "idle",
               "owner_session_id": channel.lease.owner},
        "recording": recording if recording is not None else {"state": "disabled", "integrity": "not_recording"},
        "last_error": None if channel.last_error is None else
            {"code": channel.last_error[0], "message": channel.last_error[1]}}


def event_message(event: Event, channel: Channel, *, recording: dict | None = None) -> dict:
    data = dict(event.data)
    if event.kind == "rx":
        data["data_b64"] = binary_encode(data.pop("payload"))
    elif event.kind == "tx_result":
        data = {"tx": tx_result(data["record"], channel.store)}
    elif event.kind == "channel_state":
        data = {"channel": snapshot(channel, recording=recording)}
    return {"type": "event", "event": event.kind, "channel_id": event.channel_id,
            "binding_revision": str(event.revision), "stream_epoch": event.epoch,
            "timestamp_utc": event.timestamp, "data": data}
