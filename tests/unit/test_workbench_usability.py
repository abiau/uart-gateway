"""工作台操作簡化：無秘密偏好、本次 Capture、編號與相對時間。"""

import asyncio
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch

from uart_gateway.local_launcher import LauncherCleanupError
from uart_gateway.local_profile import LocalChannel, save_profile
from uart_gateway.recording_navigation import NavigationCancelled, match_selection, output_path, time_input, timeline_capture
from uart_gateway.recording_search import search_capture
from uart_gateway.serial_backend import AdapterSelector
from uart_gateway.workbench import run_workbench
from uart_gateway.workbench_state import StartChoice, WorkbenchSession, choice_config, load_choice, save_choice
from tests.unit import test_recording_text as fixture_module
from tests.unit import test_workbench as menu_module


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        self.fixture = menu_module.WorkbenchTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.path = self.fixture.root / "preferences.json"

    def test_only_declared_nonsecret_fields_are_saved_and_reloaded(self):
        choice = StartChoice(Path("my config.toml"), "on", Path("selected.local.toml"))
        save_choice(self.path, choice)
        data = json.loads(self.path.read_text())
        self.assertEqual(set(data), {"version", "config", "recording", "profile"})
        loaded = load_choice(self.path)
        self.assertEqual(loaded.recording, "on")
        self.assertEqual(loaded.profile, choice.profile.absolute())
        self.assertEqual(loaded.config, choice.config.absolute())

    def test_invalid_or_unrelated_file_is_never_overwritten(self):
        for value in ("invalid", "x" * 8193, '{"version":1,"token":"secret"}', '[1]'):
            self.path.write_text(value)
            with self.assertRaises(ValueError):
                save_choice(self.path, StartChoice())
            self.assertEqual(self.path.read_text(), value)

    def test_saved_endpoint_profile_re_resolves_current_com_without_rewriting(self):
        from uart_gateway.core.models import AdapterInfo
        profile = self.fixture.root / "device.local.toml"
        save_profile(profile, (LocalChannel("one", "測試板", AdapterSelector(port="COM3", serial_number="same")),))
        before = profile.read_bytes()
        with patch("uart_gateway.local_profile.list_adapters", return_value=[AdapterInfo(port="COM7", serial_number="same")]):
            with choice_config(StartChoice(profile=profile)) as path:
                text = path.read_text()
                self.assertIn('port = "COM7"', text)
                self.assertNotIn('port = "COM3"', text)
                self.assertIn('permissions = ["read"]', text)
            self.assertFalse(path.exists())
        self.assertEqual(profile.read_bytes(), before)

    def test_start_is_explicit_and_recording_toggle_is_saved_after_enter(self):
        _, dispatch, _, _ = self.fixture.run_menu(["s", "r", "", "q"])
        self.assertEqual(dispatch.call_count, 1)
        args = dispatch.call_args.args[0]
        self.assertEqual(args[0], "start")
        self.assertIn("--record-dir", args)
        self.assertEqual(load_choice(self.path).recording, "on")
        # 下一次進入工作台只讀偏好，取消不能自動啟動。
        _, dispatch, messages, _ = self.fixture.run_menu(["s", "q", "q"])
        dispatch.assert_not_called()
        self.assertIn("錄製：開啟", "\n".join(messages))

    def test_main_lists_one_start_entry_and_advanced_tools_remain_available(self):
        _, dispatch, messages, _ = self.fixture.run_menu(["a", "1", "q"])
        self.assertEqual(dispatch.call_args.args[0], ["ports"])
        self.assertIn("S. 開始監看", "\n".join(messages))

    def test_interactive_device_choice_is_saved_without_requesting_profile_filename(self):
        from uart_gateway.core.models import AdapterInfo
        channels = (LocalChannel("one", "測試板", AdapterSelector(port="COM3", serial_number="same")),)
        with patch("uart_gateway.local_ports.choose_channels", return_value=channels), \
                patch("uart_gateway.local_profile.list_adapters", return_value=[AdapterInfo(port="COM7", serial_number="same")]):
            _, dispatch, messages, _ = self.fixture.run_menu(["s", "d", "4", "1", "r", "", "q"])
        choice = load_choice(self.path)
        self.assertIsNotNone(choice.profile)
        self.assertTrue(choice.profile.exists())
        self.assertIn("COM7", "\n".join(messages))
        argv = dispatch.call_args.args[0]
        self.assertIn("--record-dir", argv)
        self.assertFalse(Path(argv[argv.index("--config") + 1]).exists())

    def test_fatal_cleanup_in_new_start_never_returns_to_another_start(self):
        dispatch = Mock(side_effect=LauncherCleanupError("測試清理不明"))
        answers = iter(["s", "", "s"])
        with patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True):
            with self.assertRaises(LauncherCleanupError):
                run_workbench(dispatch, recordings=self.fixture.root, read=lambda: next(answers), report=lambda _: None,
                              preferences_file=self.path)
        dispatch.assert_called_once()
        self.assertEqual(next(answers), "s")

    def test_local_receipt_not_newest_directory_controls_last_capture(self):
        source = self.fixture.capture("capture-owned")
        self.fixture.capture("capture-unrelated-newer")
        session = WorkbenchSession(capture_channels=(source,))
        reader = iter(["l", "i", "q", "q"])
        dispatch = Mock(return_value=0)
        with patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True):
            run_workbench(dispatch, recordings=self.fixture.root, session=session,
                          read=lambda: next(reader), report=lambda _: None, preferences_file=self.path)
        self.assertEqual(dispatch.call_args.args[0], ["recording-info", str(source)])

    def test_no_receipt_and_cancelled_dual_choice_do_not_open_any_file(self):
        _, dispatch, messages, _ = self.fixture.run_menu(["l", "q"])
        dispatch.assert_not_called()
        self.assertIn("不會猜測", "\n".join(messages))
        self.fixture.capture("capture-same", "ch_uart_1")
        self.fixture.capture("capture-same", "ch_uart_2")
        _, dispatch, messages, _ = self.fixture.run_menu(["5", "1", "q", "q"])
        dispatch.assert_not_called()
        self.assertIn("2 條通道", "\n".join(messages))


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture_module.ExportTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_relative_minutes_epoch_times_and_integrity_are_observed_together(self):
        f = self.f
        f.segment(1, [f.rx(b"first"), f.rx(b"second", epoch="new")])
        path = f.capture / "000001.jsonl"
        with path.open("ab") as stream:
            stream.write(b"bad")
        result = timeline_capture(f.capture)
        self.assertEqual(len(result["epoch_times"]), 2)
        self.assertNotEqual(result["integrity"], "no_known_gap")
        self.assertEqual(time_input("1.5", result["started_at"]), "2026-11-06T01:03:33+00:00")
        self.assertEqual(time_input("2026-11-06T09:00:00+08:00", None), "2026-11-06T01:00:00+00:00")
        for value, start in (("-1", result["started_at"]), ("9999999", result["started_at"]), ("5", None)):
            with self.assertRaises(ValueError):
                time_input(value, start)

    def test_cross_chunk_match_window_includes_first_and_last_chunk_times(self):
        f = self.f
        first, last = f.rx(b"boo"), f.rx(b"t", offset=3, seq=1)
        first["timestamp_utc"] = "2026-11-06T01:00:00Z"
        last["timestamp_utc"] = "2026-11-06T01:02:00Z"
        f.segment(1, [first, last])
        match = search_capture(f.capture, "boot")["search"]["matches"][0]
        window = match_selection(match)
        self.assertEqual(window.epoch, "epoch")
        self.assertEqual(window.from_time, "2026-11-06T00:59:30+00:00")
        self.assertEqual(window.to_time, "2026-11-06T01:02:30+00:00")
        self.assertTrue(window.accepts(first) and window.accepts(last))

    def test_bad_offset_search_is_still_recoverable_with_warning(self):
        row = self.f.rx(b"boot")
        row["next_offset"] = "1"
        self.f.segment(1, [row])
        result = search_capture(self.f.capture, "boot")
        self.assertEqual(result["integrity"], "incomplete")
        self.assertEqual(len(result["search"]["matches"]), 1)

    def test_invalid_match_time_and_cancelled_timeline_do_not_guess(self):
        with self.assertRaises(ValueError):
            match_selection({"epoch": "a", "timestamp_utc": "未提供"})
        stop = threading.Event()
        stop.set()
        self.f.segment(1, [self.f.rx(b"test")])
        with self.assertRaises(NavigationCancelled):
            timeline_capture(self.f.capture, stop=stop)
        self.f.segment(1, [dict(self.f.rx(b"test"), timestamp_utc=None)])
        path = self.f.capture / "000001.jsonl"
        lines = path.read_bytes().splitlines()
        footer = json.loads(lines[-1])
        footer["timestamp_utc"] = "2026-11-06T10:00:00Z"
        path.write_bytes(b"\n".join(lines[:-1]) + b"\n" + json.dumps(footer).encode() + b"\n")
        self.assertIsNone(timeline_capture(self.f.capture)["started_at"])

    def test_export_names_are_distinct_and_outside_source_channel(self):
        a, b = output_path(self.f.root), output_path(self.f.root)
        self.assertNotEqual(a, b)
        self.assertEqual(a.parent, self.f.root / "exports")
        self.assertFalse(a.exists())


class NavigationCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_waits_for_metadata_worker_before_returning(self):
        from uart_gateway.recording_navigation import timeline
        started, finished = threading.Event(), threading.Event()

        def inspect(directory, *, on_record):
            started.set()
            try:
                while True:
                    on_record(None)
                    threading.Event().wait(0.001)
            finally:
                finished.set()

        with patch("uart_gateway.recording_navigation.inspect_capture", inspect):
            task = asyncio.create_task(timeline(Path("unused")))
            async with asyncio.timeout(3):
                while not started.is_set():
                    await asyncio.sleep(0.001)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertTrue(finished.is_set())
