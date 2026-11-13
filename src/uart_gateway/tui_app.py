"""固定多窗格 TUI；UI 更新與 SDK 接收分離，不使用文字指令輸入執行緒。"""

import asyncio
from queue import Empty, Full, Queue

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical
from textual.widgets import Button, Static

from .local_launcher import LauncherCleanupError
from .remote_workbench import label
from .tui_controller import ERRORS, TUIController, explain_error
from .tui_widgets import ChannelPicker, ConfirmDialog, DevicePicker, SerialView, UARTPane, update_static
from .client.tx_workflow import TxWorkflow, target_key
from .tui_tx import ComposeDialog, PreviewDialog, ResultsDialog, TargetDialog


class UARTApp(App[int]):
    TITLE = "UART 多窗格"
    CSS = """
    Screen { background: #0b1220; color: #e2e8f0; }
    #header { dock: top; height: 2; }
    #footer { dock: bottom; height: 6; }
    #heading { height: 1; background: #17304d; text-style: bold; }
    #hint { height: 1; color: #a5b8cc; }
    #actions { height: 3; background: #17304d; }
    #actions Button { width: 1fr; min-width: 7; height: 3; margin: 0; padding: 0; }
    #notice { height: 2; color: #facc15; background: #111f33; }
    #retention { height: 1; color: #a5b8cc; }
    #panes { grid-size: 2; grid-columns: 1fr 1fr; grid-rows: 1fr; }
    #panes.single { grid-size: 1; grid-columns: 1fr; }
    #panes.four { grid-size: 2 2; grid-rows: 1fr 1fr; }
    #panes.stacked { grid-size: 1; grid-columns: 1fr; grid-rows: 1fr; }
    .uart-pane { border: solid #334155; padding: 0; }
    .uart-pane.selected { border: solid #38bdf8; }
    .pane-title { height: 1; background: #17304d; text-style: bold; }
    .pane-state { height: 2; background: #15243a; color: #a5e8d8; }
    .serial-view { height: 1fr; scrollbar-size-vertical: 1; scrollbar-size-horizontal: 0; }
    ModalScreen { align: center middle; background: #00000088; }
    .dialog { width: 76; max-width: 95%; height: 22; max-height: 95%; border: solid #38bdf8; background: #111f33; padding: 1 2; }
    .dialog.compact { height: auto; max-height: 90%; }
    .dialog Label { width: 1fr; height: auto; margin-bottom: 1; }
    .dialog Horizontal { height: 3; }
    .dialog Button { margin-right: 1; }
    SelectionList { height: 1fr; }
    #selection-error { height: 1; color: #f87171; }
    """
    BINDINGS = [Binding("q", "quit_key", "離開", priority=True), Binding("ctrl+c", "quit", "離開", priority=True),
        ("r", "refresh_channels", "更新"), ("s", "resume", "重新監看"), ("o", "reopen", "重開 RX"),
        ("p", "pause_view", "暫停畫面"), Binding("end", "latest", "追蹤最新", priority=True), ("c", "choose", "選通道"),
        ("1", "pane(0)", "窗格 1"), ("2", "pane(1)", "窗格 2"), ("3", "pane(2)", "窗格 3"), ("4", "pane(3)", "窗格 4"),
        ("f2", "send_tx", "傳送"), ("f3", "tx_results", "TX 結果")]

    def __init__(self, connect=None, *, local_launch=None, local_preview=None, local_devices=None,
                 local_select=None, local_record=None, description="遠端讀取", channels=None, tx_journal=None,
                 preferred_channels=None, selection_saved=None):
        super().__init__()
        self.controller = TUIController(connect, self.note)
        self.tx = TxWorkflow(connect, tx_journal) if tx_journal is not None else None
        self.local_launch, self.local_preview, self.description = local_launch, local_preview, description
        self.local_devices, self.local_select, self.local_record = local_devices, local_select, local_record
        self.requested_channels = channels
        self.preferred_channels, self.selection_saved = preferred_channels, selection_saved
        self.selected = 0
        self.failure = None
        self._uart_control = self._uart_local_task = self._uart_quit_task = None
        self._uart_closing = self._uart_busy = False
        self._uart_rebuilding = False
        self._uart_monitor_active = False
        self._uart_cleanup_lock = asyncio.Lock()
        self._uart_finish = asyncio.Event()
        self._uart_reports = Queue(maxsize=100)
        self._uart_omitted_reports = 0
        self._uart_notice = "正在準備連線……"

    def compose(self) -> ComposeResult:
        with Vertical(id="header"):
            yield Static("UART 多窗格｜" + self.description, id="heading", markup=False)
            yield Static("按 1～4 或點擊選窗格；PageUp／PageDown 回看，End 追蹤最新。", id="hint", markup=False)
        yield Grid(id="panes", classes="single")
        with Vertical(id="footer"):
            yield Static("畫面僅保留最近 300 行歷史；暫停只凍結畫面，仍持續接收。", id="retention", markup=False)
            yield Static("", id="notice", markup=False)
            with Horizontal(id="actions"):
                if self.tx:
                    yield Button("傳送 F2", id="tx-send")
                    yield Button("結果 F3", id="tx-results")
                for name, text in (("refresh", "更新 R"), ("resume", "監看 S"), ("reopen", "重開 O"), ("pause", "暫停 P"),
                                   ("latest", "最新 End"), ("choose", "選通道 C"), ("quit", "離開 Q")):
                    yield Button(text, id=name)

    def note(self, message: str):
        self._uart_notice = label(message)

    def check_action(self, action, parameters):
        # 輸入 q 是命令文字；End 在表單移動游標，不攔截到主畫面。
        if action == "quit_key" and isinstance(self.screen, ComposeDialog):
            return False
        if action == "latest" and len(self.screen_stack) > 1:
            return False
        return True

    def gateway_report(self, message: str):
        try:
            self._uart_reports.put_nowait(label(message))
        except Full:
            self._uart_omitted_reports += 1

    def on_mount(self):
        self.set_interval(0.1, self.sync)
        if self.local_launch:
            self.operation(self.prepare_local)
        else:
            self.operation(self.query_and_choose)

    def operation(self, function):
        if self._uart_closing:
            return
        if self._uart_busy:
            self.note("操作進行中，請稍候；接收持續進行。")
            return
        self._uart_busy = True
        async def work():
            try:
                await function()
            except ERRORS as exc:
                self.note("操作未完成：" + explain_error(exc) + "；未自動重試。")
            finally:
                self._uart_busy = False
        self._uart_control = asyncio.create_task(work())

    def sync(self):
        if self._uart_closing or self._uart_rebuilding:
            return
        for _ in range(100):
            try:
                self.note(self._uart_reports.get_nowait())
            except Empty:
                break
        if self._uart_omitted_reports:
            self.note(f"程序診斷訊息達顯示上限，已省略 {self._uart_omitted_reports} 則；不影響 UART 錄製。")
            self._uart_omitted_reports = 0
        for task in [self._uart_control, self._uart_local_task, *self.controller.tasks.values()]:
            if task is not None and task.done() and not task.cancelled() and task.exception() is not None:
                self.failure = task.exception()
                self.action_quit()
                return
        widgets = list(self.query(UARTPane))
        grid = self.query_one("#panes", Grid)
        grid.set_class(len(widgets) <= 1, "single")
        grid.set_class(len(widgets) > 2, "four")
        grid.set_class(self.size.width < 100 and len(widgets) == 2, "stacked")
        for index, widget in enumerate(widgets):
            widget.sync(index == self.selected)
        update_static(self.query_one("#notice", Static), self._uart_notice, wrap=True)
        pane = self.current()
        if self.tx:
            self.query_one("#tx-send", Button).disabled = (self._uart_busy or pane is None or not self.controller.valid or
                not pane.snapshot.get("tx_available") or pane.snapshot["state"] != "ready" or pane.snapshot["tx"]["state"] in ("blocked", "quiescing"))
            self.query_one("#tx-results", Button).disabled = self._uart_busy
        self.query_one("#reopen", Button).disabled = (self._uart_busy or pane is None or not self.controller.valid or
            not pane.snapshot.get("reopen_rx_available") or pane.snapshot["state"] not in ("lost", "faulted", "closed"))
        self.query_one("#resume", Button).disabled = (self._uart_busy or pane is None or not self.controller.valid or
            pane.snapshot["state"] != "ready" or self.controller.active(pane.snapshot["channel_id"]))
        if self.size.width < 80 or self.size.height < 24:
            update_static(self.query_one("#hint", Static), "建議至少 80×24；放大視窗可看到更多資料。1～4 選窗格，Q 離開。")
        else:
            update_static(self.query_one("#hint", Static), "按 1～4 或點擊選窗格；PageUp／PageDown 回看，End 追蹤最新。")

    def current(self):
        values = list(self.controller.panes.values())
        return values[self.selected] if self.selected < len(values) else None

    def current_view(self):
        if self._uart_rebuilding:
            return None
        widgets = list(self.query(UARTPane))
        return widgets[self.selected].query_one(SerialView) if self.selected < len(widgets) else None

    def select_pane(self, number):
        if not self._uart_rebuilding and 0 <= number < len(self.controller.panes):
            self.selected = number
            if (view := self.current_view()) is not None:
                view.focus()
            self.sync()

    def action_pane(self, number):
        if len(self.screen_stack) == 1:
            self.select_pane(number)

    async def install_panes(self, channels, instance):
        self._uart_rebuilding = True
        try:
            await self.controller.select(channels, instance, replay=True)
            if self.tx:
                self.tx.acknowledged.clear()
            grid = self.query_one("#panes", Grid)
            await grid.remove_children()
            await grid.mount(*(UARTPane(cid, state, i) for i, (cid, state) in enumerate(self.controller.panes.items(), 1)))
            self.selected = 0
        finally:
            self._uart_rebuilding = False
        self.preferred_channels = [c["channel_id"] for c in channels]
        if self.selection_saved is not None:
            try:
                await self.selection_saved(self.preferred_channels)
            except (OSError, ValueError) as exc:
                self.note("已開始監看，但本次通道選擇未保存：" + label(exc))
                return
        self.note("已開始監看；重開 RX 使用所選窗格，Q 清理後離開。" if self.local_launch else "已開始監看；Q 只關閉 Client，Gateway 繼續執行。")
        self.select_pane(0)

    async def query_and_choose(self):
        await self.controller.refresh()
        channels = self.controller.available
        if not channels:
            self.note("目前沒有可讀通道；請確認權限，R 更新，Q 離開。")
            return
        instance = self.controller.available_instance
        if self.requested_channels:
            wanted = set(self.requested_channels)
            selected = [c for c in channels if c["channel_id"] in wanted]
            if len(selected) != len(wanted):
                raise ValueError("指定通道不存在或無讀取權限。")
            self.requested_channels = None
            await self.install_panes(selected, instance)
        else:
            def selected(result):
                if result:
                    self.operation(lambda: self.install_panes(result, instance))
                else:
                    self.note("已取消選擇；現有監看繼續，C 可重新選擇。")
            self.push_screen(ChannelPicker(channels, self.preferred_channels), selected)

    def action_choose(self):
        if len(self.screen_stack) == 1:
            if self.local_launch and not self._uart_monitor_active:
                self.operation(self.prepare_local)
            elif self.controller.connect is not None:
                self.operation(self.query_and_choose)

    def action_refresh_channels(self):
        if len(self.screen_stack) != 1:
            return
        if not self.controller.panes:
            self.action_choose()
        else:
            async def refresh():
                if not await self.controller.refresh():
                    self.note("狀態已更新；未重開裝置或重建訂閱。")
            self.operation(refresh)

    def action_resume(self):
        if len(self.screen_stack) != 1 or self.current() is None:
            return
        cid = self.current().snapshot["channel_id"]
        async def resume():
            if not await self.controller.refresh():
                self.controller.start(cid, replay=False)
                self.note("已重新監看即時資料；期間資料請另用錄製檔檢查。")
        self.operation(resume)

    def action_reopen(self):
        if len(self.screen_stack) != 1 or self.current() is None:
            return
        cid = self.current().snapshot["channel_id"]
        async def prepare():
            selected = await self.controller.prepare_reopen(cid)
            port = (selected.get("adapter") or {}).get("port") or cid
            async def reopen():
                self.note("正在重開 " + label(port) + " 的 RX；其他窗格繼續接收。")
                await self.controller.reopen(cid, selected)
                # 新世代啟動後立即解除該窗格的暫停快照，不等下一次畫面計時器。
                for widget in self.query(UARTPane):
                    if widget.cid == cid:
                        widget.sync(widget.number - 1 == self.selected)
            def confirm(accepted):
                if accepted:
                    self.operation(reopen)
                else:
                    self.note("已取消重開，未操作 UART。")
            self.push_screen(ConfirmDialog(f"要重開 {label(port)} 的 RX 嗎？\n請確認接線。新世代會清除該路記憶體歷史；不 Confirm、不取得 Lease、不發送 UART 指令。"), confirm)
        self.operation(prepare)

    def action_pause_view(self):
        if len(self.screen_stack) == 1 and (view := self.current_view()) is not None:
            view.toggle_pause()
            self.note("已暫停畫面，仍持續接收；再按 P 恢復。" if view.frozen is not None else "已恢復畫面並追蹤最新資料。")

    def action_latest(self):
        if len(self.screen_stack) == 1 and (view := self.current_view()) is not None:
            view.latest()

    def on_button_pressed(self, event: Button.Pressed):
        actions = {"refresh": self.action_refresh_channels, "resume": self.action_resume, "reopen": self.action_reopen,
                   "pause": self.action_pause_view, "latest": self.action_latest, "choose": self.action_choose, "quit": self.action_quit,
                   "tx-send": self.action_send_tx, "tx-results": self.action_tx_results}
        if event.button.id in actions:
            actions[event.button.id]()

    def action_send_tx(self):
        if len(self.screen_stack) != 1 or self.tx is None or self.current() is None:
            return
        cid = self.current().snapshot["channel_id"]
        async def prepare():
            if await self.controller.refresh():
                return
            target = await self.tx.prepare(self.controller.panes[cid].snapshot, self.controller.instance)
            if self.tx.is_acknowledged(target):
                self.compose_tx(target)
                return
            async def acknowledge():
                confirmed = await self.tx.acknowledge(target)
                pane = self.controller.panes[cid]
                if target_key(target.instance, confirmed.channel) != target_key(target.instance, target.channel):
                    await self.controller.stop(cid)
                    pane.snapshot = dict(confirmed.channel, tx_available=True, reopen_rx_available=True)
                    self.controller.start(cid, replay=True)
                self.compose_tx(confirmed)
            def chosen(yes):
                if yes:
                    self.operation(acknowledge)
                else:
                    self.note("已取消目標核對；未 Confirm、未取得租約、未傳送。")
            self.push_screen(TargetDialog(target), chosen)
        self.operation(prepare)

    def compose_tx(self, target):
        def preview(payload):
            if payload is None:
                self.note("已取消輸入，未傳送。")
                return
            async def send():
                self.note("正在核對目標、取得單次租約並等待結果；其他 UART 繼續接收。")
                receipt = await self.tx.send(target, payload)
                await self.show_tx_results(receipt.tx_id)
            self.push_screen(PreviewDialog(target, payload), lambda yes: self.operation(send) if yes else self.note("已取消預覽，未取得租約或傳送。"))
        self.push_screen(ComposeDialog(target), preview)

    async def show_tx_results(self, selected=None):
        receipts = await asyncio.to_thread(self.tx.journal.list)
        if not receipts:
            self.note("目前沒有 TX 操作收據；未傳送任何資料。")
            return
        def chosen(receipt):
            async def query():
                await self.tx.query(receipt)
                await self.show_tx_results(receipt.tx_id)
            if receipt is not None:
                self.operation(query)
        self.push_screen(ResultsDialog(receipts, selected, self.tx.notice), chosen)

    def action_tx_results(self):
        if len(self.screen_stack) == 1 and self.tx is not None:
            self.operation(self.show_tx_results)

    async def prepare_local(self):
        try:
            description = await self.local_preview()
            message = description + "\n按開始後啟動本次 Gateway；Q／Ctrl+C 清理後離開。"
        except ERRORS as exc:
            self.failure = exc
            message = "本機設定尚無法啟動：" + label(exc) + "\n請修正後重試，或取消離開。"
        def chosen(value):
            if value == "devices":
                self.operation(self.pick_devices)
            elif value == "record":
                async def record():
                    await self.local_record()
                    await self.prepare_local()
                self.operation(record)
            elif value:
                self.start_local()
            else:
                self.action_quit()
        self.push_screen(ConfirmDialog(message, start=True, local_options=self.local_devices is not None), chosen)

    async def pick_devices(self):
        devices, selected = await self.local_devices()
        if not devices:
            self.note("目前沒有 Serial 裝置；接回 USB-UART 後可按選擇 UART 重新列出。")
            await self.prepare_local()
            return
        def chosen(result):
            async def apply():
                if result:
                    await self.local_select(result)
                await self.prepare_local()
            self.operation(apply)
        self.push_screen(DevicePicker(devices, selected), chosen)

    def start_local(self):
        if self._uart_local_task is not None and not self._uart_local_task.done():
            self.note("Gateway 正在啟動或清理，未建立重複程序。")
            return
        self.failure = None
        self._uart_monitor_active = False
        async def monitor(connect, channels, instance):
            self.controller.connect = connect
            await self.install_panes(channels, instance)
            self._uart_monitor_active = True
            await self._uart_finish.wait()
            return 0
        async def launch():
            try:
                await self.local_launch(monitor, self.gateway_report)
            except LauncherCleanupError as exc:
                self.failure = exc
                self.action_quit()
            except ERRORS as exc:
                self.failure = exc
                self.note("本次啟動已結束：" + label(exc))
                if not self._uart_closing:
                    self.push_screen(ConfirmDialog("Gateway 啟動／接收未完成：" + label(exc) + "\n可確認重試；取消離開後可重新選 UART。", start=True),
                                     lambda yes: self.start_local() if yes else self.action_quit())
        self._uart_local_task = asyncio.create_task(launch())

    async def cleanup(self):
        async with self._uart_cleanup_lock:
            if self._uart_control is not None and not self._uart_control.done():
                self._uart_control.cancel()
                await asyncio.gather(self._uart_control, return_exceptions=True)
            self._uart_finish.set()
            if self._uart_local_task is not None and not self._uart_monitor_active and not self._uart_local_task.done():
                self._uart_local_task.cancel()
            await self.controller.stop()
            if self._uart_local_task is not None:
                results = await asyncio.gather(self._uart_local_task, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        self.failure = result
            await self.controller.stop()

    def action_quit(self):
        if self._uart_closing:
            return
        self._uart_closing = True
        self.note("正在清理本次 Gateway……" if self.local_launch else "正在關閉本次 Client，Gateway 繼續執行……")
        async def quit_app():
            await self.cleanup()
            self.exit(1 if self.failure else 0)
        self._uart_quit_task = asyncio.create_task(quit_app())

    def action_quit_key(self):
        self.action_quit()

    async def on_unmount(self):
        self._uart_closing = True
        await self.cleanup()
