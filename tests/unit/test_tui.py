"""窗格緩衝與 SDK Cursor；不需要 Windows 終端或實體 UART。"""

import importlib.util
import builtins
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from uart_gateway.client.rx import RXCursor, StreamContextChanged
from uart_gateway.client.transport import LocalDataLoss
from uart_gateway.protocol.codec import binary_encode

HAS_TUI = importlib.util.find_spec("textual") is not None and importlib.util.find_spec("pyte") is not None


class CursorTests(unittest.TestCase):
    def setUp(self):
        self.cursor = RXCursor("a", {"subscription_id": "s", "stream_epoch": "epoch", "binding_revision": "1", "start_offset": "0"})

    def event(self, payload=b"boot", **changes):
        row = {"channel_id": "a", "binding_revision": "1", "stream_epoch": "epoch", "event": "rx",
               "data": {"subscription_id": "s", "offset": "0", "next_offset": str(len(payload)),
                        "data_b64": binary_encode(payload), "delivery": "replay"}}
        row.update(changes)
        return row

    def test_offset_and_context_are_checked_before_payload_is_displayed(self):
        self.assertEqual(self.cursor.receive(self.event()), b"boot")
        with self.assertRaises(LocalDataLoss):
            self.cursor.receive(self.event())
        for changes in ({"channel_id": "b"}, {"stream_epoch": "new"}, {"binding_revision": "2"}):
            with self.assertRaises(StreamContextChanged):
                self.cursor.receive(self.event(**changes))

    def test_gap_stops_and_unrelated_subscription_does_not_advance_cursor(self):
        row = self.event()
        row["data"]["subscription_id"] = "other"
        self.assertIsNone(self.cursor.receive(row))
        self.assertEqual(self.cursor.next_offset, 0)
        with self.assertRaises(LocalDataLoss):
            self.cursor.receive(self.event(event="gap"))


class EntryTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_tty_is_rejected_before_ui_or_connection(self):
        from uart_gateway.tui import run_tui
        settings = Mock()
        with patch("sys.stdin.isatty", return_value=False):
            with self.assertRaisesRegex(ValueError, "互動式終端"):
                await run_tui(SimpleNamespace(local=False), settings=settings)
        settings.connect.assert_not_called()

    async def test_missing_optional_dependency_has_install_instruction(self):
        from uart_gateway.tui import run_tui
        original = builtins.__import__
        def importing(name, *args, **kwargs):
            if name == "tui_app":
                raise ModuleNotFoundError("可選依賴未安裝", name="textual")
            return original(name, *args, **kwargs)
        with patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True), \
                patch("builtins.__import__", side_effect=importing):
            with self.assertRaisesRegex(ValueError, r"server,tui"):
                await run_tui(SimpleNamespace(local=False), settings=Mock())

    async def test_workbench_remote_tui_uses_sdk_profile_endpoint_and_explicit_manage(self):
        from uart_gateway.cli import main
        for target, access in (("2", None), ("3", "manage")):
            with self.subTest(target=target), patch("sys.argv", ["uart", "menu"]), \
                    patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True), \
                    patch("builtins.input", side_effect=["t", target, "q"]), patch("sys.stderr", io.StringIO()), \
                    patch("uart_gateway.cli.client_connection", return_value=Mock()) as source, \
                    patch("uart_gateway.tui.run_tui", new_callable=AsyncMock, return_value=0) as tui:
                # main 自行管理事件迴圈，放到執行緒以保持真實 CLI 路由。
                import asyncio
                def invoke():
                    with self.assertRaises(SystemExit) as result:
                        main()
                    self.assertEqual(result.exception.code, 0)
                await asyncio.to_thread(invoke)
                args = source.call_args.args[0]
                self.assertEqual(args.access, access)
                self.assertFalse(args.port_explicit)
                self.assertIs(tui.call_args.kwargs["settings"], source.return_value)


@unittest.skipUnless(HAS_TUI, "需要 [tui] 可選依賴")
class PaneTerminalTests(unittest.TestCase):
    def setUp(self):
        from uart_gateway.tui_buffer import PaneTerminal
        self.terminal = PaneTerminal()

    def test_split_utf8_color_cr_backspace_and_newline_stay_in_one_screen(self):
        text = "開機".encode()
        self.terminal.feed(b"\x1b[31m" + text[:2])
        self.terminal.feed(text[2:] + b"\rOK\nabc\bX")
        screen = self.terminal.screen
        self.assertTrue(screen.display[0].startswith("OK"))
        self.assertTrue(screen.display[1].startswith("abX"))
        self.assertEqual(screen.buffer[0][0].fg, "red")

    def test_ascii_run_preserves_cells_and_history_at_wrap_boundary(self):
        import pyte

        from uart_gateway.text_renderer import TerminalRenderer

        self.terminal.resize(10, 2)
        reference = pyte.HistoryScreen(10, 2)
        reference.set_mode(pyte.modes.LNM)
        stream = pyte.Stream(reference)
        renderer = TerminalRenderer()
        for data in (b"\x1b[31m0123456789", b"AB\x1b[0m\rxy", b"\nwide-\xe4\xb8\xad\nnext"):
            self.terminal.feed(data)
            stream.feed(renderer.feed(data.decode()))
        self.assertEqual(self.terminal.lines(), [*reference.history.top,
                         *(reference.buffer.get(row, {}) for row in range(reference.lines))])
        self.assertEqual((self.terminal.screen.cursor.x, self.terminal.screen.cursor.y),
                         (reference.cursor.x, reference.cursor.y))

    def test_history_width_and_combining_glyphs_are_bounded(self):
        from uart_gateway.tui_buffer import HISTORY_LINES, MAX_COLUMNS, MAX_ROWS
        self.terminal.resize(10000, 10000)
        self.terminal.feed(b"line\n" * 1500)
        self.assertLessEqual(len(self.terminal.screen.history.top), HISTORY_LINES)
        self.assertLessEqual(self.terminal.screen.columns, MAX_COLUMNS)
        self.assertLessEqual(self.terminal.screen.lines, MAX_ROWS)
        self.terminal.feed(("a" + "\u0301" * 10000).encode())
        self.assertGreater(self.terminal.screen.omitted_marks, 0)
        self.assertTrue(all(len(cell.data) <= 16 for row in self.terminal.lines() for cell in row.values()))

    def test_unterminated_osc_clipboard_and_cursor_controls_cannot_escape_the_pane(self):
        self.terminal.feed(b"safe\x1b]52;c;" + b"x" * 50000)
        self.assertTrue(self.terminal.screen.display[0].startswith("safe"))
        self.assertFalse(self.terminal.filter.parameters)
        self.terminal.feed(b"\x07\x1b[2Jafter")
        self.assertTrue(self.terminal.screen.display[0].startswith("safeafter"))
        self.assertNotIn("\x1b", "".join(self.terminal.screen.display))

    def test_pause_snapshot_is_independent_and_clear_resets_epoch_text(self):
        self.terminal.feed(b"old")
        snapshot = self.terminal.lines(freeze=True)
        self.terminal.feed(b"\rnew")
        self.assertEqual(snapshot[0][0].data, "o")
        self.terminal.clear()
        self.terminal.feed(b"second")
        self.assertEqual(len(self.terminal.screen.history.top), 0)
        self.assertTrue(self.terminal.screen.display[0].startswith("second"))


@unittest.skipUnless(HAS_TUI, "需要 [tui] 可選依賴")
class LocalEntryTests(unittest.IsolatedAsyncioTestCase):
    async def test_device_selection_recording_and_preferences_reuse_existing_launcher(self):
        from uart_gateway.core.models import AdapterInfo
        from uart_gateway.serial_backend import AdapterDetails
        from uart_gateway.tui import run_tui
        from uart_gateway.tui_app import UARTApp
        from uart_gateway.workbench_state import load_choice
        import tomllib
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "gateway.toml"
            config.write_text(Path("examples/gateway.com3.toml").read_text())
            args = SimpleNamespace(local=True, config=config, record_dir=None, no_record=False,
                recordings_dir=root / "recordings", preferences_file=root / "preferences.json", startup_timeout=20)
            device = AdapterDetails(AdapterInfo(port="COM9", vid=0x10c4, pid=0xea60, serial_number="unit", location="1-2"), "模擬 UART")
            async def run_headless(app):
                await app.local_preview()
                listed, _ = await app.local_devices()
                self.assertEqual(listed, [device])
                await app.local_select(listed)
                selected = list(root.glob(".uart-tui-*.local.toml"))
                self.assertEqual(len(selected), 1)
                value = tomllib.loads(selected[0].read_text())
                self.assertNotIn("port", value["channels"][0]["adapter"])
                self.assertEqual(value["channels"][0]["adapter"]["serial_number"], "unit")
                self.assertEqual(value["auth"]["principals"][0]["permissions"], ["read"])
                await app.local_record()
                await app.local_preview()
                await app.local_launch(AsyncMock(), Mock())
                return 0
            with patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True), \
                    patch("uart_gateway.serial_backend.list_adapter_details", return_value=[device]), \
                    patch("uart_gateway.serial_backend.list_adapters", return_value=[device.adapter]), \
                    patch("uart_gateway.configured_launcher.list_adapters", return_value=[device.adapter]), \
                    patch("uart_gateway.tui.launch_config", new_callable=AsyncMock, return_value=0) as launch, \
                    patch.object(UARTApp, "run_async", run_headless):
                self.assertEqual(await run_tui(args), 0)
            saved = load_choice(args.preferences_file)
            self.assertEqual(saved.recording, "on")
            self.assertEqual(launch.call_args.kwargs["record_directory"], args.recordings_dir)
            self.assertIsNotNone(launch.call_args.kwargs["reporter"])
            self.assertNotIn("Token", saved.config.read_text())

    async def test_duplicate_serial_keeps_selected_com5_in_saved_config_and_preview(self):
        from uart_gateway.core.models import AdapterInfo
        from uart_gateway.serial_backend import AdapterDetails
        from uart_gateway.tui import run_tui
        from uart_gateway.tui_app import UARTApp
        from uart_gateway.workbench_state import load_choice
        import tomllib

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = SimpleNamespace(local=True, config=Path("examples/gateway.com3.toml"), record_dir=None,
                no_record=False, recordings_dir=root / "recordings", preferences_file=root / "preferences.json",
                startup_timeout=20)
            devices = [AdapterDetails(AdapterInfo(port=port, vid=0x0403, pid=0x6001, serial_number="6"), port)
                       for port in ("COM5", "COM6")]

            async def run_headless(app):
                await app.local_select([devices[0]])
                self.assertIn("COM5", await app.local_preview())
                await app.local_launch(AsyncMock(), Mock())
                return 0

            with patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True), \
                    patch("uart_gateway.serial_backend.list_adapters", return_value=[d.adapter for d in devices]), \
                    patch("uart_gateway.tui.launch_config", new_callable=AsyncMock, return_value=0), \
                    patch.object(UARTApp, "run_async", run_headless):
                self.assertEqual(await run_tui(args), 0)
            saved = load_choice(args.preferences_file)
            config = tomllib.loads(saved.config.read_text(encoding="utf-8"))
            self.assertEqual(config["channels"][0]["adapter"]["port"], "COM5")
            self.assertEqual(config["channels"][0]["adapter"]["serial_number"], "6")

    async def test_disappeared_selection_does_not_switch_to_duplicate(self):
        from uart_gateway.core.models import AdapterInfo
        from uart_gateway.serial_backend import AdapterDetails
        from uart_gateway.tui import run_tui
        from uart_gateway.tui_app import UARTApp

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = SimpleNamespace(local=True, config=Path("examples/gateway.com3.toml"), record_dir=None,
                no_record=False, recordings_dir=root / "recordings", preferences_file=root / "preferences.json",
                startup_timeout=20)
            selected = AdapterDetails(AdapterInfo(port="COM5", vid=0x0403, pid=0x6001, serial_number="6"), "COM5")
            remaining = AdapterInfo(port="COM6", vid=0x0403, pid=0x6001, serial_number="6")

            async def run_headless(app):
                with self.assertRaises(OSError):
                    await app.local_select([selected])
                return 0

            with patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True), \
                    patch("uart_gateway.serial_backend.list_adapters", return_value=[remaining]), \
                    patch.object(UARTApp, "run_async", run_headless):
                self.assertEqual(await run_tui(args), 0)
            self.assertEqual(list(root.glob(".uart-tui-*.local.toml")), [])
            self.assertFalse(args.preferences_file.exists())
