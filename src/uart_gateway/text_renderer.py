"""增量終端顯示過濾器；只放行基本排版與有限的 SGR 顏色。"""

import re


RESET = "\x1b[0m"


def _color_parameters(parameters: str) -> bool:
    """只接受重設、粗體及前景／背景色，不接受隱藏或任意 CSI。"""
    if not parameters:
        return True
    if any(char not in "0123456789;" for char in parameters):
        return False
    values = [int(value or "0") for value in parameters.split(";")]
    index = 0
    while index < len(values):
        code = values[index]
        index += 1
        if code in (0, 1, 22, 39, 49) or 30 <= code <= 37 or 40 <= code <= 47 or 90 <= code <= 97 or 100 <= code <= 107:
            continue
        if code not in (38, 48) or index >= len(values):
            return False
        mode = values[index]
        count = 1 if mode == 5 else 3 if mode == 2 else 0
        index += 1
        if not count or index + count > len(values) or any(value > 255 for value in values[index:index + count]):
            return False
        index += count
    return True


class TerminalRenderer:
    """跨區塊辨識控制序列；參數暫存有上限，控制字串不累積內容。"""

    def __init__(self) -> None:
        self.state = "text"
        self.parameters = ""
        self.valid = True
        self.osc = False

    def feed(self, text: str) -> str:
        result = []
        for char in text:
            if self.state in ("string", "string_escape"):
                if char == "\x9c" or (self.osc and char == "\x07") or (self.state == "string_escape" and char == "\\"):
                    self.state = "text"
                else:
                    self.state = "string_escape" if char == "\x1b" else "string"
                continue
            if char == "\x1b":
                self.state = "escape"
                self.parameters = ""
                self.valid = True
                continue
            if char == "\x9b":
                # C1 形式只辨識並丟棄，不轉送原始控制碼。
                self.state, self.parameters, self.valid = "csi", "", False
                continue
            if char in "\x90\x98\x9d\x9e\x9f":
                self.state, self.osc = "string", char == "\x9d"
                continue
            if char in "\x18\x1a":
                self.state, self.parameters = "text", ""
                continue
            if self.state == "escape":
                if char == "[":
                    self.state = "csi"
                elif char in "]PX^_":
                    self.state, self.osc = "string", char == "]"
                elif " " <= char <= "/":
                    self.state = "escape_intermediate"
                else:
                    self.state = "text"
                continue
            if self.state == "escape_intermediate":
                if not " " <= char <= "/":
                    self.state = "text"
                continue
            if self.state == "csi":
                if "@" <= char <= "~":
                    if char == "m" and self.valid and _color_parameters(self.parameters):
                        result.append("\x1b[" + self.parameters + "m")
                    self.state, self.parameters = "text", ""
                elif char in "0123456789;" and len(self.parameters) < 128:
                    self.parameters += char
                else:
                    self.valid = False
                continue
            if char.isprintable() or char in "\r\n\b\t":
                result.append(char)
        return "".join(result)

    def finish(self) -> str:
        """丟棄未完成的控制序列，結束時恢復終端顏色。"""
        self.state, self.parameters = "text", ""
        return RESET


class TaggedText:
    """合併畫面每批／每行標明來源；只保存有限的 SGR 樣式狀態。"""

    def __init__(self, label: str, *, terminal: bool):
        self.label, self.terminal = label, terminal
        self.styles: dict[str, str] = {}

    def _remember(self, text: str) -> None:
        for match in re.finditer(r"\x1b\[([0-9;]*)m", text):
            values = [int(value or "0") for value in match[1].split(";")]
            index = 0
            while index < len(values):
                code = values[index]
                index += 1
                if code == 0:
                    self.styles.clear()
                elif code in (1, 22):
                    self.styles["bold"] = str(code)
                elif code in (38, 48):
                    count = 2 if values[index] == 5 else 4
                    self.styles["fg" if code == 38 else "bg"] = ";".join(map(str, [code] + values[index:index + count]))
                    index += count
                else:
                    self.styles["fg" if code == 39 or 30 <= code <= 37 or 90 <= code <= 97 else "bg"] = str(code)

    def format(self, text: str) -> str:
        if not text:
            return ""
        # 合併畫面不能讓裝置 CR／BS 改寫來源標記或另一通道。
        text = text.replace("\r", "\\r").replace("\b", "\\x08")
        lines = text.split("\n")
        if lines[-1] == "":
            lines.pop()
        output = []
        for line in lines:
            prefix = RESET if self.terminal else ""
            style = "\x1b[" + ";".join(self.styles.values()) + "m" if self.terminal and self.styles else ""
            output.append(f"{prefix}[{self.label}] {style}{line}{prefix}\n")
            if self.terminal:
                self._remember(line)
        return "".join(output)
