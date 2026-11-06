"""模擬 Windows 鍵盤及 POSIX 管線，不存取 Win11 實機。"""

from collections import deque
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from uart_gateway.console_input import ConsoleInput


class ConsoleTests(unittest.TestCase):
    def windows(self, keys):
        data = deque(keys)
        module = SimpleNamespace(kbhit=lambda: bool(data), getwch=data.popleft)
        with patch("importlib.import_module", return_value=module):
            console = ConsoleInput(echo=Mock(), windows=True)
        return console, data

    def test_windows_edit_extended_key_and_complete_command(self):
        console, data = self.windows("o 2\b1")
        self.assertIsNone(console.poll())
        console.echo.assert_called_once_with("指令輸入：o 1")
        data.extend("\xe0K\r")
        self.assertEqual(console.poll(), "o 1")
        self.assertEqual(console.buffer, "")
        self.assertIsNone(console.poll())

    def test_windows_oversize_is_bounded_and_eof_is_explicit(self):
        console, data = self.windows("x" * 1024 + "\r")
        for _ in range(8):
            self.assertIsNone(console.poll())
            self.assertLessEqual(len(console.buffer), 128)
        self.assertEqual(console.poll(), "輸入過長")
        data.append("\x1a")
        with self.assertRaises(EOFError):
            console.poll()

    def test_windows_ctrl_c_cancels_without_waiting_for_another_key(self):
        import asyncio
        console, _ = self.windows("\x03")
        with self.assertRaises(asyncio.CancelledError):
            console.poll()

    @unittest.skipUnless(os.name == "posix", "需要 POSIX 管線")
    def test_posix_partial_line_multiple_commands_and_eof_without_thread(self):
        read_fd, write_fd = os.pipe()
        try:
            console = ConsoleInput(echo=Mock(), stream=SimpleNamespace(fileno=lambda: read_fd), windows=False)
            self.assertIsNone(console.poll())
            os.write(write_fd, b"o ")
            self.assertIsNone(console.poll())
            os.write(write_fd, b"1\ny\nq\n")
            self.assertEqual([console.poll(), console.poll(), console.poll()], ["o 1", "y", "q"])
            console.echo.assert_not_called()
            os.close(write_fd)
            write_fd = -1
            with self.assertRaises(EOFError):
                console.poll()
        finally:
            os.close(read_fd)
            if write_fd >= 0:
                os.close(write_fd)
