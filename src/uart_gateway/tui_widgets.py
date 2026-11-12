"""固定窗格與確認畫面；裝置資料只轉成 Rich 格子，不輸出原始 ANSI。"""

from functools import lru_cache

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.geometry import Size
from textual.screen import ModalScreen
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.widgets import Button, Label, SelectionList, Static

from .remote_workbench import label

STATES = {"ready": "就緒", "lost": "裝置遺失", "faulted": "故障", "closed": "已關閉", "opening": "開啟中",
          "draining": "關閉中", "unavailable": "狀態失效"}

def update_static(widget: Static, text: str, *, wrap: bool = False):
    if getattr(widget, "_uart_text", None) != text:
        widget._uart_text = text
        widget.update(Text(text, no_wrap=not wrap, overflow="ellipsis"), layout=False)


@lru_cache(maxsize=512)
def cell_style(fg: str, bg: str, bold: bool) -> Style:
    def color(value):
        if value == "default":
            return None
        if len(value) == 6 and all(c in "0123456789abcdef" for c in value):
            return "#" + value
        if value.startswith("bright"):
            return "bright_" + value[6:]
        return "yellow" if value == "brown" else value
    return Style(color=color(fg), bgcolor=color(bg), bold=bold)


class SerialView(ScrollView):
    BINDINGS = [("pageup", "page_up", "往前"), ("pagedown", "page_down", "往後")]

    def __init__(self, model, **kwargs):
        super().__init__(**kwargs)
        self.model = model
        self.frozen = None
        self.rows = []
        self.version = -1
        self.follow = True
        self.frozen_size = None
        self.generation = model.generation

    def sync(self) -> None:
        if self.generation != self.model.generation:
            self.generation = self.model.generation
            self.frozen, self.frozen_size, self.follow = None, None, True
            self.version = -1
        region = self.scrollable_content_region
        if region.width >= 10 and region.height >= 2:
            self.model.resize(region.width, region.height)
        if self.frozen is not None:
            size = (self.size.width, self.size.height, id(self.frozen))
            if size == self.frozen_size:
                return
            self.frozen_size = size
            self.rows = self.frozen
        elif self.version == self.model.version:
            return
        else:
            self.rows = self.model.lines()
        self.version = self.model.version
        self.virtual_size = Size(self.model.screen.columns, len(self.rows))
        if self.follow:
            self.scroll_end(animate=False, immediate=True)
        self.refresh()

    def render_line(self, y: int) -> Strip:
        index = y + int(self.scroll_y)
        row = self.rows[index] if index < len(self.rows) else {}
        segments, current, text = [], None, ""
        base = self.rich_style
        for column in range(self.model.screen.columns):
            cell = row.get(column)
            style = base + cell_style(cell.fg, cell.bg, cell.bold) if cell and (cell.fg, cell.bg, cell.bold) != ("default", "default", False) else base
            data = cell.data if cell else " "
            if style != current and text:
                segments.append(Segment(text, current))
                text = ""
            current, text = style, text + data
        if text:
            segments.append(Segment(text, current))
        return Strip(segments).crop(int(self.scroll_x), int(self.scroll_x) + self.size.width).adjust_cell_length(self.size.width, self.rich_style)

    def on_resize(self):
        self.sync()

    def toggle_pause(self):
        self.frozen = None if self.frozen is not None else self.model.lines(freeze=True)
        self.follow = self.frozen is None
        self.frozen_size = None
        self.version = -1
        self.sync()

    def latest(self):
        self.frozen, self.follow, self.version = None, True, -1
        self.frozen_size = None
        self.sync()

    def action_page_up(self):
        self.follow = False
        super().action_page_up()

    def on_mouse_scroll_up(self):
        self.follow = False


class UARTPane(Vertical):
    def __init__(self, cid, state, number):
        super().__init__(id=f"pane-{number}", classes="uart-pane")
        self.cid, self.state, self.number = cid, state, number

    def compose(self) -> ComposeResult:
        yield Static("", classes="pane-title", markup=False)
        yield SerialView(self.state.terminal, classes="serial-view")
        yield Static("", classes="pane-state", markup=False)

    def on_click(self):
        self.app.select_pane(self.number - 1)

    def sync(self, active: bool):
        self.set_class(active, "selected")
        view = self.query_one(SerialView)
        view.sync()
        snapshot, pane = self.state.snapshot, self.state
        name = snapshot.get("adapter", {}).get("port") or self.cid
        update_static(self.query_one(".pane-title", Static), f"{self.number}. {label(name)}／{label(snapshot['label'])}")
        recording = snapshot.get("recording", {}).get("state", "disabled")
        recorded = {"disabled": "關", "recording": "開", "degraded": "連續性未知", "failed": "失敗"}.get(recording, "未知")
        mode = pane.delivery if pane.receiving == "監看中" and pane.delivery else pane.receiving
        if view.frozen is not None:
            mode = "畫面暫停（仍接收）" if pane.receiving == "監看中" else "畫面暫停／接收停止"
        line = f"{STATES.get(snapshot['state'], '未知')}｜{mode}｜{pane.rx_bytes} B｜錄製：{recorded}"
        error = pane.error or (snapshot.get("last_error") or {}).get("message", "")
        if pane.terminal.screen.omitted_marks:
            error = "畫面組合符號超限已省略；原始錄製不變。"
        context = f"修訂 {snapshot['binding']['revision']}｜世代 {str(pane.epoch or snapshot.get('stream_epoch') or '無')[:8]}"
        if snapshot.get("tx_available"):
            context += "｜TX：" + ("目標已確認" if snapshot["binding"]["status"] == "confirmed" else "目標待確認")
        update_static(self.query_one(".pane-state", Static), line + "\n" + label(error or context))


class ChannelPicker(ModalScreen):
    BINDINGS = [("escape", "cancel", "取消")]

    def __init__(self, channels, preferred=None):
        super().__init__()
        self.channels = channels
        self.preferred = preferred

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(Text("選擇要顯示的 UART（最多四路）"))
            yield Static("Tab 移動、空白勾選，Enter 開始。套用會重新建立本次監看。", markup=False)
            yield SelectionList(*[(Text(f"{label(c['adapter'].get('port') or c['channel_id'])}／{label(c['label'])} | {STATES.get(c['state'], '未知')}"),
                                   c["channel_id"], self.preferred is None or c["channel_id"] in self.preferred) for c in self.channels], id="channels")
            with Horizontal():
                yield Button("取消", id="cancel")
                yield Button("開啟所選", id="open", variant="primary")
            yield Static("", id="selection-error", markup=False)

    def on_mount(self):
        self.query_one("#open", Button).focus()

    def action_cancel(self):
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "cancel":
            self.dismiss(None)
        else:
            selected = self.query_one(SelectionList).selected
            if not 1 <= len(selected) <= 4:
                self.query_one("#selection-error", Static).update("請選擇一至四條 UART。")
            else:
                self.dismiss([c for c in self.channels if c["channel_id"] in selected])


class DevicePicker(ModalScreen):
    BINDINGS = [("escape", "cancel", "取消")]

    def __init__(self, devices, selected_ports):
        super().__init__()
        self.devices, self.selected_ports = devices, selected_ports

    def compose(self):
        with Vertical(classes="dialog"):
            yield Label(Text("選擇本機 UART（最多兩路）"))
            yield Static("Tab 移動、空白勾選；只列出裝置，尚未開啟。", markup=False)
            yield SelectionList(*[(Text(f"{label(d.adapter.port)}／{label(d.description or '未提供描述')}"), i,
                                   d.adapter.port in self.selected_ports) for i, d in enumerate(self.devices)], id="devices")
            with Horizontal():
                yield Button("取消", id="cancel")
                yield Button("使用所選裝置", id="apply", variant="primary")
            yield Static("", id="device-error", markup=False)

    def on_mount(self):
        self.query_one(SelectionList).focus()

    def action_cancel(self):
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "cancel":
            self.dismiss(None)
        else:
            selected = self.query_one(SelectionList).selected
            if not 1 <= len(selected) <= 2:
                self.query_one("#device-error", Static).update("請勾選一至兩條 UART。")
            else:
                self.dismiss([self.devices[i] for i in selected])


class ConfirmDialog(ModalScreen):
    BINDINGS = [("escape", "cancel", "取消"), ("y", "confirm", "確認")]

    def __init__(self, message: str, *, start=False, local_options=False):
        super().__init__()
        self.message, self.start = message, start
        self.local_options = local_options

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog compact"):
            yield Label(Text(self.message))
            if self.local_options:
                with Horizontal():
                    yield Button("選擇 UART", id="devices")
                    yield Button("切換錄製", id="record")
            with Horizontal():
                yield Button("取消", id="cancel")
                yield Button("開始監看" if self.start else "確認重開（Y）", id="confirm", variant="primary")

    def on_mount(self):
        self.query_one("#confirm" if self.start else "#cancel", Button).focus()

    def action_cancel(self):
        self.dismiss(False)

    def action_confirm(self):
        self.dismiss(True)

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id if event.button.id in ("devices", "record") else event.button.id == "confirm")
