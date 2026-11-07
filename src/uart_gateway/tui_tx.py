"""TX 表單：目標核對、輸入、精確預覽與收據查詢；不直接操作 SDK。"""

from rich.text import Text
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select, Static, TextArea

from .client.tx_workflow import encode_payload
from .remote_workbench import label


class FormBody(VerticalScroll):
    can_focus = False


def target_text(target) -> str:
    c = target.channel
    a = c.get("adapter") or {}
    return (f"目標：{label(c['label'])}／{label(c['channel_id'])}\n"
        f"端點：{label(a.get('port') or '未提供')}；序號：{label(a.get('serial_number') or '未提供')}\n"
        f"備註：{label(c.get('target_note') or '無')}\n"
        f"Gateway：{label(target.instance)[:8]}；修訂：{c['binding']['revision']}；世代：{label(c['stream_epoch'])[:8]}")


def result_text(receipt) -> str:
    r = receipt.result
    state = {"written_to_driver": "Driver 已接受全部 bytes；不代表 IPCAM 已執行成功。", "pending": "仍在等待結果；不重送。",
        "unknown": "結果不明；可能已送出部分或全部資料，不重送。", "rejected": "已接受後在寫入前取消／拒絕。",
        "not_accepted": "Gateway 未接受本次傳送要求。"}[r["state"]]
    submitted = r["submitted_bytes"] if r["submitted_bytes"] is not None else "未知"
    accepted = r["accepted_bytes"] if r["accepted_bytes"] is not None else "未知"
    origin = {"local": "本機待確認", "record": "Gateway 紀錄", "unavailable": "查無紀錄"}[r["status_origin"]]
    query = "\n本次查詢查無紀錄，以上保留先前收據；不表示未執行。" if receipt.last_query and receipt.last_query["status_origin"] == "unavailable" else ""
    if receipt.execution is not None:
        query += "\n這是 Shell 命令的 TX 收據；命令完成／輸出請用 uart exec-status 查詢。"
    return (f"{state}\n通道：{receipt.channel_id}\n時間：{receipt.created}\nTX ID：{receipt.tx_id}\n"
        f"原 Gateway：{receipt.instance}\n操作身分：{receipt.principal}\n"
        f"提交量：{submitted}；Driver 接受量：{accepted} bytes\n"
        f"原因：{r['reason_code'] or '無'}；來源：{origin}\n"
        "紀錄不含命令內容；查無資料不能推論未執行。" + query)


class TargetDialog(ModalScreen):
    BINDINGS = [("escape", "cancel", "取消")]

    def __init__(self, target):
        super().__init__()
        self.target = target

    def compose(self):
        with Vertical(classes="dialog"):
            with FormBody():
                yield Label(Text("確認 TX 目標\n" + target_text(self.target)))
                yield Static("核對實體接線與 RX 畫面；同 Adapter 換板無法自動辨識。\n未確認的通道會建立新世代，只重新監看此路。", markup=False)
                yield Checkbox("我已核對目標與接線，允許此目標 TX", id="tx-ack")
                yield Static("尚未勾選，無法確認。", id="tx-target-status", markup=False)
            with Horizontal():
                yield Button("取消", id="tx-cancel")
                yield Button("確認目標", id="tx-target-confirm", disabled=True, variant="primary")

    def on_mount(self):
        self.query_one(Checkbox).focus()

    def action_cancel(self):
        self.dismiss(False)

    def on_checkbox_changed(self, event: Checkbox.Changed):
        self.query_one("#tx-target-confirm", Button).disabled = not event.value
        self.query_one("#tx-target-status", Static).update("已勾選；點確認目標，或 Tab 選擇後 Enter。" if event.value else "尚未勾選，無法確認。")

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        self.dismiss(event.button.id == "tx-target-confirm" and self.query_one(Checkbox).value)


class ComposeDialog(ModalScreen):
    BINDINGS = [("escape", "cancel", "取消")]

    def __init__(self, target):
        super().__init__()
        self.target = target

    def compose(self):
        with Vertical(classes="dialog"):
            with FormBody():
                yield Label(Text("傳送至：" + label(self.target.channel["label"]) + "／" + label(self.target.channel["channel_id"])))
                yield Input(placeholder="輸入內容；文字不解讀反斜線跳脫", max_length=12288, id="tx-text")
                yield Select([("文字（UTF-8）", "text"), ("二進位（Hex）", "hex")], value="text", allow_blank=False, id="tx-encoding")
                yield Select([("不附加換行", "none"), ("LF（0A）", "lf"), ("CR（0D）", "cr"), ("CRLF（0D 0A）", "crlf")],
                             value="none", allow_blank=False, id="tx-newline")
                yield Static("Enter 只進入預覽；不直接傳送。尚未取得租約。", markup=False)
                yield Static("", id="tx-input-error", markup=False)
            with Horizontal():
                yield Button("取消", id="tx-cancel")
                yield Button("預覽", id="tx-preview", variant="primary")

    def on_mount(self):
        self.query_one(Input).focus()

    def action_cancel(self):
        self.dismiss(None)

    def preview(self):
        try:
            payload = encode_payload(self.query_one(Input).value, self.query_one("#tx-newline", Select).value,
                self.query_one("#tx-encoding", Select).value, self.target.maximum)
        except ValueError as exc:
            self.query_one("#tx-input-error", Static).update(Text(str(exc)))
        else:
            self.dismiss(payload)

    def on_input_submitted(self, event: Input.Submitted):
        event.stop()
        self.preview()

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        self.preview() if event.button.id == "tx-preview" else self.action_cancel()


class PreviewDialog(ModalScreen):
    BINDINGS = [("escape", "cancel", "取消")]

    def __init__(self, target, payload: bytes):
        super().__init__()
        self.target, self.payload = target, payload

    def compose(self):
        with Vertical(classes="dialog"):
            yield Label(Text(f"送出前預覽：{label(self.target.channel['label'])}／{label(self.target.channel['channel_id'])}\n"
                             f"共 {len(self.payload)} bytes（已含所選換行）；只送一次。"))
            text = repr(self.payload.decode("utf-8", errors="backslashreplace"))
            yield TextArea("文字表示：\n" + text + "\n\n完整 Hex：\n" + self.payload.hex(" "), read_only=True, id="tx-bytes")
            yield Static("送出時才取得單次租約；結果不明只查詢，不自動重送。", markup=False)
            with Horizontal():
                yield Button("取消", id="tx-cancel")
                yield Button("送出一次", id="tx-send-once", variant="warning")

    def on_mount(self):
        self.query_one("#tx-cancel", Button).focus()

    def action_cancel(self):
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        self.dismiss(event.button.id == "tx-send-once")


class ResultsDialog(ModalScreen):
    BINDINGS = [("escape", "cancel", "返回")]

    def __init__(self, receipts, selected=None, notice=""):
        super().__init__()
        self.receipts = {r.tx_id: r for r in receipts}
        self.selected = selected if selected in self.receipts else receipts[0].tx_id
        self.notice = notice

    def compose(self):
        with Vertical(classes="dialog"):
            yield Label(Text("TX 結果／操作收據；只查詢，不會重送。"))
            yield Select([(f"{r.created[:19]}／{r.channel_id}／{r.tx_id[:8]}", r.tx_id) for r in self.receipts.values()],
                value=self.selected, allow_blank=False, id="tx-receipt")
            yield TextArea(result_text(self.receipts[self.selected]), read_only=True, id="tx-result")
            yield Static(Text(self.notice), id="tx-result-notice")
            with Horizontal():
                yield Button("返回監看", id="tx-cancel")
                yield Button("查詢所選結果", id="tx-query", variant="primary")

    def action_cancel(self):
        self.dismiss(None)

    def on_select_changed(self, event: Select.Changed):
        if event.value in self.receipts:
            self.selected = event.value
            self.query_one(TextArea).load_text(result_text(self.receipts[event.value]))

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        self.dismiss(self.receipts[self.selected] if event.button.id == "tx-query" else None)
