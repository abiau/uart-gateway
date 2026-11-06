"""只讀工作台的選單、命令路由與不可重啟的清理失敗。"""

import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from uart_gateway.cli import main
from uart_gateway.local_launcher import LauncherCleanupError, LauncherError
from uart_gateway.workbench import capture_paths, run_workbench


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_menu(self, answers, dispatch=None):
        dispatch = dispatch or Mock(return_value=0)
        reader = Mock(side_effect=answers)
        messages = []
        with patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True):
            result = run_workbench(dispatch, recordings=self.root, read=reader, report=messages.append)
        return result, dispatch, messages, reader

    def capture(self, name="capture-one", channel="ch_uart_1"):
        path = self.root / name / channel
        path.mkdir(parents=True)
        (path / "000001.jsonl").write_text("fixture")
        return path

    def test_cancel_invalid_and_eof_do_not_start_commands(self):
        for answers in (["q"], [""], ["bad", "q"], [EOFError]):
            result, dispatch, _, _ = self.run_menu(answers)
            self.assertEqual(result, 0)
            dispatch.assert_not_called()
        _, _, messages, _ = self.run_menu([EOFError])
        self.assertIn("EOF", messages[-1])

    def test_workbench_requires_interactive_input_and_output(self):
        for stdin, stdout in ((False, True), (True, False)):
            with patch("sys.stdin.isatty", return_value=stdin), patch("sys.stdout.isatty", return_value=stdout):
                with self.assertRaisesRegex(ValueError, "終端機"):
                    run_workbench(Mock(), recordings=self.root)

    def test_monitor_and_record_routes_are_explicit_readonly_and_optional_profile_save(self):
        result, dispatch, _, _ = self.run_menu(["2", "", "", "3", "2", "bench.local.toml", "q"])
        self.assertEqual(result, 0)
        first, second = [call.args[0] for call in dispatch.call_args_list]
        self.assertEqual(first, ["local", "--read-only", "--watch", "--terminal", "--select", "--count", "1"])
        self.assertIn("--save-profile", second)
        self.assertEqual(second[-2:], ["--record-dir", str(self.root)])
        self.assertEqual(second[second.index("--count") + 1], "2")

    def test_profile_path_and_recording_option_do_not_become_shell_arguments(self):
        filename = self.root / "profile $(literal) space.local.toml"
        _, dispatch, _, _ = self.run_menu(["4", f'"{filename}"', "y", "q"])
        argv = dispatch.call_args.args[0]
        self.assertEqual(argv[argv.index("--profile") + 1], str(filename))
        self.assertNotIn("--select", argv)
        self.assertEqual(argv[-2:], ["--record-dir", str(self.root)])

    def test_bad_operation_returns_to_menu_and_can_list_ports(self):
        dispatch = Mock(side_effect=[LauncherError("端點占用"), 0])
        result, _, messages, _ = self.run_menu(["2", "", "", "1", "q"], dispatch)
        self.assertEqual(result, 0)
        self.assertIn("端點占用", "\n".join(messages))
        self.assertEqual(dispatch.call_args.args[0], ["ports"])

    def test_fatal_cleanup_stops_workbench_without_next_selection(self):
        dispatch = Mock(side_effect=LauncherCleanupError("子程序尚未結束"))
        answers = iter(["2", "", "", "1", "q"])
        with patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True):
            with self.assertRaises(LauncherCleanupError):
                run_workbench(dispatch, recordings=self.root, read=lambda: next(answers), report=lambda _: None)
        dispatch.assert_called_once()
        self.assertEqual(next(answers), "1")

    def test_ctrl_c_during_operation_returns_to_menu_but_prompt_ctrl_c_exits(self):
        result, dispatch, messages, _ = self.run_menu(["2", "", "", "q"], Mock(side_effect=KeyboardInterrupt))
        self.assertEqual(result, 0)
        dispatch.assert_called_once()
        self.assertIn("返回工作台", "\n".join(messages))
        result, dispatch, _, _ = self.run_menu([KeyboardInterrupt])
        self.assertEqual(result, 130)
        dispatch.assert_not_called()

    def test_recording_selection_check_play_export_and_return(self):
        selected = self.capture("capture-space name")
        destination = self.root / "view $(literal).txt"
        result, dispatch, _, _ = self.run_menu(["5", "1", "i", "v", "t", str(destination), "q", "q"])
        self.assertEqual(result, 0)
        self.assertEqual([call.args[0] for call in dispatch.call_args_list], [
            ["recording-info", str(selected)], ["recording-play", str(selected), "--terminal"],
            ["recording-text", str(selected), "--output", str(destination)]])

    def test_empty_inventory_refresh_and_manual_path(self):
        external = self.root / "external" / "channel"
        external.mkdir(parents=True)
        result, dispatch, messages, _ = self.run_menu(["5", "r", "m", str(external), "i", "q", "q"])
        self.assertEqual(result, 0)
        self.assertEqual(dispatch.call_args.args[0], ["recording-info", str(external)])
        self.assertIn("目前沒有", "\n".join(messages))

    def test_discovery_limits_and_recent_order(self):
        paths = []
        for number in range(55):
            path = self.capture(f"capture-{number:03d}")
            os.utime(path, (1000 + number, 1000 + number))
            paths.append(path)
        found, limited = capture_paths(self.root)
        self.assertFalse(limited)
        self.assertEqual(found, list(reversed(paths[5:])))
        with patch("uart_gateway.workbench.SCAN_LIMIT", 2):
            self.assertEqual(capture_paths(self.root), ([], True))

    def test_display_does_not_execute_control_characters_in_paths(self):
        with patch("uart_gateway.workbench.capture_paths", return_value=([self.root / "capture-\x1b[31m" / "a"], False)):
            _, dispatch, messages, _ = self.run_menu(["5", "q", "q"])
        self.assertNotIn("\x1b", "\n".join(messages))
        self.assertIn("\\x1b", "\n".join(messages))
        dispatch.assert_not_called()

    def test_real_cli_routes_to_existing_selection_and_global_port(self):
        with patch("sys.argv", ["uart", "--port", "18082", "menu"]), \
                patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True), \
                patch("builtins.input", side_effect=["2", "", "", "q"]), patch("sys.stderr", io.StringIO()), \
                patch("uart_gateway.cli._run_selected", return_value=0) as selected:
            with self.assertRaises(SystemExit) as result:
                main()
        self.assertEqual(result.exception.code, 0)
        args = selected.call_args.args[0]
        self.assertEqual(args.port, 18082)
        self.assertTrue(args.select and args.read_only and args.watch and args.terminal)

    def test_real_cli_fatal_cleanup_is_not_swallowed_by_child_main(self):
        with patch("sys.argv", ["uart", "menu"]), patch("sys.stdin.isatty", return_value=True), \
                patch("sys.stdout.isatty", return_value=True), patch("builtins.input", side_effect=["2", "", "", "q"]), \
                patch("sys.stderr", io.StringIO()), \
                patch("uart_gateway.cli._run_selected", side_effect=LauncherCleanupError("未清理")) as selected:
            with self.assertRaises(SystemExit) as result:
                main()
        self.assertEqual(result.exception.code, 1)
        selected.assert_called_once()
