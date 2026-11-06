"""錄製回看只接觸暫存檔與終端替身，不使用實體 UART。"""

import asyncio
import io
import os
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

from uart_gateway.recording_play import PlaybackCancelled, PlaybackOutputError, play_capture, playback
from uart_gateway.text_renderer import RESET
from tests.fakes import eventually
from tests.unit import test_recording_text as fixtures


class PlaybackTests(unittest.TestCase):
    setUp = fixtures.ExportTests.setUp
    segment = fixtures.ExportTests.segment
    rx = fixtures.ExportTests.rx

    def play(self, terminal=True, stdout=None, stop=None):
        stdout = stdout if stdout is not None else io.StringIO()
        stderr = io.StringIO()
        result = play_capture(self.capture, terminal=terminal, stop=stop or threading.Event(), stdout=stdout, stderr=stderr)
        return result, stdout.getvalue(), stderr.getvalue()

    def test_color_and_utf8_span_segments_and_dangerous_sequences_are_filtered(self):
        glyph = "開".encode()
        self.segment(1, [self.rx(b"\x1b[32m" + glyph[:2])], final=False)
        self.segment(2, [self.rx(glyph[2:] + b"\r\n|\b/\x1b[2J\x1b]52;c;secret\x07", offset=7, seq=1)])
        before = [path.read_bytes() for path in sorted(self.capture.glob("*.jsonl"))]
        result, out, err = self.play()
        self.assertEqual(result["integrity"], "no_known_gap")
        self.assertIn("\x1b[32m開\r\n|\b/", out)
        self.assertNotIn("\x1b[2J", out)
        self.assertNotIn("secret", out)
        self.assertIn("離線回看完成", err)
        self.assertIn("並非即時 UART", err)
        self.assertTrue(out.endswith(RESET))
        self.assertEqual(before, [path.read_bytes() for path in sorted(self.capture.glob("*.jsonl"))])

    def test_plain_mode_escapes_control_bytes_and_invalid_utf8(self):
        self.segment(1, [self.rx(b"\x1b[31mboot\r\n\xff\x00")])
        _, out, _ = self.play(terminal=False)
        self.assertNotIn("\x1b", out)
        self.assertIn("\\x1b[31mboot\\r\n\\xff\\x00", out)

    def test_gap_resets_utf8_and_ansi_state(self):
        self.segment(1, [self.rx(b"\xe9\x96"), self.rx(b"\x8b\x1b[3", offset=4, seq=1),
                         self.rx(b"1mX", offset=20, seq=2)])
        result, out, err = self.play()
        self.assertEqual(result["integrity"], "incomplete")
        self.assertNotIn("開", out)
        self.assertNotIn("\x1b[31m", out)
        self.assertIn("\\xe9\\x96", out)
        self.assertIn("資料不完整", err)

    def test_epoch_and_device_loss_do_not_join_partial_character(self):
        self.segment(1, [self.rx(b"\xe9\x96"), self.rx(b"\x8b", epoch="new"),
            {"type": "channel_state", "channel": {"state": "lost", "stream_epoch": None}}])
        result, out, err = self.play()
        self.assertNotIn("開", out)
        self.assertEqual(result["integrity"], "unknown")
        self.assertIn("裝置遺失", err)

    def test_missing_footer_is_not_reported_as_clean(self):
        self.segment(1, [self.rx(b"recoverable")])
        file = self.capture / "000001.jsonl"
        raw = file.read_bytes()
        file.write_bytes(raw[:raw.rfind(b'{"type":"segment_end"')])
        result, out, err = self.play()
        self.assertEqual(result["integrity"], "unknown")
        self.assertIn("recoverable", out)
        self.assertIn("連續性未知", err)

    def test_stdout_failure_is_not_swallowed_or_retried(self):
        self.segment(1, [self.rx(b"boom")])
        class Broken(io.StringIO):
            attempts = 0
            def write(self, text):
                if "boom" in text:
                    self.attempts += 1
                    raise BrokenPipeError
                return super().write(text)
        output = Broken()
        with self.assertRaises(PlaybackOutputError):
            self.play(stdout=output)
        self.assertEqual(output.attempts, 1)

    def test_already_cancelled_scan_does_not_claim_completion(self):
        stop = threading.Event()
        stop.set()
        out, err = io.StringIO(), io.StringIO()
        with self.assertRaises(PlaybackCancelled):
            play_capture(self.capture, terminal=True, stop=stop, stdout=out, stderr=err)
        self.assertTrue(out.getvalue().endswith(RESET))
        self.assertIn("已中止", err.getvalue())
        self.assertNotIn("回看完成", err.getvalue())

    def test_real_cli_without_credentials_and_redirected_terminal_rejected(self):
        self.segment(1, [self.rx(b"boot\n")])
        env = os.environ.copy()
        env.pop("UART_GATEWAY_TOKEN", None)
        command = [sys.executable, "-m", "uart_gateway.cli", "recording-play", str(self.capture)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=5, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("boot", result.stdout)
        result = subprocess.run(command + ["--terminal"], capture_output=True, text=True, timeout=5, env=env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("互動式終端機", result.stderr)
        with (self.capture / "000001.jsonl").open("ab") as file:
            file.write(b"{unfinished")
        result = subprocess.run(command, capture_output=True, text=True, timeout=5, env=env)
        self.assertEqual(result.returncode, 2)

    @unittest.skipUnless(os.name == "posix", "需要 POSIX PTY；Win11 終端另行驗證")
    def test_real_cli_terminal_mode_on_pty(self):
        self.segment(1, [self.rx(b"\x1b[32mboot\r\n|\b/")])
        master, slave = os.openpty()
        process = None
        try:
            process = subprocess.Popen([sys.executable, "-m", "uart_gateway.cli", "recording-play",
                str(self.capture), "--terminal"], stdout=slave, stderr=subprocess.PIPE)
            _, err = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, err.decode())
            os.close(slave)
            slave = None
            chunks = []
            while True:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
            output = b"".join(chunks)
            self.assertIn(b"\x1b[32mboot", output)
            self.assertIn(b"|\b/", output)
            self.assertTrue(output.endswith(RESET.encode()))
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
            if slave is not None:
                os.close(slave)
            os.close(master)


class PlaybackCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_stops_worker_after_inflight_output_and_resets_color(self):
        # 借用同一組錄製檔 fixture，不繼承其測試方法以免重複執行。
        fixture = PlaybackTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.segment(1, [fixture.rx(b"block"), fixture.rx(b"must not appear", offset=5, seq=1)])
        entered, release = threading.Event(), threading.Event()
        class Console(io.StringIO):
            def isatty(self):
                return True
            def write(self, text):
                if "block" in text:
                    entered.set()
                    if not release.wait(3):
                        raise OSError("測試未釋放輸出")
                return super().write(text)
        out, err = Console(), io.StringIO()
        with patch("sys.stdout", out), patch("sys.stderr", err):
            task = asyncio.create_task(playback(fixture.capture, terminal=True))
            try:
                await eventually(entered.is_set)
                task.cancel()
                await asyncio.sleep(0)
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 2)
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
        self.assertNotIn("must not appear", out.getvalue())
        self.assertTrue(out.getvalue().endswith(RESET))
        self.assertIn("已中止", err.getvalue())
