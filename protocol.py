"""最小 NDJSON 協定及 POSIX Shell 標記；不在本機執行命令。"""

import base64
import json
import math
import re
import shlex

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18081
MAX_FRAME = 2 * 1024 * 1024
MAX_COMMAND = 4096
MAX_OUTPUT = 1024 * 1024


def encode(message):
    wire = json.dumps(message, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode() + b"\n"
    if len(wire) > MAX_FRAME:
        raise ValueError("NDJSON 訊息過大")
    return wire


def decode(line):
    if not line or not line.endswith(b"\n") or len(line) > MAX_FRAME:
        raise ValueError("NDJSON 訊息不完整或過大")
    def invalid_constant(value):
        raise ValueError("JSON 不允許非有限數值")
    value = json.loads(line, parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError("NDJSON 必須為物件")
    return value


def read_message(stream):
    return decode(stream.readline(MAX_FRAME + 1))


def b64(data):
    return base64.b64encode(data).decode("ascii")


def unb64(value):
    return base64.b64decode(value, validate=True)


def validate_exec(request):
    command = request.get("command")
    if not isinstance(command, str) or not command.strip() or not all(c.isprintable() for c in command):
        raise ValueError("command 必須為非空單行文字，不可含控制字元")
    if len(command.encode("utf-8")) > MAX_COMMAND:
        raise ValueError("命令過長")
    cwd = request.get("cwd")
    if cwd is not None and (not isinstance(cwd, str) or not cwd.strip()
                            or not all(c.isprintable() for c in cwd)
                            or len(cwd.encode("utf-8")) > MAX_COMMAND):
        raise ValueError("cwd 必須為非空單行 IPCam 路徑，不可含控制字元，最多 4096 bytes")
    timeout = request.get("timeout", 10)
    if type(timeout) not in (float, int) or not math.isfinite(timeout) or not 0.1 <= timeout <= 300:
        raise ValueError("timeout 必須為 0.1～300 秒")
    maximum = request.get("max_output", MAX_OUTPUT)
    if type(maximum) is not int or not 1 <= maximum <= MAX_OUTPUT:
        raise ValueError("max_output 必須為 1～1048576 bytes")
    return command, timeout, maximum


def markers(command_id):
    if not isinstance(command_id, str) or not re.fullmatch(r"[0-9a-f]{32}", command_id):
        raise ValueError("命令 ID 不合法")
    return ("__UART_BEGIN_" + command_id + "__").encode(), ("__UART_END_" + command_id + "__:").encode()


def shell_payload(command, command_id, newline="cr", cwd=None):
    validate_exec({"command": command, "cwd": cwd})
    begin, end = markers(command_id)
    if cwd is not None:
        # 路徑只作為字面值；停用 CDPATH，且不讓以 '-' 開頭的路徑變成 cd 選項。
        path = "./" + cwd if cwd.startswith("-") else cwd
        # 用 exec sh 包住整條命令，避免 cd 失敗時仍執行分號後的部分。
        command = f"CDPATH= cd {shlex.quote(path)} && exec sh -c {shlex.quote(command)}"
    # 拆開標記，避免完整標記出現在 TTY echo；子 Shell 的 exit 不會離開登入 Shell。
    # 外層子 Shell 接住 INT，讓被中斷的內層命令仍能回傳 Exit Code／完成標記。
    source = (f"( trap ':' INT; printf '\\n%s%s\\n' '__UART_BEGIN_' '{command_id}__'; "
              f"if sh -c {shlex.quote(command)} </dev/null 2>&1; then _uart_rc=0; else _uart_rc=$?; fi; "
              f"printf '\\n%s%s:%s\\n' '__UART_END_' '{command_id}__' \"$_uart_rc\" )")
    wire = source.encode("utf-8") + {"cr": b"\r", "lf": b"\n", "crlf": b"\r\n"}[newline]
    if len(wire) > MAX_COMMAND or begin in wire or end in wire:
        raise ValueError("含包裝的命令超過 4096 bytes 或包含保留標記")
    return wire


class ShellMarkers:
    """逐 byte、有界解析；只接受完整行的開始／結束標記。"""

    def __init__(self, command_id, maximum=MAX_OUTPUT):
        self.begin, self.end = markers(command_id)
        self.maximum = maximum
        self.started = self.done = False
        self.exit_code = None
        self.pending = bytearray()
        self.buffer = bytearray()
        self.total = 0
        self.tail = b""
        self.line_start = False

    def _append(self, data):
        self.total += len(data)
        self.tail = (self.tail + data)[-2:]
        self.buffer.extend(data[:max(0, self.maximum - len(self.buffer))])

    def feed(self, data):
        for byte in data:
            if self.done:
                break
            char = bytes((byte,))
            if not self.started:
                if not self.line_start:
                    self.line_start = byte == 10
                    continue
                self.pending.append(byte)
                value = bytes(self.pending)
                if value in (self.begin + b"\n", self.begin + b"\r\n"):
                    self.started = self.line_start = True
                    self.pending.clear()
                elif not any(m.startswith(value) for m in (self.begin + b"\n", self.begin + b"\r\n")):
                    self.pending.clear()
                    self.line_start = byte == 10
                continue
            if not self.line_start:
                self._append(char)
                self.line_start = byte == 10
                continue
            self.pending.append(byte)
            value = bytes(self.pending)
            if len(value) <= len(self.end) and self.end.startswith(value):
                continue
            if value.startswith(self.end):
                suffix = value[len(self.end):]
                match = re.fullmatch(rb"([0-9]{1,3})\r?\n", suffix)
                if match and int(match[1]) <= 255:
                    self.done, self.exit_code = True, int(match[1])
                    self.pending.clear()
                    delimiter = 2 if self.tail == b"\r\n" else 1 if self.tail.endswith(b"\n") else 0
                    self.total -= delimiter
                    del self.buffer[min(self.total, self.maximum):]
                    continue
                if re.fullmatch(rb"[0-9]{0,3}\r?", suffix):
                    continue
            self._append(value)
            self.pending.clear()
            self.line_start = byte == 10

    @property
    def output(self):
        return (bytes(self.buffer) + (bytes(self.pending) if self.started else b""))[:self.maximum]

    @property
    def observed_bytes(self):
        return self.total + (len(self.pending) if self.started else 0)

    @property
    def truncated(self):
        return self.observed_bytes > self.maximum
