"""遠端雙通道工作區；只經 SDK，單路失敗不取消另一路。"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from .client import Client
from .console_input import ConsoleInput
from .protocol.errors import ProtocolError, RemoteError
from .remote_workbench import label, query_channels, reopen_channel
from .text_renderer import RESET

if TYPE_CHECKING:
    from .cli import _WatchOutput

ERRORS = (OSError, ValueError, ProtocolError, RemoteError, TimeoutError)


class RemoteGroup:
    def __init__(self, connect: Callable[[], Awaitable[Client]], channels: list[dict], instance: str, *,
                 watch: Callable[..., Awaitable[int]], sink: "_WatchOutput", owned: bool = False):
        if not 1 <= len(channels) <= 2 or len({c["channel_id"] for c in channels}) != len(channels):
            raise ValueError("工作區需要一至兩個不同的 Channel。")
        self.connect, self.instance, self.watch, self.sink = connect, instance, watch, sink
        self.channels = list(channels)
        self.tasks: dict[str, asyncio.Task] = {}
        self.states: dict[str, str] = {}
        self.valid = True
        self.owned = owned

    def report(self, message: str) -> None:
        self.sink.put(RESET + "\n[工作台] " + message + "\n", diagnostic=True)

    def active(self, cid: str) -> bool:
        return cid in self.tasks and not self.tasks[cid].done()

    def start(self, number: int, *, replay: bool) -> None:
        if not 1 <= number <= len(self.channels):
            raise ValueError("通道編號超出目前工作區範圍。")
        channel = self.channels[number - 1]
        cid = channel["channel_id"]
        if not self.valid:
            raise ValueError("Gateway 已改變，請 Q 返回清單重新選擇。")
        if self.active(cid):
            raise ValueError("此通道已在監看，未建立重複訂閱。")
        if channel["state"] != "ready":
            raise ValueError("此通道尚未就緒；R 更新，或使用有權限的 O 手動重開。")
        self.states[cid] = "連線／訂閱中"
        self.tasks[cid] = asyncio.create_task(self._observe(channel, replay))

    async def _observe(self, channel: dict, replay: bool) -> None:
        cid = channel["channel_id"]
        expected = (self.instance, channel["binding"]["revision"], channel["stream_epoch"])
        try:
            async with await self.connect() as client:
                self.states[cid] = "監看中（訂閱仍須通過快照核對）"
                result = await self.watch(client, cid, duration=None, text_mode=True, replay=replay,
                    terminal_mode=True, shared_sink=self.sink,
                    label=f"{label(cid)}／{label(channel['label'])}" if len(self.channels) > 1 else None,
                    expected_context=expected)
            self.states[cid] = "訂閱已停止" if result else "監看已結束"
            other = "另一路繼續，" if len(self.channels) > 1 else ""
            self.report(f"{label(cid)}：{self.states[cid]}；{other}R 查詢後可 O 重開或 S 重新監看。")
        except ERRORS as exc:
            self.states[cid] = "監看失敗"
            code = getattr(exc, "code", type(exc).__name__)
            detail = "請核對 Token 來源。" if code == "AUTH_FAILED" else label(exc)
            other = "，另一路繼續" if len(self.channels) > 1 else ""
            self.report(f"{label(cid)}：監看失敗（{label(code)}）：{detail}；未自動重連{other}。")

    async def refresh(self) -> None:
        instance, channels = await query_channels(self.connect)
        if instance != self.instance:
            self.valid = False
            raise ValueError("Gateway 已改變，請 Q 返回清單重新選擇；未重新訂閱或開啟裝置。")
        by_id = {c["channel_id"]: c for c in channels}
        for number, old in enumerate(self.channels):
            self.channels[number] = by_id.get(old["channel_id"],
                dict(old, state="unavailable", reopen_rx_available=False))
        self.show()

    def show(self) -> None:
        self.report(f"{'本機 RX' if self.owned else '雙路'}工作區；Gateway Instance：{label(self.instance)}；狀態為最近查詢快照。")
        for number, channel in enumerate(self.channels, 1):
            cid = channel["channel_id"]
            error = channel.get("last_error") or {}
            states = {"ready": "就緒", "lost": "裝置遺失", "faulted": "故障", "closed": "已關閉",
                      "opening": "開啟中", "draining": "關閉中", "unavailable": "無讀取權限或通道不存在"}
            self.report(f"{number}. {label(cid)}／{label(channel['label'])} | "
                f"通道：{states.get(channel['state'], '未知')} | 接收：{self.states.get(cid, '未監看')} | "
                f"修訂：{label(channel['binding']['revision'])} | Epoch：{label(channel['stream_epoch'] or '無')}" +
                (f" | 錯誤：{label(error.get('code'))}：{label(error.get('message'))}" if error else ""))
        numbers = "／".join(str(n) for n in range(1, len(self.channels) + 1))
        end = "Q 或 Ctrl+C 結束並清理本次 Gateway。" if self.owned else "Q 返回清單；Ctrl+C 停止雙路。"
        self.report(f"輸入指令後按 Enter：R 查詢；O {numbers} 手動重開；S {numbers} 重新監看；{end}")

    async def reopen(self, number: int, selected: dict) -> None:
        if not self.valid:
            raise ValueError("Gateway 已改變，請返回清單重新選擇。")
        try:
            result = await reopen_channel(self.connect, selected, self.instance)
        except asyncio.CancelledError:
            self.report("RX 重開等待已中止，Gateway 可能已接受請求；返回清單後先 R 查詢，不直接重送。")
            raise
        # 舊訂閱的結束事件可能仍在排隊；只收斂被重開的通道。
        cid = selected["channel_id"]
        task = self.tasks.get(cid)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.channels[number - 1] = dict(result, reopen_rx_available=selected.get("reopen_rx_available", False))
        self.report(f"{label(cid)} RX 重開成功；新 Epoch：{label(result['stream_epoch'])}；仍未 Confirm。重新接收新世代歷史＋即時資料。")
        self.start(number, replay=True)

    async def close(self) -> None:
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        await self.sink.close()


async def run_remote_group(connect: Callable[[], Awaitable[Client]], channels: list[dict], instance: str,
                           replay: bool, *, watch: Callable[..., Awaitable[int]],
                           sink_factory: Callable[[], "_WatchOutput"],
                           poll: Callable[[], str | None] | None = None, owned: bool = False) -> int:
    if ((len(channels) != 2 and not (owned and len(channels) == 1))
            or len({c["channel_id"] for c in channels}) != len(channels)):
        raise ValueError("雙路畫面需要兩個不同的 Channel。")
    sink = sink_factory()
    group = RemoteGroup(connect, channels, instance, watch=watch, sink=sink, owned=owned)
    pending = None
    try:
        poll = poll or ConsoleInput(echo=group.report).poll
        group.show()
        for number, channel in enumerate(channels, 1):
            if channel["state"] == "ready":
                group.start(number, replay=replay)
        while True:
            if sink.task.done():
                await sink.task
                raise OSError("雙路畫面輸出已停止。")
            for task in group.tasks.values():
                if task.done() and not task.cancelled():
                    task.result()
            try:
                command = poll()
            except EOFError:
                group.report("輸入來源已結束（EOF），結束並清理本次 Gateway。" if owned else "輸入來源已結束（EOF），停止雙路監看並返回清單。")
                return 0
            if command is None:
                await asyncio.sleep(0.05)
                continue
            command = command.strip().casefold()
            if command == "q":
                group.report("停止監看，正在清理本次 Gateway。" if owned else "停止雙路監看並返回清單；Gateway 繼續執行。")
                return 0
            if pending is not None:
                number, selected = pending
                pending = None
                if command != "y":
                    group.report("已取消重開；未操作 Serial，其他指令請重新輸入。")
                    continue
                try:
                    await group.reopen(number, selected)
                except ERRORS as exc:
                    detail = "認證失敗（AUTH_FAILED），請核對 Token 來源。" if getattr(exc, "code", None) == "AUTH_FAILED" else label(exc)
                    group.report(f"重開未完成或結果未確認：{detail}；未自動重試，先 R 查詢。占用時請關閉 MobaXterm 的 COM 連線。")
                continue
            try:
                if command == "r":
                    await group.refresh()
                elif command in ("s 1", "s 2"):
                    group.start(int(command[-1]), replay=False)
                elif command in ("o 1", "o 2"):
                    number = int(command[-1])
                    if number > len(group.channels):
                        raise ValueError("通道編號超出目前工作區範圍。")
                    selected = group.channels[number - 1]
                    if not group.valid or not selected.get("reopen_rx_available"):
                        raise ValueError("缺少有效快照、重開能力或 configure 權限；請 R 查詢或返回清單。")
                    if selected["state"] not in ("lost", "faulted", "closed"):
                        raise ValueError("只能重開遺失、故障或已關閉的通道；請先 R 更新狀態。")
                    pending = (number, selected)
                    group.report(f"即將重開 {label(selected['channel_id'])}／{label(selected.get('adapter', {}).get('port') or '原設定端點')}；"
                        "請核對接線。新世代會清除該路記憶體歷史，不 Confirm／Lease／TX。輸入 Y 確認，其他輸入取消：")
                elif not command:
                    group.show()
                else:
                    group.report("指令無效；請輸入 R、O 1、O 2、S 1、S 2 或 Q，再按 Enter。")
            except ERRORS as exc:
                detail = "認證失敗（AUTH_FAILED），請核對 Token 來源。" if getattr(exc, "code", None) == "AUTH_FAILED" else label(exc)
                group.report(f"操作失敗：{detail}；未自動重試，既有監看繼續。")
    finally:
        await group.close()
