"""終端文字顯示不執行 UART 送來的控制序列。"""

import codecs
import unittest
from unittest.mock import AsyncMock, patch

from uart_gateway.cli import main, safe_text


class DisplayTests(unittest.TestCase):
    def test_control_sequences_are_visible_and_utf8_can_span_chunks(self):
        self.assertEqual(safe_text("boot\r\n\x1b[31m\x00\t"), "boot\\r\n\\x1b[31m\\x00\t")
        decoder = codecs.getincrementaldecoder("utf-8")(errors="backslashreplace")
        self.assertEqual(decoder.decode("開".encode("utf-8")[:1]), "")
        self.assertEqual(decoder.decode("開".encode("utf-8")[1:]), "開")
        self.assertEqual(decoder.decode(b"\xff", final=True), "\\xff")

    def test_double_click_error_pause_does_not_hide_failure_exit(self):
        with patch("sys.argv", ["uart", "local", "--port", "COM3", "--pause-on-error"]), \
                patch("uart_gateway.cli.run", new=AsyncMock(side_effect=ValueError("模擬失敗"))), \
                patch("sys.stdin.isatty", return_value=True), patch("builtins.input") as prompt, \
                patch("sys.stderr"):
            with self.assertRaises(SystemExit) as error:
                main()
        self.assertEqual(error.exception.code, 1)
        prompt.assert_called_once_with("按 Enter 關閉此視窗。")
