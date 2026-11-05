"""終端模式只允許基本排版及顏色，跨區塊保持有限狀態。"""

import unittest

from uart_gateway.text_renderer import RESET, TerminalRenderer


class TerminalRendererTests(unittest.TestCase):
    def test_boot_spinner_and_color_are_rendered(self):
        source = "開機\r\nLoading... |\b/\b-\b\\\b\r\n\x1b[0;32mZF MCU is ready.\x1b[0m\r\n"
        self.assertEqual(TerminalRenderer().feed(source), source)

    def test_every_split_and_single_character_chunks_preserve_allowed_sequences(self):
        source = "boot\r\n\x1b[0;36m時間\x1b[m\r\n|\b/\b"
        for split in range(len(source) + 1):
            renderer = TerminalRenderer()
            with self.subTest(split=split):
                self.assertEqual(renderer.feed(source[:split]) + renderer.feed(source[split:]), source)
                self.assertEqual(renderer.finish(), RESET)
        renderer = TerminalRenderer()
        self.assertEqual("".join(renderer.feed(char) for char in source), source)

    def test_queries_clear_screen_clipboard_titles_and_control_strings_are_filtered(self):
        controls = ["\x1b[6n", "\x1b[2J", "\x1b[H", "\x1b[?2004h", "\x1bc", "\x1b(0",
                    "\x1b]52;c;hidden\x07", "\x1b]0;title\x1b\\", "\x1bPpayload\x1b\\",
                    "\x1b_\x1b[31mhidden\x1b\\", "\x1b^hidden\x1b\\", "\x1bXhidden\x1b\\"]
        for control in controls:
            renderer = TerminalRenderer()
            with self.subTest(control=repr(control)):
                self.assertEqual("".join(renderer.feed(char) for char in "before" + control + "after"), "beforeafter")

    def test_c1_and_other_control_characters_are_not_forwarded(self):
        renderer = TerminalRenderer()
        self.assertEqual(renderer.feed("A\x9b31mB\x9d52;c;hidden\x9cC\x90hidden\x9cD\x00\x07\x7f\x85"), "ABCD")

    def test_extended_colors_are_validated_and_hidden_text_is_rejected(self):
        allowed = ["0", "1;31", "22;39;49", "97;104", "38;5;255", "48;2;0;128;255"]
        rejected = ["8", "5", "38;5;256", "48;2;1;2", "38;3;1", "38:2:1:2:3", "٣١", "-1"]
        for parameters in allowed:
            sequence = "\x1b[" + parameters + "m"
            with self.subTest(parameters=parameters):
                self.assertEqual(TerminalRenderer().feed(sequence + "text"), sequence + "text")
        for parameters in rejected:
            with self.subTest(parameters=parameters):
                self.assertEqual(TerminalRenderer().feed("\x1b[" + parameters + "mtext"), "text")

    def test_long_and_unfinished_sequences_do_not_grow_or_escape_to_terminal(self):
        renderer = TerminalRenderer()
        self.assertEqual(renderer.feed("\x1b[" + "0;" * 1000), "")
        self.assertLessEqual(len(renderer.parameters), 128)
        self.assertEqual(renderer.feed("mvisible"), "visible")
        self.assertEqual(renderer.feed("\x1b]52;c;"), "")
        for _ in range(100):
            self.assertEqual(renderer.feed("hidden" * 1000), "")
        self.assertEqual(renderer.feed("\x1b"), "")
        self.assertEqual(renderer.feed("\\visible"), "visible")
        self.assertEqual(renderer.feed("\x1b[3"), "")
        self.assertEqual(renderer.finish(), RESET)
        self.assertEqual(renderer.feed("1mplain"), "1mplain")

    def test_literal_escape_notation_is_not_decoded(self):
        text = r"literal \x1b[31m and \r and \x08"
        self.assertEqual(TerminalRenderer().feed(text), text)
