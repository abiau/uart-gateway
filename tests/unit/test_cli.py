"""終端文字顯示不執行 UART 送來的控制序列。"""

import asyncio
import codecs
import io
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from uart_gateway.cli import _WatchOutput, display_text, main, run, safe_text, watch_client
from uart_gateway.client.transport import LocalDataLoss
from uart_gateway.protocol.codec import binary_encode
from uart_gateway.text_renderer import RESET
from tests.fakes import eventually


class DisplayTests(unittest.TestCase):
    def test_terminal_and_safe_text_flags_are_mutually_exclusive(self):
        with patch("sys.argv", ["uart", "watch", "a", "--text", "--terminal"]), patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                main()
        self.assertEqual(caught.exception.code, 2)

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


class WatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_output_budget_counts_utf8_and_inflight_write(self):
        entered, release = asyncio.Event(), asyncio.Event()
        writes = []

        async def display(text):
            writes.append(text)
            entered.set()
            await release.wait()

        with patch("uart_gateway.cli._display", side_effect=display):
            sink = _WatchOutput(terminal=True, maximum=8)
            try:
                sink.put("開")
                await asyncio.wait_for(entered.wait(), 1)
                sink.put("機")
                with self.assertRaisesRegex(LocalDataLoss, "CLI 顯示佇列"):
                    sink.put("字")
                self.assertEqual(sink.used, 6)
            finally:
                release.set()
                await sink.close()
        self.assertEqual(writes, ["開", "機", RESET])
        self.assertEqual(sink.used, 0)

    async def test_output_item_limit_is_bounded(self):
        with patch("uart_gateway.cli._display", new=AsyncMock()) as display:
            sink = _WatchOutput(terminal=True)
            try:
                for _ in range(256):
                    sink.put("x")
                with self.assertRaises(LocalDataLoss):
                    sink.put("overflow")
            finally:
                await sink.close()
        self.assertEqual(display.await_count, 257)
        self.assertEqual(sink.used, 0)

    async def test_writer_failure_wakes_watch_without_new_uart_data(self):
        client = self.client()
        with patch("sys.stderr", io.StringIO()), \
                patch("uart_gateway.cli._display", side_effect=BrokenPipeError("終端輸出已關閉")):
            with self.assertRaisesRegex(BrokenPipeError, "終端輸出已關閉"):
                await asyncio.wait_for(watch_client(client, "a", duration=None,
                                                   text_mode=True, replay=False), 1)
        client.unsubscribe.assert_awaited_once()

    async def test_simultaneous_data_and_output_failure_preserves_data_error(self):
        client = self.client()
        failure = LocalDataLoss("原始接收失敗")
        client.next_event = AsyncMock(side_effect=failure)
        with patch("uart_gateway.cli._display", side_effect=BrokenPipeError("輸出也失敗")):
            with self.assertRaises(LocalDataLoss) as caught:
                await watch_client(client, "a", duration=None, text_mode=True, replay=False)
        self.assertIs(caught.exception, failure)
        self.assertIn("輸出也失敗", " ".join(failure.__notes__))
        client.unsubscribe.assert_awaited_once()

    async def test_terminal_requires_tty_before_connecting(self):
        with patch("sys.stdout.isatty", return_value=False), patch("uart_gateway.cli.Client.connect") as connect:
            with self.assertRaisesRegex(ValueError, "互動式終端機"):
                await run(SimpleNamespace(command="watch", terminal=True))
        connect.assert_not_called()

    def client(self, events=()):
        queue = asyncio.Queue()
        for event in events:
            queue.put_nowait(event)
        return SimpleNamespace(subscribe=AsyncMock(return_value={"subscription_id": "test"}),
                               next_event=queue.get, unsubscribe=AsyncMock())

    def rx(self, payload, delivery="live"):
        return {"event": "rx", "data": {"delivery": delivery, "data_b64": binary_encode(payload)}}

    async def test_cancel_during_flush_does_not_repeat_batch(self):
        client = self.client([self.rx(b"x" * 4096) for _ in range(4)])
        entered, release = asyncio.Event(), asyncio.Event()
        writes = []

        async def display(value):
            writes.append(value)
            entered.set()
            await release.wait()

        with patch("uart_gateway.cli.display_text", side_effect=display), patch("sys.stderr", io.StringIO()):
            task = asyncio.create_task(watch_client(client, "a", duration=None, text_mode=True, replay=False))
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual("".join(writes), "x" * 16384)
        client.unsubscribe.assert_awaited_once_with("test")

    async def test_cancel_waits_for_started_stdout_write(self):
        entered, release = threading.Event(), threading.Event()

        class Console(io.StringIO):
            def write(self, value):
                entered.set()
                if not release.wait(2):
                    raise RuntimeError("測試未釋放輸出執行緒")
                return super().write(value)

        console = Console()
        with patch("sys.stdout", console):
            task = asyncio.create_task(display_text("已開始輸出"))
            try:
                await eventually(entered.is_set)
                task.cancel()
                for _ in range(3):
                    await asyncio.sleep(0)
                finished_before_write = task.done()
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(finished_before_write)
        self.assertEqual(console.getvalue(), "已開始輸出")

    async def test_output_failure_still_unsubscribes_without_retry(self):
        client = self.client([self.rx(b"pending")])
        with patch("sys.stderr", io.StringIO()), \
                patch("uart_gateway.cli.display_text", side_effect=BrokenPipeError("輸出已關閉")) as display:
            with self.assertRaises(BrokenPipeError):
                await watch_client(client, "a", duration=0.1, text_mode=True, replay=False)
        client.unsubscribe.assert_awaited_once_with("test")
        self.assertEqual(display.await_count, 1)

    async def test_sdk_timeout_is_not_normal_watch_expiry(self):
        for events in ([], [self.rx(b"pending")]):
            client = self.client()
            client.next_event = AsyncMock(side_effect=events + [TimeoutError("SDK 心跳逾時")])
            with self.subTest(buffered=bool(events)), patch("sys.stderr", io.StringIO()), \
                    patch("sys.stdout", io.StringIO()):
                with self.assertRaisesRegex(TimeoutError, "SDK 心跳逾時"):
                    await watch_client(client, "a", duration=1, text_mode=True, replay=False)
            client.unsubscribe.assert_awaited_once()

    async def test_cleanup_failure_does_not_replace_data_loss(self):
        client = self.client()
        failure = LocalDataLoss("原始資料遺失")
        client.next_event = AsyncMock(side_effect=failure)
        client.unsubscribe.side_effect = ConnectionError("清理連線已失效")
        with patch("sys.stderr", io.StringIO()):
            with self.assertRaises(LocalDataLoss) as caught:
                await watch_client(client, "a", duration=1, text_mode=True, replay=False)
        self.assertIs(caught.exception, failure)
        self.assertIn("清理連線已失效", " ".join(failure.__notes__))

    async def test_partial_line_flushes_while_no_more_uart_data_arrives(self):
        client = self.client([self.rx(b"no newline")])
        with patch("sys.stderr", io.StringIO()), patch("uart_gateway.cli.display_text") as display:
            task = asyncio.create_task(watch_client(client, "a", duration=None, text_mode=True, replay=False))
            try:
                await eventually(lambda: display.await_count > 0)
                self.assertFalse(task.done())
                display.assert_awaited_once_with("no newline")
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        client.unsubscribe.assert_awaited_once()

    async def test_utf8_split_replay_live_controls_and_subscription_end(self):
        encoded = "開".encode()
        client = self.client([self.rx(encoded[:1], "replay"),
            self.rx(encoded[1:] + b"\r\n\x1b[31m", "replay"), self.rx(b"\xff", "live"),
            {"event": "subscription_state", "data": {"state": "ended"}}])
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            result = await watch_client(client, "a", duration=1, text_mode=True, replay=False)
        self.assertEqual(result, 2)
        self.assertEqual(stdout.getvalue(), "開\\r\n\\x1b[31m\\xff")
        self.assertLess(stderr.getvalue().index("歷史重播"), stderr.getvalue().index("即時接收"))
        client.unsubscribe.assert_awaited_once()

    async def test_duration_expiry_during_write_finishes_batch_once(self):
        entered, release = threading.Event(), threading.Event()

        class Console(io.StringIO):
            def write(self, value):
                entered.set()
                if not release.wait(2):
                    raise RuntimeError("測試未釋放輸出執行緒")
                return super().write(value)

        console = Console()
        client = self.client([self.rx(b"x" * 4096) for _ in range(4)])
        with patch("sys.stdout", console), patch("sys.stderr", io.StringIO()):
            task = asyncio.create_task(watch_client(client, "a", duration=0.1, text_mode=True, replay=False))
            try:
                await eventually(entered.is_set)
                await asyncio.sleep(0.15)
                finished_before_write = task.done()
            finally:
                release.set()
            result = await asyncio.wait_for(task, 1)
        self.assertFalse(finished_before_write)
        self.assertEqual(result, 0)
        self.assertEqual(console.getvalue(), "x" * 16384)
        client.unsubscribe.assert_awaited_once()

    async def test_terminal_mode_renders_color_controls_and_split_utf8(self):
        client = self.client([self.rx(b"\x1b[0;3", "replay"),
            self.rx(b"2m" + "開".encode()[:1], "replay"),
            self.rx("開".encode()[1:] + b"\r\nloading |\b/\b\r\n", "replay"),
            self.rx(b"\x1b]52;c;hidden\x07\x1b[6nend", "replay")])
        stdout = io.StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", io.StringIO()):
            result = await watch_client(client, "a", duration=0.1, text_mode=True, replay=False, terminal_mode=True)
        self.assertEqual(result, 0)
        self.assertEqual(stdout.getvalue().replace(RESET, ""), "\x1b[0;32m開\r\nloading |\b/\b\r\nend")
        self.assertTrue(stdout.getvalue().endswith(RESET))
        client.unsubscribe.assert_awaited_once()

    async def test_terminal_cancel_restores_color_and_unsubscribes(self):
        client = self.client([self.rx(b"\x1b[31mpending")])
        stdout = io.StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", io.StringIO()):
            task = asyncio.create_task(watch_client(client, "a", duration=None, text_mode=True,
                                                    replay=False, terminal_mode=True))
            try:
                await eventually(lambda: "pending" in stdout.getvalue())
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertTrue(stdout.getvalue().endswith(RESET))
        client.unsubscribe.assert_awaited_once()

    async def test_terminal_gap_does_not_join_partial_utf8_or_control_sequences(self):
        for prefix, suffix, expected in ((b"\x1b[3", b"1mplain", "1mplain"),
                                          (b"\xe9", b"\x96\x8b", r"\xe9\x96\x8b")):
            client = self.client([self.rx(prefix), {"event": "gap", "data": {}}, self.rx(suffix),
                                 {"event": "subscription_state", "data": {"state": "ended"}}])
            stdout = io.StringIO()
            with self.subTest(prefix=prefix), patch("sys.stdout", stdout), patch("sys.stderr", io.StringIO()):
                result = await watch_client(client, "a", duration=1, text_mode=True,
                                            replay=False, terminal_mode=True)
            self.assertEqual(result, 2)
            self.assertEqual(stdout.getvalue().replace(RESET, ""), expected)
