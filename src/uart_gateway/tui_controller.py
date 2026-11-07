"""多窗格的 SDK 工作路徑；各路獨立接收，查詢及人工恢復不阻塞另一路。"""

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field

from .client.rx import RXCursor, subscribe_checked
from .protocol.errors import ProtocolError, RemoteError
from .protocol.messages import uint
from .remote_workbench import label, query_channels, reopen_channel
from .tui_buffer import PaneTerminal

ERRORS = (OSError, ValueError, ProtocolError, RemoteError, TimeoutError)


def explain_error(exc: Exception) -> str:
    if getattr(exc, "code", None) == "AUTH_FAILED":
        return "認證失敗，請確認固定憑證來源。"
    if isinstance(exc, ConnectionRefusedError):
        return "無法連到 Gateway，請確認 Gateway 與 SSH Tunnel 已啟動。"
    if isinstance(exc, TimeoutError):
        return "等待 Gateway 回應逾時，請先更新狀態再決定是否重試。"
    if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
        return "Gateway 連線已中斷，未自動重連。"
    return label(exc)


@dataclass
class PaneState:
    snapshot: dict
    terminal: PaneTerminal = field(default_factory=PaneTerminal)
    receiving: str = "未監看"
    error: str = ""
    rx_bytes: int = 0
    delivery: str = ""
    epoch: str | None = None


class TUIController:
    def __init__(self, connect, report):
        self.connect, self.report = connect, report
        self.instance = None
        self.available_instance = None
        self.available = []
        self.panes: dict[str, PaneState] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.valid = True
        self.uncertain_reopen = False

    async def refresh(self) -> bool:
        instance, channels = await query_channels(self.connect)
        self.uncertain_reopen = False
        self.available_instance, self.available = instance, channels
        changed = self.instance is not None and instance != self.instance
        if changed:
            self.valid = False
            await self.stop()
            for pane in self.panes.values():
                pane.receiving = "舊 Gateway 畫面"
                pane.delivery = ""
                pane.snapshot = dict(pane.snapshot, state="unavailable", reopen_rx_available=False)
            self.report("Gateway 已重新啟動，舊畫面保留；請按 C 重新選擇通道。")
        else:
            by_id = {c["channel_id"]: c for c in channels}
            for cid, pane in self.panes.items():
                current = by_id.get(cid, dict(pane.snapshot, state="unavailable", reopen_rx_available=False))
                if self.active(cid) and (current["state"] != "ready" or
                        (current["binding"]["revision"], current["stream_epoch"]) !=
                        (pane.snapshot["binding"]["revision"], pane.snapshot["stream_epoch"])):
                    await self.stop(cid)
                    pane.receiving = "資料流已停止"
                pane.snapshot = current
        return changed

    async def select(self, channels: list[dict], instance: str, *, replay: bool = True) -> None:
        if not 1 <= len(channels) <= 4 or len({c["channel_id"] for c in channels}) != len(channels):
            raise ValueError("請選擇一至四條不同的 UART。")
        await self.stop()
        self.tasks.clear()
        self.instance, self.valid = instance, True
        self.panes = {c["channel_id"]: PaneState(deepcopy(c)) for c in channels}
        for cid, pane in self.panes.items():
            if pane.snapshot["state"] == "ready":
                self.start(cid, replay=replay)

    def active(self, cid: str) -> bool:
        return cid in self.tasks and not self.tasks[cid].done()

    def start(self, cid: str, *, replay: bool = False) -> None:
        pane = self.panes[cid]
        if not self.valid:
            raise ValueError("Gateway 已改變，請按 C 重新選擇通道。")
        if self.active(cid):
            raise ValueError("此通道仍在監看或清理，未建立重複訂閱。")
        if pane.snapshot["state"] != "ready":
            raise ValueError("通道尚未就緒；可按 O 人工重開，或 R 更新狀態。")
        pane.terminal.clear()
        pane.rx_bytes, pane.error, pane.delivery = 0, "", ""
        pane.epoch = pane.snapshot["stream_epoch"]
        pane.receiving = "訂閱中"
        self.tasks[cid] = asyncio.create_task(self.watch(cid, deepcopy(pane.snapshot), replay))

    async def watch(self, cid: str, snapshot: dict, replay: bool) -> None:
        pane, subscription = self.panes[cid], None
        expected = (self.instance, snapshot["binding"]["revision"], snapshot["stream_epoch"])
        try:
            async with await self.connect() as client:
                try:
                    subscription = await subscribe_checked(client, cid, replay=replay, expected_context=expected)
                    cursor = RXCursor(cid, subscription)
                    pane.receiving = "監看中"
                    pane.delivery = "等待即時" if cursor.next_offset == uint(subscription["replay_until_offset"]) else "歷史重播"
                    while True:
                        event = await client.next_event()
                        payload = cursor.receive(event)
                        if payload is not None:
                            pane.terminal.feed(payload)
                            pane.rx_bytes += len(payload)
                            pane.delivery = "歷史重播" if event["data"]["delivery"] == "replay" else "即時"
                            if event["data"]["delivery"] == "replay" and cursor.next_offset == uint(subscription["replay_until_offset"]):
                                pane.delivery = "等待即時"
                        elif event["event"] == "recording_state":
                            pane.snapshot["recording"] = dict(event["data"])
                        elif event["event"] == "channel_state":
                            channel = event["data"]["channel"]
                            pane.snapshot = dict(channel, reopen_rx_available=pane.snapshot.get("reopen_rx_available", False))
                            if channel["state"] != "ready" or (channel["binding"]["revision"], channel["stream_epoch"]) != expected[1:]:
                                pane.receiving = "資料流已停止"
                                break
                        elif event["event"] == "subscription_state" and event["data"]["subscription_id"] == subscription["subscription_id"]:
                            reason = event["data"].get("reason")
                            if reason == "device_lost":
                                pane.snapshot = dict(pane.snapshot, state="lost")
                            pane.receiving = {"device_lost": "裝置遺失", "epoch_changed": "世代已變更",
                                              "server_shutdown": "Gateway 已關閉"}.get(reason, "訂閱已停止")
                            break
                        await asyncio.sleep(0)
                finally:
                    if subscription is not None:
                        try:
                            await client.unsubscribe(subscription["subscription_id"])
                        except ERRORS:
                            self.report(f"{label(cid)} 取消訂閱未確認；本次 Client 連線已關閉。")
        except ERRORS as exc:
            pane.receiving = "接收已停止"
            pane.error = explain_error(exc)
            self.report(f"{label(cid)}：{pane.error}；未自動重連，其他窗格繼續。")
        except (KeyError, TypeError):
            pane.receiving, pane.error = "接收已停止", "事件格式不合法，未繼續顯示資料。"
            self.report(f"{label(cid)}：{pane.error}")
        finally:
            pane.terminal.finish()
            if pane.receiving in ("監看中", "訂閱中"):
                pane.receiving = "已停止"

    async def prepare_reopen(self, cid: str) -> dict:
        await self.refresh()
        pane = self.panes[cid]
        if not self.valid or not pane.snapshot.get("reopen_rx_available"):
            raise ValueError("此連線沒有有效的管理權限或重開能力。")
        if pane.snapshot["state"] not in ("lost", "faulted", "closed"):
            raise ValueError("只能重開遺失、故障或已關閉的通道。")
        return deepcopy(pane.snapshot)

    async def reopen(self, cid: str, selected: dict) -> None:
        try:
            channel = await reopen_channel(self.connect, selected, self.instance)
        except asyncio.CancelledError:
            self.uncertain_reopen = True
            self.report("RX 重開等待已中止；Gateway 可能已接受，請先 R 查詢，不直接重送。")
            raise
        except ConnectionError:
            self.uncertain_reopen = True
            raise
        await self.stop(cid)
        pane = self.panes[cid]
        pane.snapshot = dict(channel, reopen_rx_available=selected.get("reopen_rx_available", False))
        self.start(cid, replay=True)
        self.report(f"{label(cid)} 已重開，開始接收新資料流；仍未 Confirm。")

    async def stop(self, cid: str | None = None) -> None:
        tasks = [task for key, task in self.tasks.items() if cid is None or key == cid]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
