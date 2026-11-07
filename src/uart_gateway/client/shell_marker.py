"""POSIX 單行包裝與有界標記解析；只產生 bytes，不在本機執行命令。"""

import re
import shlex


def markers(execution_id: str) -> tuple[bytes, bytes]:
    if not isinstance(execution_id, str) or not re.fullmatch(r"[0-9a-f]{32}", execution_id):
        raise ValueError("Shell 執行識別不合法。")
    return ("__UART_BEGIN_" + execution_id + "__").encode(), ("__UART_END_" + execution_id + "__:").encode()


def shell_payload(command: str, execution_id: str, newline: str, maximum: int = 4096) -> bytes:
    if not isinstance(command, str) or not command.strip() or len(command) > 4096 or any(not c.isprintable() for c in command):
        raise ValueError("請提供單行非空命令，不可含隱藏控制字元。")
    if newline not in ("lf", "cr", "crlf"):
        raise ValueError("Shell 換行必須明確選 LF、CR 或 CRLF。")
    begin, end = markers(execution_id)
    # 拆開標記；TTY echo 的包裝原文不會包含完整完成標記。
    source = (f"( printf '\\n%s%s\\n' '__UART_BEGIN_' '{execution_id}__'; "
        f"if sh -c {shlex.quote(command)} </dev/null 2>&1; then _uart_rc=0; else _uart_rc=$?; fi; "
        f"printf '\\n%s%s:%s\\n' '__UART_END_' '{execution_id}__' \"$_uart_rc\" )")
    try:
        payload = source.encode("utf-8") + {"lf": b"\n", "cr": b"\r", "crlf": b"\r\n"}[newline]
    except UnicodeError:
        raise ValueError("命令包含無法編碼的文字，請使用有效 UTF-8。") from None
    if len(payload) > min(4096, maximum):
        raise ValueError("命令含包裝及換行超過單次 TX 上限，不會拆分或傳送。")
    if begin in payload or end in payload:
        raise ValueError("命令不可包含本次保留標記。")
    return payload


class ShellMarkers:
    def __init__(self, execution_id: str, maximum: int = 65536):
        if type(maximum) is not int or not 1 <= maximum <= 1048576:
            raise ValueError("Shell 輸出上限須為 1～1048576 bytes。")
        self.begin, self.end = markers(execution_id)
        self.maximum = maximum
        self.started = self.done = False
        self.exit_code: int | None = None
        self.pending = bytearray()
        self.buffer = bytearray()
        self.total = 0
        self.tail = b""
        self.line_start = False

    def _append(self, data: bytes) -> None:
        self.total += len(data)
        self.tail = (self.tail + data)[-2:]
        self.buffer.extend(data[:max(0, self.maximum - len(self.buffer))])

    def feed(self, data: bytes) -> None:
        for byte in data:
            if self.done:
                break
            char = bytes((byte,))
            if not self.started:
                if not self.line_start:
                    if byte == 10:
                        self.line_start = True
                    continue
                self.pending.append(byte)
                value = bytes(self.pending)
                if value in (self.begin + b"\n", self.begin + b"\r\n"):
                    self.started, self.line_start = True, True
                    self.pending.clear()
                elif not any(marker.startswith(value) for marker in (self.begin + b"\n", self.begin + b"\r\n")):
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
    def observed_bytes(self) -> int:
        return self.total + (len(self.pending) if self.started else 0)

    @property
    def output(self) -> bytes:
        return (bytes(self.buffer) + (bytes(self.pending) if self.started else b""))[:self.maximum]

    @property
    def truncated(self) -> bool:
        return self.observed_bytes > self.maximum
