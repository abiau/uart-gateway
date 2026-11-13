"""多通道本機配置與選單；枚舉均為替身，不操作實體 COM。"""

import asyncio
import io
from pathlib import Path
import tempfile
import tomllib
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from uart_gateway.cli import main, watch_channels
from uart_gateway.client.transport import LocalDataLoss
from uart_gateway.core.models import AdapterInfo
from uart_gateway.local_launcher import _gateway_profile
from uart_gateway.local_ports import choose_channels
from uart_gateway.local_profile import LocalChannel, load_profile, resolve_profile, save_profile, validate_channels
from uart_gateway.serial_backend import AdapterSelector
from uart_gateway.protocol.codec import binary_encode
from uart_gateway.text_renderer import RESET, TaggedText, TerminalRenderer


def channels():
    return (LocalChannel("ch_uart_1", "主控台", AdapterSelector(port="COM3", vid=1027, pid=24577,
                        serial_number="adapter-a", location="usb-1")),
            LocalChannel("ch_uart_2", "MCU", AdapterSelector(port="COM8")))


class ProfileTests(unittest.TestCase):
    def test_profile_roundtrip_readonly_gateway_config_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bench.local.toml"
            save_profile(path, channels())
            self.assertEqual(load_profile(path), channels())
            self.assertNotIn("auth", tomllib.loads(path.read_text()))
            original = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "未覆蓋"):
                save_profile(path, channels()[:1])
            self.assertEqual(path.read_bytes(), original)
        gateway = tomllib.loads(_gateway_profile(channels(), 18081))
        self.assertEqual(len(gateway["channels"]), 2)
        for channel in gateway["channels"]:
            self.assertFalse(channel["auto_reconnect"])
            self.assertEqual(channel["serial"], {"dtr": False, "rts": False})
        self.assertEqual(gateway["auth"]["principals"][0]["permissions"], ["read"])
        self.assertEqual(gateway["auth"]["principals"][0]["channels"], [c.id for c in channels()])

    def test_invalid_profiles_rejected_before_enumeration_or_process(self):
        examples = ["version = 1\nchannels = []", "version = true\nchannels = []",
                    "version = 1\nchannels = []\ntoken = 'forbidden'", "錯誤 TOML", "x" * 65537]
        with tempfile.TemporaryDirectory() as directory, patch("uart_gateway.local_profile.list_adapters") as enumerate_ports:
            path = Path(directory) / "invalid.local.toml"
            for value in examples:
                path.write_text(value, encoding="utf-8")
                with self.subTest(value=value[:32]), self.assertRaises(ValueError):
                    load_profile(path)
            enumerate_ports.assert_not_called()
        for invalid in (
            (channels()[0], LocalChannel("other", "MCU", AdapterSelector(port="com03"))),
            (channels()[0], LocalChannel("other", "主控台", AdapterSelector(port="COM8"))),
            (channels()[0], LocalChannel("ch_uart_1", "MCU", AdapterSelector(port="COM8"))),
            (LocalChannel("a", "\x1b[31m名稱", AdapterSelector(port="COM3")),),
            channels() + (LocalChannel("third", "第三條", AdapterSelector(port="COM9")),),
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_channels(invalid)

    def test_unique_serial_can_follow_com_change_but_preserves_other_conditions(self):
        devices = [AdapterInfo(port="COM12", vid=1027, pid=24577, serial_number="adapter-a", location="usb-1"),
                   AdapterInfo(port="COM8")]
        with patch("uart_gateway.local_profile.list_adapters", return_value=devices):
            resolved = resolve_profile(channels())
        self.assertEqual(resolved[0].adapter.port, "COM12")
        self.assertEqual(resolved[0].label, "主控台")
        self.assertEqual(channels()[0].adapter.port, "COM3")
        for bad in (devices + [AdapterInfo(port="COM13", vid=1027, pid=24577,
                                          serial_number="adapter-a", location="usb-1")],
                    [AdapterInfo(port="COM12", vid=1027, pid=24577, serial_number="adapter-a", location="usb-2"), devices[1]],
                    devices[:1], []):
            with patch("uart_gateway.local_profile.list_adapters", return_value=bad), self.assertRaisesRegex(ValueError, "重新選擇"):
                resolve_profile(channels())

    def test_two_saved_identities_cannot_resolve_to_same_port(self):
        saved = (channels()[0], LocalChannel("second", "第二條", AdapterSelector(port="COM12")))
        with patch("uart_gateway.local_profile.list_adapters", return_value=[
                AdapterInfo(port="COM12", vid=1027, pid=24577, serial_number="adapter-a", location="usb-1")]):
            with self.assertRaisesRegex(ValueError, "相同端點"):
                resolve_profile(saved)

    def test_duplicate_serial_preserves_explicit_selected_port(self):
        devices = [AdapterInfo(port=port, vid=0x0403, pid=0x6001, serial_number="6")
                   for port in ("COM5", "COM6")]
        for port in ("COM5", "COM6"):
            saved = (LocalChannel("one", "IPCAM", AdapterSelector(
                port=port, vid=0x0403, pid=0x6001, serial_number="6")),)
            with self.subTest(port=port), patch("uart_gateway.local_profile.list_adapters", return_value=devices):
                resolved = resolve_profile(saved)
            self.assertEqual(resolved[0].adapter.port, port)
            self.assertEqual(resolved[0].adapter.serial_number, "6")

    def test_duplicate_serial_never_selects_another_port_or_relaxes_identity(self):
        saved = (LocalChannel("one", "IPCAM", AdapterSelector(
            port="COM5", vid=0x0403, pid=0x6001, serial_number="6")),)
        duplicates = [AdapterInfo(port=port, vid=0x0403, pid=0x6001, serial_number="6")
                      for port in ("COM6", "COM7")]
        for devices in (duplicates, duplicates + [AdapterInfo(port="COM5", serial_number="wrong")],
                        [AdapterInfo(port="COM5", vid=0x0403, pid=0x6001, serial_number="6")] * 2):
            with patch("uart_gateway.local_profile.list_adapters", return_value=devices):
                with self.assertRaisesRegex(ValueError, "重新選擇"):
                    resolve_profile(saved)


class GroupMenuTests(unittest.TestCase):
    def test_duplicate_port_and_label_reprompt_then_cancel_is_atomic(self):
        ports = [AdapterSelector(port="COM3"), AdapterSelector(port="com03"), AdapterSelector(port="COM8")]
        reports = []
        with patch("uart_gateway.local_ports.choose_port", side_effect=ports):
            selected = choose_channels(2, read=Mock(side_effect=["主控台", "主控台", "MCU"]), report=reports.append)
        self.assertEqual([c.label for c in selected], ["主控台", "MCU"])
        self.assertTrue(any("相同" in line or "其他通道" in line for line in reports))
        self.assertTrue(any("名稱重複" in line for line in reports))
        with patch("uart_gateway.local_ports.choose_port", side_effect=[ports[0], None]):
            self.assertIsNone(choose_channels(2, read=lambda: "第一條", report=lambda _: None))

    def test_cancel_eof_and_invalid_cli_never_start_gateway(self):
        cases = [(["--select", "--count", "2"], None, 0),
                 (["--port", "COM3", "--count", "2"], None, 2),
                 (["--profile", "file.local.toml", "--select"], None, 2),
                 (["--port", "COM3", "--save-profile", "file.local.toml"], None, 2),
                 (["--select", "--count", "2", "--duration", "inf"], None, 2)]
        for args, selected, code in cases:
            with self.subTest(args=args), patch("sys.argv", ["uart", "local"] + args), \
                    patch("sys.stderr", io.StringIO()), \
                    patch("uart_gateway.local_ports.choose_channels", return_value=selected), \
                    patch("uart_gateway.local_launcher.OwnedGateway.start") as start:
                with self.assertRaises(SystemExit) as result:
                    main()
                self.assertEqual(result.exception.code, code)
                start.assert_not_called()
        with patch("uart_gateway.local_ports.choose_port", return_value=AdapterSelector(port="COM3")):
            reports = []
            self.assertIsNone(choose_channels(2, read=Mock(side_effect=EOFError), report=reports.append))
            self.assertIn("EOF", reports[-1])

    def test_cli_saves_selection_and_recovery_does_not_overwrite_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bench.local.toml"
            args = ["uart", "local", "--select", "--count", "2", "--save-profile", str(path)]
            with patch("sys.argv", args), patch("sys.stderr", io.StringIO()), \
                    patch("uart_gateway.local_ports.choose_channels", side_effect=[channels(), None]) as choose, \
                    patch("uart_gateway.cli.run", new=AsyncMock(return_value=2)):
                with self.assertRaises(SystemExit) as result:
                    main()
                self.assertEqual(result.exception.code, 0)
            self.assertEqual(load_profile(path), channels())
            self.assertTrue(choose.call_args.kwargs["recovering"])


class TaggedDisplayTests(unittest.TestCase):
    def test_sources_control_characters_and_colors_are_isolated_between_batches(self):
        first = TaggedText("第一條", terminal=True)
        second = TaggedText("第二條", terminal=True)
        filter_first = TerminalRenderer()
        a = first.format(filter_first.feed("\x1b[31m紅色\r\n進度\b"))
        b = second.format("一般\n")
        c = first.format("續行")
        self.assertIn("\\r", a)
        self.assertIn("\\x08", a)
        self.assertNotIn("\r", a)
        self.assertNotIn("\b", a)
        self.assertEqual(b, RESET + "[第二條] 一般" + RESET + "\n")
        self.assertEqual(c, RESET + "[第一條] \x1b[31m續行" + RESET + "\n")
        self.assertTrue(all("[第一條]" in line for line in a.splitlines()))
        first.format(filter_first.feed("\x1b[0m"))
        self.assertNotIn("\x1b[31m", first.format("已重設"))

    def test_style_memory_is_bounded_and_extended_colors_survive(self):
        tagged = TaggedText("測試", terminal=True)
        renderer = TerminalRenderer()
        tagged.format(renderer.feed("\x1b[38;2;1;2;3m\x1b[48;5;17m\x1b[1m" * 1000))
        self.assertEqual(len(tagged.styles), 3)
        text = tagged.format("續行")
        self.assertIn("38;2;1;2;3", text)
        self.assertIn("48;5;17", text)


class GroupWatchTests(unittest.IsolatedAsyncioTestCase):
    def client(self, channel_id):
        queue = asyncio.Queue()
        client = SimpleNamespace(
            get_channel=AsyncMock(return_value={"stream_epoch": "epoch", "rx": {"earliest_offset": "0"}}),
            subscribe=AsyncMock(return_value={"subscription_id": channel_id}),
            next_event=queue.get, unsubscribe=AsyncMock(), close=AsyncMock())
        return client, queue

    async def test_one_sdk_failure_keeps_other_channel_receiving(self):
        first, _ = self.client("ch_uart_1")
        second, queue = self.client("ch_uart_2")
        first.next_event = AsyncMock(side_effect=LocalDataLoss("SDK 事件佇列已滿"))
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            task = asyncio.create_task(watch_channels((first, second), channels(), duration=0.1, terminal_mode=False))
            try:
                from tests.fakes import eventually
                await eventually(lambda: "SDK 事件佇列已滿" in stderr.getvalue())
                self.assertFalse(task.done())
                queue.put_nowait({"event": "rx", "data": {"delivery": "live", "data_b64": binary_encode(b"still receiving\n")}})
                self.assertEqual(await task, 0)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertIn("[ch_uart_1／主控台] 監控失敗", stderr.getvalue())
        self.assertIn("[ch_uart_2／MCU] still receiving", stdout.getvalue())
        first.close.assert_awaited_once()
        second.close.assert_awaited_once()

    async def test_shared_writer_failure_stops_all_and_closes_both_clients(self):
        clients = tuple(self.client(c.id)[0] for c in channels())
        with patch("uart_gateway.cli._display", new=AsyncMock(side_effect=BrokenPipeError("模擬畫面關閉"))):
            with self.assertRaisesRegex(BrokenPipeError, "模擬畫面關閉"):
                await asyncio.wait_for(watch_channels(clients, channels(), duration=None, terminal_mode=True), 2)
        for client in clients:
            client.close.assert_awaited_once()
