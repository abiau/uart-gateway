"""遠端工作台的選單與快照競態；不使用實體 UART。"""

import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from uart_gateway.cli import main, read_client_token, StreamSelectionChanged, watch_client
from uart_gateway.protocol.errors import RemoteError
from uart_gateway.remote_workbench import run_remote_workbench


def channel(state="ready"):
    return {"channel_id": "a", "label": "主控台", "state": state, "binding": {"revision": "1"},
            "stream_epoch": "epoch", "adapter": {"port": "COM3"},
            "recording": {"state": "disabled", "integrity": "not_recording"},
            "rx": {"earliest_offset": "0", "next_offset": "0"}, "last_error": None}


class RemoteMenuTests(unittest.TestCase):
    def run_menu(self, answers, *, channels=None, query=None, monitor=None):
        read = Mock(side_effect=answers)
        messages = []
        query = query or AsyncMock(return_value=("instance", channels if channels is not None else [channel()]))
        monitor = monitor or AsyncMock(return_value=0)
        with patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True), \
                patch("uart_gateway.remote_workbench.query_channels", query), \
                patch("uart_gateway.remote_workbench.monitor_channel", monitor):
            result = run_remote_workbench(AsyncMock(), AsyncMock(), host="127.0.0.1", port=18081,
                                           read=read, report=messages.append)
        return result, query, monitor, messages

    def test_live_and_replay_choices_keep_selected_snapshot(self):
        result, query, monitor, _ = self.run_menu(["1", "", "1", "r", "q"])
        self.assertEqual(result, 0)
        query.assert_awaited_once()
        self.assertEqual(monitor.await_count, 2)
        self.assertEqual([call.args[-1] for call in monitor.call_args_list], [False, True])
        self.assertEqual(monitor.call_args.args[2:4], (channel(), "instance"))

    def test_auth_failure_only_reconnects_after_explicit_r_and_does_not_echo_message(self):
        error = RemoteError({"code": "AUTH_FAILED", "message": "secret-must-not-appear", "details": {}})
        query = AsyncMock(side_effect=[error, ("new", [channel()])])
        result, query, monitor, messages = self.run_menu(["bad", "r", "q"], query=query)
        self.assertEqual(result, 0)
        self.assertEqual(query.await_count, 2)
        monitor.assert_not_awaited()
        self.assertNotIn("secret-must-not-appear", "\n".join(messages))
        self.assertIn("AUTH_FAILED", "\n".join(messages))

    def test_empty_and_not_ready_channels_do_not_start_watch(self):
        for entries in ([], [channel("lost")]):
            result, _, monitor, _ = self.run_menu(["1", "q"], channels=entries)
            self.assertEqual(result, 0)
            monitor.assert_not_awaited()
        for error in (TimeoutError("等待逾時"), ValueError("未提供 Token"), FileNotFoundError("Token 檔不存在")):
            result, query, monitor, _ = self.run_menu(["q"], query=AsyncMock(side_effect=error))
            self.assertEqual(result, 0)
            query.assert_awaited_once()
            monitor.assert_not_awaited()

    def test_disconnect_requires_explicit_refresh_and_no_automatic_watch(self):
        monitor = AsyncMock(side_effect=ConnectionError("Gateway 已斷線"))
        result, query, monitor, messages = self.run_menu(["1", "l", "1", "q"], monitor=monitor)
        self.assertEqual(result, 0)
        query.assert_awaited_once()
        monitor.assert_awaited_once()
        self.assertIn("目前沒有監看連線", "\n".join(messages))

    def test_lost_snapshot_enter_waits_and_r_does_not_start_monitor(self):
        result, query, monitor, messages = self.run_menu(["", "r", "", "q"], channels=[channel("lost")])
        self.assertEqual(result, 0)
        self.assertEqual(query.await_count, 2)
        monitor.assert_not_awaited()
        output = "\n".join(messages)
        self.assertIn("不會重新開啟 Serial", output)
        self.assertIn("同一 Token", output)
        self.assertIn("全部通道", output)
        self.assertEqual(output.count("保持等待，未重新連線"), 2)

    def test_ended_monitor_enter_waits_until_explicit_refresh(self):
        result, query, monitor, messages = self.run_menu(["1", "l", "", "q"], monitor=AsyncMock(return_value=2))
        self.assertEqual(result, 0)
        query.assert_awaited_once()
        monitor.assert_awaited_once()
        self.assertIn("device_lost", "\n".join(messages))
        self.assertIn("保持等待，未重新連線", "\n".join(messages))

    def test_recovery_refresh_ready_restores_normal_enter_exit(self):
        query = AsyncMock(side_effect=[("old", [channel("lost")]), ("new", [channel()])])
        result, query, monitor, messages = self.run_menu(["", "r", ""], query=query)
        self.assertEqual(result, 0)
        self.assertEqual(query.await_count, 2)
        monitor.assert_not_awaited()
        self.assertIn("已離開遠端工作台", messages[-1])

    def test_recovery_eof_exits_without_busy_retry(self):
        result, query, monitor, messages = self.run_menu([EOFError], channels=[channel("lost")])
        self.assertEqual(result, 0)
        query.assert_awaited_once()
        monitor.assert_not_awaited()
        self.assertIn("EOF", messages[-1])

    def test_cancel_monitor_returns_to_snapshot_but_prompt_cancel_exits(self):
        result, query, monitor, messages = self.run_menu(["1", "l", "q"], monitor=AsyncMock(side_effect=KeyboardInterrupt))
        self.assertEqual(result, 0)
        query.assert_awaited_once()
        self.assertIn("Gateway 繼續執行", "\n".join(messages))
        self.assertEqual(self.run_menu([KeyboardInterrupt])[0], 130)
        self.assertEqual(self.run_menu([EOFError])[0], 0)

    def test_invalid_selection_mode_and_back_do_not_start_watch(self):
        _, _, monitor, _ = self.run_menu(["999", "1", "bad", "q", "q"])
        monitor.assert_not_awaited()

    def test_terminal_required_before_any_connection(self):
        connect = AsyncMock()
        for source, target in ((False, True), (True, False)):
            with patch("sys.stdin.isatty", return_value=source), patch("sys.stdout.isatty", return_value=target):
                with self.assertRaisesRegex(ValueError, "互動式終端機"):
                    run_remote_workbench(connect, AsyncMock(), host="127.0.0.1", port=18081)
        connect.assert_not_awaited()

    def test_metadata_controls_are_escaped(self):
        item = channel()
        item["label"] = "\x1b[31m\n偽裝"
        _, _, _, messages = self.run_menu(["q"], channels=[item])
        self.assertNotIn("\x1b", "\n".join(messages))
        self.assertIn("\\x1b", "\n".join(messages))

    def test_cli_modes_are_exclusive(self):
        with patch("sys.argv", ["uart", "menu", "--remote", "--recordings-dir", "data"]), patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                main()
        self.assertEqual(result.exception.code, 2)

    def test_token_file_and_env_are_read_again_without_persisting_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token"
            args = SimpleNamespace(token_file=str(path), token_env="REMOTE_TEST_TOKEN")
            for value in ("first-value", "updated-value"):
                path.write_text(value)
                self.assertEqual(read_client_token(args), value)
            path.write_bytes(b"\xff")
            with self.assertRaisesRegex(ValueError, "UTF-8"):
                read_client_token(args)
            args.token_file = None
            with patch.dict(os.environ, {"REMOTE_TEST_TOKEN": "env-value"}):
                self.assertEqual(read_client_token(args), "env-value")


class ContextGuardTests(unittest.IsolatedAsyncioTestCase):
    def client(self):
        return SimpleNamespace(instance="instance", get_channel=AsyncMock(return_value=channel()),
            subscribe=AsyncMock(return_value={"subscription_id": "sub", "binding_revision": "1", "stream_epoch": "epoch"}),
            unsubscribe=AsyncMock(), next_event=AsyncMock())

    async def test_stale_instance_revision_epoch_or_state_never_subscribes(self):
        for problem in ("instance", "revision", "epoch", "state"):
            for replay in (False, True):
                client = self.client()
                if problem == "instance":
                    client.instance = "new"
                elif problem == "revision":
                    client.get_channel.return_value["binding"]["revision"] = "2"
                elif problem == "epoch":
                    client.get_channel.return_value["stream_epoch"] = "new"
                else:
                    client.get_channel.return_value["state"] = "lost"
                with self.subTest(problem=problem, replay=replay), self.assertRaises(StreamSelectionChanged):
                    await watch_client(client, "a", duration=None, text_mode=True, replay=replay,
                                       expected_context=("instance", "1", "epoch"))
                client.subscribe.assert_not_awaited()

    async def test_subscription_race_unsubscribes_before_any_output(self):
        for problem in ("revision", "epoch"):
            for replay in (False, True):
                client = self.client()
                client.subscribe.return_value["binding_revision" if problem == "revision" else "stream_epoch"] = "new"
                with self.subTest(problem=problem, replay=replay), \
                        patch("uart_gateway.cli._WatchOutput") as sink, self.assertRaises(StreamSelectionChanged):
                    await watch_client(client, "a", duration=None, text_mode=True, replay=replay,
                                       expected_context=("instance", "1", "epoch"))
                client.unsubscribe.assert_awaited_once_with("sub")
                client.next_event.assert_not_awaited()
                sink.assert_not_called()
