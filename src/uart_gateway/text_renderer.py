"""增量終端顯示過濾器；只放行基本排版與有限的 SGR 顏色。"""


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
