"""exec／monitor／status／interrupt／terminal；只使用標準函式庫，支援 Linux 與 Windows。"""

import argparse
import codecs
from contextlib import contextmanager
from datetime import datetime
import json
import os
import re
import select
import socket
import sys
import time

from protocol import DEFAULT_HOST, DEFAULT_PORT, b64, encode, iter_frames, markers, read_message, unb64, validate_exec


def safe_text(text):
    # 不讓設備輸出的控制序列改動操作者終端；原 bytes 由 JSON 保留。
    return "".join(c if c in "\n\r\t" or c.isprintable() else f"\\x{ord(c):02x}" for c in text)


def show_json(value):
    print(json.dumps(value, ensure_ascii=True), flush=True)


def monitor_batches(sock):
    """每次 recv 的完整訊息立即成批交付；保留半筆 NDJSON，定期處理 Ctrl+C。"""
    pending = bytearray()
    while True:
        readable, _, _ = select.select([sock], [], [], 0.1)
        if not readable:
            continue
        data = sock.recv(65536)
        if not data:
            raise ValueError("Monitor 連線中斷" + ("，NDJSON 訊息不完整" if pending else ""))
        batch = []
        try:
            batch.extend(iter_frames(pending, data))
        except (ValueError, UnicodeError, RecursionError):
            if batch:
                yield batch
            raise
        if batch:
            yield batch


def raw_writer():
    decoder = None
    if os.name == "nt" and sys.stdout.isatty():
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        sys.stdout.reconfigure(newline="")
    def write(data, *, final=False):
        if data or (final and decoder is not None):
            if decoder is not None:
                sys.stdout.write(decoder.decode(data, final=final))
                sys.stdout.flush()
            else:
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
    return write


@contextmanager
def terminal_keyboard():
    """原生鍵盤輸入，不做本機 Echo；離開時還原主控台模式。"""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("terminal 需要互動終端，不能使用管線；只讀輸出請用 monitor")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes as w
        class Key(ctypes.Structure):
            _fields_ = [("down", w.BOOL), ("repeat", w.WORD), ("vk", w.WORD), ("scan", w.WORD),
                        ("char", w.WCHAR), ("control", w.DWORD)]
        class EventData(ctypes.Union):
            _fields_ = [("key", Key), ("padding", ctypes.c_byte * 16)]
        class Event(ctypes.Structure):
            _fields_ = [("kind", w.WORD), ("data", EventData)]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetStdHandle.argtypes, kernel.GetStdHandle.restype = [w.DWORD], w.HANDLE
        kernel.GetConsoleMode.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
        kernel.SetConsoleMode.argtypes = [w.HANDLE, w.DWORD]
        kernel.GetNumberOfConsoleInputEvents.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
        kernel.ReadConsoleInputW.argtypes = [w.HANDLE, ctypes.POINTER(Event), w.DWORD, ctypes.POINTER(w.DWORD)]
        kernel.FlushConsoleInputBuffer.argtypes = [w.HANDLE]
        handle = kernel.GetStdHandle(-10)
        original = w.DWORD()
        if not kernel.GetConsoleMode(handle, ctypes.byref(original)):
            raise ctypes.WinError(ctypes.get_last_error())
        # 關閉 Ctrl+C 系統處理、行輸入、Echo、Quick Edit、VT Input；自行轉換按鍵。
        if not kernel.SetConsoleMode(handle, (original.value & ~(0x01 | 0x02 | 0x04 | 0x40 | 0x200)) | 0x80):
            raise ctypes.WinError(ctypes.get_last_error())
        unicode_decoder = codecs.getincrementaldecoder("utf-16-le")("replace")
        special = {0x25: b"\x1b[D", 0x26: b"\x1b[A", 0x27: b"\x1b[C", 0x28: b"\x1b[B",
                   0x24: b"\x1b[H", 0x23: b"\x1b[F", 0x2e: b"\x1b[3~", 0x2d: b"\x1b[2~",
                   0x21: b"\x1b[5~", 0x22: b"\x1b[6~", 0x70: b"\x1bOP", 0x71: b"\x1bOQ",
                   0x72: b"\x1bOR", 0x73: b"\x1bOS"}
        def read():
            count = w.DWORD()
            if not kernel.GetNumberOfConsoleInputEvents(handle, ctypes.byref(count)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not count.value:
                return b""
            events = (Event * min(count.value, 64))()
            if not kernel.ReadConsoleInputW(handle, events, len(events), ctypes.byref(count)):
                raise ctypes.WinError(ctypes.get_last_error())
            data = bytearray()
            for event in events[:count.value]:
                key = event.data.key
                if event.kind != 1 or not key.down:
                    continue
                if key.char != "\x00":
                    text = unicode_decoder.decode(key.char.encode("utf-16-le", "surrogatepass"))
                    raw = text.encode("utf-8")
                    if key.control & 3 and not key.control & 12 and text:
                        raw = b"\x1b" + raw
                else:
                    raw = special.get(key.vk, b"")
                data.extend(raw * max(1, key.repeat))
                if len(data) > 4096:
                    raise ValueError("鍵盤輸入超過 4096 bytes，不會重送已送出的內容")
            return bytes(data)
        try:
            yield read
        finally:
            # 未送出的密碼／按鍵不可在返回 PowerShell 後被當成本機指令。
            kernel.FlushConsoleInputBuffer(handle)
            kernel.SetConsoleMode(handle, original.value)
    else:
        import termios
        import tty
        fd = sys.stdin.fileno()
        original = termios.tcgetattr(fd)
        try:
            tty.setraw(fd, when=termios.TCSANOW)
            def read():
                return os.read(fd, 4096) if select.select([fd], [], [], 0)[0] else b""
            yield read
        finally:
            try:
                termios.tcflush(fd, termios.TCIFLUSH)
            finally:
                termios.tcsetattr(fd, termios.TCSANOW, original)


def terminal_session(sock, read_keyboard, write_output):
    """雙向、有界、至多一筆未確認的 TX；沒有自動重連或重送。"""
    pending = bytearray()
    keyboard = bytearray()
    ready = False
    sent_at = None
    expected_bytes = None
    closing = False
    close_at = None
    newline = b"\r"
    opened_at = time.monotonic()
    while True:
        if not ready and time.monotonic() - opened_at > 5:
            raise TimeoutError("終端連線確認逾時，沒有送出鍵盤資料")
        if ready and not closing:
            keys = read_keyboard()
            if b"\x1d" in keys:
                # 離開不再送出同批其他按鍵，避免它們在本機 Shell 或設備上意外執行。
                closing = True
                keyboard.clear()
                close_at = time.monotonic()
                sock.sendall(encode({"op": "terminal_close"}))
            else:
                keys = keys.replace(b"\r", newline)
                if len(keyboard) + len(keys) > 4096:
                    raise ValueError("互動輸入佇列已滿；部分內容可能已送出，不會重送")
                keyboard.extend(keys)
        if keyboard and sent_at is None and not closing:
            data = bytes(keyboard)
            keyboard.clear()
            sock.sendall(encode({"op": "terminal_write", "data_b64": b64(data)}))
            expected_bytes, sent_at = len(data), time.monotonic()
        if sent_at is not None and time.monotonic() - sent_at > 5:
            raise TimeoutError("互動 TX 回覆逾時，結果不明，不會重送")
        if closing and time.monotonic() - close_at > 2:
            raise TimeoutError("終端關閉回覆逾時；請用 status 確認，不會重送")
        if not select.select([sock], [], [], 0.02)[0]:
            continue
        data = sock.recv(65536)
        if not data:
            raise ValueError("互動連線中斷，Shell 結果不明，不會重連或重送")
        output = bytearray()
        try:
            for reply in iter_frames(pending, data):
                kind = reply.get("type")
                if kind == "terminal":
                    newline = {"cr": b"\r", "lf": b"\n", "crlf": b"\r\n"}[reply["newline"]]
                    ready = True
                    print("已進入 UART 終端：Ctrl+C 傳至設備，Ctrl+] 離開；互動內容不存入 Server Log。", file=sys.stderr, flush=True)
                elif kind == "rx":
                    output.extend(unb64(reply["data_b64"]))
                elif kind == "terminal_tx":
                    if expected_bytes is None or reply.get("accepted_bytes") != expected_bytes:
                        raise ValueError("互動 TX 回覆不符，結果不明，不會重送")
                    sent_at = expected_bytes = None
                elif kind == "terminal_closed":
                    if closing:
                        return 0
                elif kind == "error":
                    if not ready and reply.get("error") == "invalid_request":
                        raise ValueError("目前 Server 不支援 terminal，請重新啟動 Server；沒有送出鍵盤資料")
                    raise ValueError(reply.get("message", reply.get("error", "terminal failed")))
        finally:
            write_output(output)


class CompactMonitor:
    """只隱藏目前 Gateway 命令的 Echo／框架；輸出與 Prompt 保留原始 bytes。"""

    def __init__(self):
        self.phase = "idle"
        self.pending = bytearray()

    def start(self, command_id, started=False, prefix=b""):
        begin, end = markers(command_id)
        self.begins = (b"\n" + begin + b"\n", b"\n" + begin + b"\r\n")
        self.ends = (b"\n" + end, b"\r\n" + end)
        self.phase = "body" if started else "echo"
        self.pending[:] = prefix
        self.skip = len(prefix)

    @staticmethod
    def command_line(event):
        cwd = event.get("cwd")
        prefix = f"[cwd={cwd}] " if cwd is not None else ""
        return (prefix + event["command"] + "\r\n").encode("utf-8")

    def event(self, event):
        kind = event["type"]
        if kind == "monitor" and event.get("execution"):
            execution = event["execution"]
            self.start(execution["id"], execution["started"], unb64(execution.get("prefix_b64", "")))
            if execution.get("command"):
                return self.command_line(execution)
        elif kind == "tx" and event.get("id") and event.get("action") not in ("interrupt", "terminal"):
            self.start(event["id"])
            if event.get("command"):
                return self.command_line(event)
        elif kind == "completed":
            self.phase = "idle"
            self.pending.clear()
        elif kind == "terminal_opened":
            remaining = self.finish()
            self.phase = "idle"
            return remaining
        elif kind == "tx" and event.get("action") == "interrupt" and self.phase == "echo":
            # 若開始標記尚未出現，Ctrl+C 可能直接回到登入 Shell。
            self.phase = "idle"
            self.pending.clear()
        elif kind == "rx":
            return self.feed(unb64(event["data_b64"]))
        return b""

    def feed(self, data):
        output = bytearray()
        for byte in data:
            if self.phase == "idle":
                output.append(byte)
            elif self.phase == "echo":
                # Shell Echo 可能換行、折行或帶游標控制，開始標記之前整段隱藏。
                self.pending.append(byte)
                if any(self.pending.endswith(begin) for begin in self.begins):
                    self.phase = "body"
                    self.pending.clear()
                    self.skip = 0
                else:
                    del self.pending[:-max(map(len, self.begins))]
            else:
                self.pending.append(byte)
                while self.pending:
                    value = bytes(self.pending)
                    possible = False
                    for end in self.ends:
                        if end.startswith(value):
                            possible = True
                        elif value.startswith(end):
                            suffix = value[len(end):]
                            match = re.fullmatch(rb"([0-9]{1,3})\r?\n", suffix)
                            if match and int(match[1]) <= 255:
                                self.phase = "idle"
                                self.pending.clear()
                                self.skip = 0
                                break
                            possible |= re.fullmatch(rb"[0-9]{0,3}\r?", suffix) is not None
                    if not self.pending or possible:
                        break
                    byte = self.pending.pop(0)
                    if self.skip:
                        self.skip -= 1
                    else:
                        output.append(byte)
        return bytes(output)

    def finish(self):
        # 沒有完成標記時，尚未確定是框架的輸出不可靜默丟棄。
        remaining = bytes(self.pending[self.skip:]) if self.phase == "body" else b""
        self.pending.clear()
        return remaining


class FullMonitor:
    """逐步產生日誌行；只保留 SGR 顏色，不執行設備的游標／畫面控制。"""

    def __init__(self, output):
        self.output = output
        self.decoder = codecs.getincrementaldecoder("utf-8")("backslashreplace")
        self.line_open = False
        self.rx_time = None
        self.now = ""
        self.escape = ""
        self.cr = False
        self.parts = []

    def _text(self, text):
        if not self.line_open:
            self.parts.append(f"[{self.rx_time or self.now}] RX  ")
            self.line_open = True
        self.parts.append(text)
        if text == "\n":
            self.line_open = False
            self.rx_time = None

    def _escape_text(self):
        if re.fullmatch(r"\x1b\[[0-9;:]*m", self.escape):
            self._text(self.escape)
        else:
            self._text("<" + safe_text(self.escape).replace("\\x1b", "ESC")
                       .replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t") + ">")
        self.escape = ""

    def _rx(self, text):
        for char in text:
            if self.cr:
                self.cr = False
                if char != "\n":
                    self._text("<CR>")
            if self.escape:
                self.escape += char
                if self.escape.startswith("\x1b["):
                    ended = len(self.escape) >= 3 and "@" <= char <= "~"
                elif self.escape[1:2] in ("]", "P", "X", "^", "_"):
                    ended = self.escape.endswith("\x1b\\") or char == "\x07"
                else:
                    ended = len(self.escape) >= 2
                if ended or len(self.escape) >= 256:
                    self._escape_text()
            elif char == "\x1b":
                self.escape = char
            elif char == "\r":
                self.cr = True
            elif char in "\n\t" or char.isprintable():
                self._text(char)
            else:
                self._text("<BS>" if char == "\b" else f"<0x{ord(char):02x}>")

    def flush(self):
        if self.parts:
            self.output.write("".join(self.parts))
            self.output.flush()
            self.parts.clear()

    def event(self, event, *, flush=True):
        moment = datetime.fromisoformat(event["time"]) if event.get("time") else datetime.now().astimezone()
        self.now = moment.astimezone().strftime("%H:%M:%S.%f")[:-3]
        kind = event["type"]
        if kind == "rx":
            data = unb64(event["data_b64"])
            if data and not self.line_open and self.rx_time is None:
                self.rx_time = self.now
            self._rx(self.decoder.decode(data))
        else:
            if self.line_open:
                self.parts.append("\n")
                self.line_open = False
                self.rx_time = None
            if kind == "tx":
                if event.get("redacted"):
                    text = f"<互動輸入已隱去，{event.get('bytes', 0)} bytes>"
                else:
                    text = "Ctrl+C (0x03)" if event.get("action") == "interrupt" else safe_text(
                        unb64(event["data_b64"]).decode("utf-8", "backslashreplace")).replace("\r", "\\r").replace("\n", "\\n")
            else:
                text = " ".join(f"{key}={json.dumps(value, ensure_ascii=True)}"
                                for key, value in event.items() if key not in ("type", "time", "seq"))
            label = "DONE" if kind == "completed" else kind.upper()
            self.parts.append(f"[{self.now}] {label}  {text}\n")
        if flush:
            self.flush()

    def finish(self):
        self._rx(self.decoder.decode(b"", final=True))
        if self.escape:
            self._escape_text()
        if self.cr:
            self._text("<CR>")
            self.cr = False
        if self.line_open:
            self.parts.append("\n")
            self.line_open = False
        self.flush()


@contextmanager
def terminal_mode():
    """Windows Console 啟用原生 ANSI／VT；管線與 Linux 不改動。"""
    original = None
    if os.name == "nt" and sys.stdout.isatty():
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel.GetStdHandle.restype = wintypes.HANDLE
        kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        handle = kernel.GetStdHandle(-11)
        mode = wintypes.DWORD()
        if not kernel.GetConsoleMode(handle, ctypes.byref(mode)):
            raise ctypes.WinError(ctypes.get_last_error())
        # ENABLE_PROCESSED_OUTPUT | ENABLE_VIRTUAL_TERMINAL_PROCESSING
        if not kernel.SetConsoleMode(handle, mode.value | 0x0001 | 0x0004):
            raise ctypes.WinError(ctypes.get_last_error())
        original = mode.value
    try:
        yield
    finally:
        if original is not None:
            sys.stdout.flush()
            kernel.SetConsoleMode(handle, original)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    commands = parser.add_subparsers(dest="operation", required=True)
    execution = commands.add_parser("exec", help="執行單次非互動命令")
    execution.add_argument("command")
    execution.add_argument("--cwd", help="本次命令的 IPCam 工作目錄，不保存至下一次 exec")
    execution.add_argument("--timeout", type=float, default=10)
    execution.add_argument("--max-output", type=int, default=1024 * 1024)
    execution.add_argument("--json", action="store_true")
    monitor = commands.add_parser("monitor", help="唯讀即時 TX/RX")
    display = monitor.add_mutually_exclusive_group()
    display.add_argument("--full", action="store_true", help="時間戳記、TX/RX 與命令狀態日誌")
    display.add_argument("--json", action="store_true", help="原始 NDJSON 事件")
    commands.add_parser("status", help="唯讀狀態查詢，輸出 JSON")
    commands.add_parser("interrupt", help="經 UART 送出一次 Ctrl+C，輸出 JSON")
    terminal = commands.add_parser("terminal", help="互動 UART 終端：Ctrl+C 傳至設備，Ctrl+] 離開")
    terminal.add_argument("--shell-ready", action="store_true", help="明確確認已登入 Shell 且前景命令已結束，恢復 exec 與正常 Log；不進入終端")
    args = parser.parse_args(argv)
    request = {"op": args.operation}
    command_id = None
    try:
        if args.operation == "terminal":
            if args.shell_ready:
                request = {"op": "terminal_ready", "confirmed": True}
            elif not sys.stdin.isatty() or not sys.stdout.isatty():
                raise ValueError("terminal 需要互動終端，不能使用管線；只讀輸出請用 monitor")
        if args.operation == "exec":
            request.update(command=args.command, timeout=args.timeout, max_output=args.max_output)
            if args.cwd is not None:
                request["cwd"] = args.cwd
            validate_exec(request)
        with socket.create_connection((args.host, args.port), timeout=5) as sock:
            sock.settimeout(args.timeout + 7 if args.operation == "exec" else 5)
            with sock.makefile("rb") as stream:
                if args.operation == "exec" and args.cwd is not None:
                    # 舊 Server 會忽略不認識的 exec 欄位；先用唯讀握手，未確認前不送命令。
                    sock.sendall(encode({"op": "hello"}))
                    hello = read_message(stream)
                    if (hello.get("type") != "hello" or not isinstance(hello.get("features"), list)
                            or "cwd" not in hello["features"]):
                        error = {"type": "error", "error": "cwd_unsupported",
                                 "message": "目前連線的 Server 不支援 --cwd，請重新啟動 Server；命令尚未送出"}
                        if args.json:
                            show_json(error)
                        else:
                            print(error["message"], file=sys.stderr)
                        return 2
                sock.sendall(encode(request))
                if args.operation == "terminal" and not args.shell_ready:
                    with terminal_mode(), terminal_keyboard() as read_keyboard:
                        result = terminal_session(sock, read_keyboard, raw_writer())
                    print("\n已離開終端。確認設備已回到 Shell 且命令已結束後，可用 terminal --shell-ready 恢復 exec。", file=sys.stderr)
                    return result
                if args.operation in ("status", "interrupt", "terminal"):
                    reply = read_message(stream)
                    show_json(reply)
                    return 2 if reply["type"] == "error" or reply.get("state") == "unknown" else 0
                if args.operation == "monitor":
                    renderer = FullMonitor(sys.stdout) if args.full else None
                    compact = CompactMonitor() if not args.full and not args.json else None
                    # Windows Console 不直接寫半個 UTF-8 字元；管線仍保留 bytes。
                    if not args.json and os.name == "nt" and sys.stdout.isatty():
                        sys.stdout.reconfigure(newline="")
                    write_compact = raw_writer() if compact else None
                    with terminal_mode():
                        try:
                            for batch in monitor_batches(sock):
                                output = bytearray()
                                lines = []
                                error = None
                                try:
                                    for reply in batch:
                                        if args.json:
                                            lines.append(json.dumps(reply, ensure_ascii=True) + "\n")
                                        elif renderer:
                                            renderer.event(reply, flush=False)
                                        else:
                                            output.extend(compact.event(reply))
                                        if reply["type"] == "error":
                                            error = reply
                                            break
                                finally:
                                    if lines:
                                        sys.stdout.write("".join(lines))
                                        sys.stdout.flush()
                                    elif renderer:
                                        renderer.flush()
                                    elif compact:
                                        write_compact(output)
                                if error:
                                    if compact:
                                        print("\n" + json.dumps(error, ensure_ascii=True), file=sys.stderr, flush=True)
                                    return 2
                        finally:
                            if renderer:
                                renderer.finish()
                            elif compact:
                                write_compact(compact.finish())
                reply = read_message(stream)
                if reply["type"] == "accepted":
                    command_id = reply["id"]
                    reply = read_message(stream)
                if args.json:
                    show_json(reply)
                if reply["type"] == "error":
                    if not args.json:
                        print(json.dumps(reply, ensure_ascii=True), file=sys.stderr)
                    return 2
                if not args.json:
                    output = unb64(reply["output_b64"])
                    if sys.stdout.isatty():
                        with terminal_mode():
                            raw_writer()(output, final=True)
                    else:
                        print(safe_text(output.decode("utf-8", errors="backslashreplace")), end="", flush=True)
                if reply["state"] != "completed" or not reply["output_complete"]:
                    print(f"\n命令 {reply['id']}：state={reply['state']} reason={reply['reason']} "
                          f"output_truncated={reply['output_truncated']}；請用 status 查詢，不要重送。", file=sys.stderr)
                    return 2
                return 0 if reply["exit_code"] == 0 else 1
    except KeyboardInterrupt:
        print("\n已停止等待；不會中止或重送 UART 命令。" + (f" id={command_id}" if command_id else ""), file=sys.stderr)
        return 130
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        print(f"連線／協定失敗：{exc}。不會重送命令。" + (f" id={command_id}" if command_id else ""), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
