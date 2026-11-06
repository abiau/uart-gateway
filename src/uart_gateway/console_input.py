"""雙路畫面的有界、非阻塞主控台輸入，不建立等待輸入的執行緒。"""

import importlib
from collections.abc import Callable
import os
import select
import sys
from typing import TextIO


class ConsoleInput:
    def __init__(self, *, echo: Callable[[str], None], stream: TextIO | None = None,
                 windows: bool | None = None):
        self.stream = stream or sys.stdin
        self.windows = os.name == "nt" if windows is None else windows
        self.console = importlib.import_module("msvcrt") if self.windows else None
        self.echo = echo
        self.buffer = ""
        self.overflow = False
        self.extended = False

    def poll(self) -> str | None:
        changed = False
        for _ in range(128):
            if self.windows:
                if not self.console.kbhit():
                    break
                char = self.console.getwch()
                if self.extended:
                    self.extended = False
                    continue
                if char in ("\x00", "\xe0"):
                    self.extended = True
                    continue
                if char == "\x03":
                    import asyncio
                    raise asyncio.CancelledError
                if char in ("\x1a", "\x04"):
                    raise EOFError
            else:
                fd = self.stream.fileno()
                if not select.select([fd], [], [], 0)[0]:
                    break
                raw = os.read(fd, 1)
                if not raw:
                    raise EOFError
                # 指令只有 ASCII；其他 bytes 作無效輸入，不執行控制字元。
                char = raw.decode("ascii", errors="replace")
            if char in ("\r", "\n"):
                result = "輸入過長" if self.overflow else self.buffer
                self.buffer, self.overflow = "", False
                return result
            if char in ("\b", "\x7f"):
                self.buffer = self.buffer[:-1]
                changed = True
            elif char.isprintable():
                if len(self.buffer) < 128:
                    self.buffer += char
                else:
                    self.overflow = True
                changed = True
        if self.windows and changed:
            self.echo("指令輸入：" + self.buffer + ("（過長）" if self.overflow else ""))
        return None
