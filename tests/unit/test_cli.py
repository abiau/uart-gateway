"""終端文字顯示不執行 UART 送來的控制序列。"""

import codecs
import unittest

from uart_gateway.cli import safe_text


class DisplayTests(unittest.TestCase):
    def test_control_sequences_are_visible_and_utf8_can_span_chunks(self):
        self.assertEqual(safe_text("boot\r\n\x1b[31m\x00\t"), "boot\\r\n\\x1b[31m\\x00\t")
        decoder = codecs.getincrementaldecoder("utf-8")(errors="backslashreplace")
        self.assertEqual(decoder.decode("開".encode("utf-8")[:1]), "")
        self.assertEqual(decoder.decode("開".encode("utf-8")[1:]), "開")
        self.assertEqual(decoder.decode(b"\xff", final=True), "\\xff")
