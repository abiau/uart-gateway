"""獨立窗格終端模型；只解讀既有允許字元，歷史／寬度都有上限。"""

import codecs
import unicodedata

import pyte

from .text_renderer import TerminalRenderer

HISTORY_LINES = 300
MAX_COLUMNS = 240
MAX_ROWS = 80


class BoundedScreen(pyte.HistoryScreen):
    def __init__(self, *args, **kwargs):
        self.omitted_marks = 0
        super().__init__(*args, **kwargs)

    def draw(self, data: str) -> None:
        # pyte 可把無限個組合符號附在一個字元；限制每個格子的字串長度。
        for char in data:
            if unicodedata.combining(char):
                x, y = self.cursor.x - 1, self.cursor.y
                if x < 0:
                    x, y = self.columns - 1, y - 1
                previous = self.buffer.get(y, {}).get(x)
                if previous is not None and len(previous.data) >= 16:
                    self.omitted_marks += 1
                    continue
            super().draw(char)


class PaneTerminal:
    def __init__(self):
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="backslashreplace")
        self.filter = TerminalRenderer()
        self.screen = BoundedScreen(100, 20, history=HISTORY_LINES)
        self.screen.set_mode(pyte.modes.LNM)
        self.stream = pyte.Stream(self.screen)
        self.version = 0
        self.generation = 0

    def feed(self, data: bytes) -> None:
        self.stream.feed(self.filter.feed(self.decoder.decode(data)))
        self.version += 1

    def finish(self) -> None:
        self.stream.feed(self.filter.feed(self.decoder.decode(b"", final=True)))
        self.decoder.reset()
        self.filter.finish()
        self.version += 1

    def clear(self) -> None:
        self.finish()
        self.screen.reset()
        self.screen.set_mode(pyte.modes.LNM)
        self.screen.omitted_marks = 0
        self.generation += 1
        self.version += 1

    def resize(self, columns: int, rows: int) -> None:
        columns, rows = max(10, min(MAX_COLUMNS, columns)), max(2, min(MAX_ROWS, rows))
        if (columns, rows) != (self.screen.columns, self.screen.lines):
            self.screen.resize(lines=rows, columns=columns)
            self.version += 1

    def lines(self, *, freeze: bool = False) -> list[dict]:
        lines = [*self.screen.history.top, *(self.screen.buffer.get(row, {}) for row in range(self.screen.lines))]
        return [dict(row) for row in lines] if freeze else lines
