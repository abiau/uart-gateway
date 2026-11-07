"""Shell 包裝／完成標記與輸出界限；只在測試子 Shell 執行固定字串。"""

import os
import subprocess
import unittest

from uart_gateway.client.shell_marker import ShellMarkers, markers, shell_payload


class ShellMarkerTests(unittest.TestCase):
    execution_id = "a" * 32

    def framed(self, body, code=0, crlf=False):
        begin, end = markers(self.execution_id)
        nl = b"\r\n" if crlf else b"\n"
        return nl + begin + nl + body + nl + end + str(code).encode() + nl

    def test_split_echo_wrong_nonce_and_binary_body_are_not_confused(self):
        payload = shell_payload("printf '%s' 'q\\n'", self.execution_id, "lf")
        begin, end = markers(self.execution_id)
        self.assertNotIn(begin, payload)
        self.assertNotIn(end, payload)
        body = "開機".encode() + b"\x00\xff\r\n"
        wire = payload + b"\n__UART_BEGIN_" + b"b" * 32 + b"__\n" + self.framed(body, 7, crlf=True) + b"prompt# "
        parser = ShellMarkers(self.execution_id)
        for start in range(0, len(wire), 3):
            parser.feed(wire[start:start+3])
        self.assertTrue(parser.done)
        self.assertEqual(parser.exit_code, 7)
        self.assertEqual(parser.output, body)
        self.assertFalse(parser.truncated)

    def test_huge_line_and_terminator_are_bounded_and_counted_correctly(self):
        body = b"x" * 50000
        parser = ShellMarkers(self.execution_id, 128)
        parser.feed(self.framed(body))
        self.assertEqual(parser.observed_bytes, len(body))
        self.assertEqual(len(parser.output), 128)
        self.assertLessEqual(len(parser.pending), 64)
        self.assertTrue(parser.done and parser.truncated)
        exact = ShellMarkers(self.execution_id, 128)
        exact.feed(self.framed(b"x" * 128))
        self.assertFalse(exact.truncated)
        self.assertEqual(exact.observed_bytes, 128)

    def test_echo_or_bad_end_without_a_valid_start_never_completes(self):
        parser = ShellMarkers(self.execution_id)
        parser.feed(shell_payload("true", self.execution_id, "lf"))
        self.assertFalse(parser.started)
        parser.feed(b"\n" + markers(self.execution_id)[1] + b"0\n")
        self.assertFalse(parser.done)
        parser.feed(self.framed(b"body", 999))
        self.assertFalse(parser.done)

    def test_bad_controls_empty_large_commands_and_invalid_limits_are_rejected(self):
        for command in ("", "  ", "uname\n-a", "\x1b[0m", "a" * 4096, "\udcff"):
            with self.assertRaises(ValueError):
                shell_payload(command, self.execution_id, "lf")
        for maximum in (0, -1, 1048577, True):
            with self.assertRaises(ValueError):
                ShellMarkers(self.execution_id, maximum)
        with self.assertRaises(ValueError):
            shell_payload("true", self.execution_id, "none")

    @unittest.skipUnless(os.name == "posix", "需要測試用 POSIX 子 Shell")
    def test_real_shell_quotes_exit_and_dev_null_stdin(self):
        command = "printf '%s' \"A'B \\\"C\\\"\"; printf '錯誤' >&2; exit 7"
        payload = shell_payload(command, self.execution_id, "lf")
        result = subprocess.run(["/bin/sh", "-c", payload.decode()], input=b"unread", capture_output=True, timeout=3)
        parser = ShellMarkers(self.execution_id)
        parser.feed(result.stdout)
        self.assertTrue(parser.done)
        self.assertEqual(parser.exit_code, 7)
        self.assertIn("錯誤".encode(), parser.output)
        self.assertIn(b"A'B", parser.output)
        no_input = shell_payload("if read value; then exit 9; else printf EOF; fi", self.execution_id, "lf")
        result = subprocess.run(["/bin/sh", "-c", no_input.decode()], input=b"unread\n", capture_output=True, timeout=3)
        parser = ShellMarkers(self.execution_id)
        parser.feed(result.stdout)
        self.assertEqual((parser.output, parser.exit_code), (b"EOF", 0))
