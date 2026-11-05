"""以 Mock 清單驗證本機枚舉、選擇與 CLI，不開啟實體端點。"""

from dataclasses import asdict, replace
import io
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from uart_gateway.cli import main
from uart_gateway.core.models import AdapterInfo
from uart_gateway.local_ports import choose_port, list_ports
from uart_gateway.serial_backend import AdapterDetails, AdapterSelector, SerialBackendError, list_adapter_details, list_adapters
from uart_gateway.local_launcher import LauncherCleanupError, LauncherError


def device(port="COM3", serial="adapter-1", description="USB UART"):
    return AdapterDetails(AdapterInfo(port, 0x0403, 0x6001, serial, "usb-1"), description)


class PortMenuTests(unittest.TestCase):
    def setUp(self):
        self.inventory = patch("uart_gateway.local_ports.list_adapter_details", return_value=[device()]).start()
        self.tty = patch("sys.stdin.isatty", return_value=True).start()
        self.addCleanup(patch.stopall)
        self.messages = []

    def choose(self, answers):
        return choose_port(read=Mock(side_effect=answers), report=self.messages.append)

    def test_list_is_naturally_sorted_and_labels_escape_controls(self):
        self.inventory.return_value = [device("COM10"), device("COM3", None, "name\n\x1b[31m")]
        lines = list_ports()
        self.assertIn("1. COM3", lines[1])
        self.assertIn("2. COM10", lines[2])
        self.assertIn(r"name\n\x1b[31m", lines[1])
        self.assertIn("序號：未提供", lines[1])
        self.assertNotIn("\x1b", "".join(lines))

    def test_number_selects_snapshot_and_returns_all_available_constraints(self):
        self.inventory.return_value = [device("COM10", "adapter-10"), device()]
        selected = self.choose(["2"])
        self.assertEqual(vars(selected), {"port": "COM10", "vid": 0x0403, "pid": 0x6001,
                         "serial_number": "adapter-10", "location": "usb-1"})
        self.assertEqual(self.inventory.call_count, 2)

    def test_one_device_still_requires_choice_and_cancel_or_eof_never_selects(self):
        for answer in ("", "q", "Q", EOFError()):
            with self.subTest(answer=repr(answer)):
                self.assertIsNone(self.choose([answer]))
        self.assertEqual(self.inventory.call_count, 4)

    def test_invalid_numbers_do_not_change_the_snapshot_or_auto_select(self):
        selected = self.choose(["0", "2", "-1", "１", "9" * 5000, "wrong", "1"])
        self.assertEqual(selected.port, "COM3")
        self.assertEqual(self.inventory.call_count, 2)
        self.assertEqual(sum("編號無效" in line for line in self.messages), 6)

    def test_no_devices_refresh_then_explicit_selection(self):
        self.inventory.side_effect = [[], [device("COM8")], [device("COM8")]]
        selected = self.choose(["1", "r", "1"])
        self.assertEqual(selected.port, "COM8")
        self.assertTrue(any("未找到" in line for line in self.messages))

    def test_disappearing_device_does_not_reuse_its_index(self):
        self.inventory.side_effect = [[device()], [device("COM8")]]
        self.assertIsNone(self.choose(["1", "q"]))
        self.assertTrue(any("已消失" in line for line in self.messages))

    def test_same_port_with_changed_identity_requires_a_new_choice(self):
        self.inventory.side_effect = [[device()], [device(serial="new")], [device(serial="new")]]
        self.assertEqual(self.choose(["1", "1"]).serial_number, "new")
        self.assertTrue(any("資訊改變" in line for line in self.messages))

    def test_duplicate_aliases_cannot_be_selected_as_unique(self):
        self.inventory.side_effect = [[device()], [device(), device("com03")]]
        self.assertIsNone(self.choose(["1", "q"]))
        self.assertTrue(any("無法唯一識別" in line for line in self.messages))

    def test_noninteractive_fails_before_enumeration_and_ctrl_c_propagates(self):
        self.tty.return_value = False
        with self.assertRaisesRegex(ValueError, "互動選擇"):
            self.choose(["1"])
        self.inventory.assert_not_called()
        self.tty.return_value = True
        with self.assertRaises(KeyboardInterrupt):
            self.choose([KeyboardInterrupt()])

    def test_partial_usb_identity_is_not_an_invalid_selector(self):
        self.inventory.return_value = [AdapterDetails(replace(device().adapter, pid=None))]
        selected = self.choose(["1"])
        self.assertIsNone(selected.vid)
        self.assertIsNone(selected.pid)
        self.assertEqual(selected.serial_number, "adapter-1")


class EnumerationTests(unittest.TestCase):
    def test_description_is_local_only_and_serial_constructor_is_never_called(self):
        module = Mock()
        ports = SimpleNamespace(comports=Mock(return_value=[SimpleNamespace(device="COM3", vid=1027,
            pid=24577, serial_number="adapter-1", location="usb-1", description="USB UART")]))
        with patch("uart_gateway.serial_backend._serial_module", return_value=module), \
                patch("uart_gateway.serial_backend.importlib.import_module", return_value=ports):
            self.assertEqual(list_adapter_details()[0].description, "USB UART")
            self.assertNotIn("description", asdict(list_adapters()[0]))
        module.Serial.assert_not_called()


class LocalPortCliTests(unittest.TestCase):
    def invoke(self, arguments):
        with patch("sys.argv", ["uart", *arguments]), self.assertRaises(SystemExit) as caught:
            main()
        return caught.exception.code

    def test_ports_needs_no_token_gateway_or_serial_handle(self):
        with patch("uart_gateway.local_ports.list_adapter_details", return_value=[device()]), \
                patch("uart_gateway.local_launcher.launch_local", new=AsyncMock()) as launch, \
                patch("uart_gateway.cli.Client.connect", new=AsyncMock()) as connect, \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(self.invoke(["ports"]), 0)
        self.assertIn("COM3", output.getvalue())
        self.assertIn("USB UART", output.getvalue())
        launch.assert_not_awaited()
        connect.assert_not_awaited()

    def test_empty_ports_is_success_and_enumeration_error_is_visible(self):
        with patch("uart_gateway.local_ports.list_adapter_details", return_value=[]), \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(self.invoke(["ports"]), 0)
        self.assertIn("未找到", output.getvalue())
        with patch("uart_gateway.local_ports.list_adapter_details", side_effect=SerialBackendError("SERIAL_IO_FAILED", "無法枚舉")), \
                patch("sys.stderr", new_callable=io.StringIO) as error:
            self.assertEqual(self.invoke(["ports"]), 1)
        self.assertIn("無法枚舉", error.getvalue())

    def test_cancel_and_ctrl_c_create_no_event_loop_or_gateway(self):
        with patch("uart_gateway.local_ports.list_adapter_details", return_value=[device()]), \
                patch("sys.stdin.isatty", return_value=True), patch("sys.stderr", io.StringIO()), \
                patch("uart_gateway.cli.asyncio.run") as run, patch("builtins.input", return_value="q") as read:
            self.assertEqual(self.invoke(["local", "--select"]), 0)
            read.side_effect = KeyboardInterrupt()
            self.assertEqual(self.invoke(["local", "--select"]), 130)
        run.assert_not_called()

    def test_select_routes_selected_port_and_identity_to_existing_launcher(self):
        with patch("uart_gateway.local_ports.list_adapter_details", return_value=[device("COM8")]), \
                patch("sys.stdin.isatty", return_value=True), patch("sys.stderr", io.StringIO()), \
                patch("builtins.input", return_value="1"), \
                patch("uart_gateway.local_launcher.launch_local", new=AsyncMock(return_value=0)) as launch:
            self.assertEqual(self.invoke(["local", "--select", "--read-only", "--watch"]), 0)
        self.assertEqual(launch.call_args.args[0], "COM8")
        self.assertEqual(launch.call_args.kwargs["selector"].serial_number, "adapter-1")

    def test_fixed_or_default_port_never_enumerates_or_prompts(self):
        for arguments, port in ((["local"], "COM3"), (["local", "--port", "COM8"], "COM8")):
            with self.subTest(port=port), patch("uart_gateway.local_ports.choose_port") as choose, \
                    patch("uart_gateway.local_launcher.launch_local", new=AsyncMock(return_value=0)) as launch:
                self.assertEqual(self.invoke(arguments), 0)
            choose.assert_not_called()
            self.assertEqual(launch.call_args.args[0], port)
            self.assertIsNone(launch.call_args.kwargs["selector"])

    def test_select_and_port_are_exclusive_and_remote_enumeration_is_rejected(self):
        with patch("sys.stderr", io.StringIO()), patch("uart_gateway.local_ports.choose_port") as choose:
            self.assertEqual(self.invoke(["local", "--select", "--port", "COM3"]), 2)
            self.assertEqual(self.invoke(["--host", "remote", "local", "--select"]), 1)
            self.assertEqual(self.invoke(["--host", "remote", "ports"]), 1)
        choose.assert_not_called()

    def test_redirected_terminal_mode_fails_before_prompting(self):
        with patch("sys.stderr", io.StringIO()), patch("sys.stdout.isatty", return_value=False), \
                patch("uart_gateway.local_ports.choose_port") as choose:
            self.assertEqual(self.invoke(["local", "--select", "--terminal"]), 1)
        choose.assert_not_called()

    def test_ended_monitor_requires_fresh_choice_and_can_change_port(self):
        choices = [AdapterSelector(port="COM3"), AdapterSelector(port="COM8")]
        with patch("uart_gateway.local_ports.choose_port", side_effect=choices) as choose, \
                patch("uart_gateway.local_launcher.launch_local", new=AsyncMock(side_effect=[2, 0])) as launch, \
                patch("sys.stderr", new_callable=io.StringIO) as output:
            self.assertEqual(self.invoke(["local", "--select"]), 0)
        self.assertEqual(choose.call_count, 2)
        self.assertEqual([call.args[0] for call in launch.call_args_list], ["COM3", "COM8"])
        self.assertIn("前次記憶體歷史不保留", output.getvalue())

    def test_launch_or_connection_failure_returns_to_choice_until_cancelled(self):
        for error in (LauncherError("端點被占用"), ConnectionError("連線已中斷")):
            with self.subTest(error=type(error).__name__), \
                    patch("uart_gateway.local_ports.choose_port", side_effect=[AdapterSelector(port="COM3"), None]) as choose, \
                    patch("uart_gateway.local_launcher.launch_local", new=AsyncMock(side_effect=error)) as launch, \
                    patch("sys.stderr", new_callable=io.StringIO) as output:
                self.assertEqual(self.invoke(["local", "--select"]), 0)
            self.assertEqual(choose.call_count, 2)
            launch.assert_awaited_once()
            self.assertIn(str(error), output.getvalue())
            self.assertIn("已返回裝置選單", output.getvalue())

    def test_cleanup_or_unexpected_failure_does_not_offer_another_start(self):
        for error in (LauncherCleanupError("無法確認清理"), ValueError("非預期設定錯誤")):
            with self.subTest(error=type(error).__name__), \
                    patch("uart_gateway.local_ports.choose_port", return_value=AdapterSelector(port="COM3")) as choose, \
                    patch("uart_gateway.local_launcher.launch_local", new=AsyncMock(side_effect=error)), \
                    patch("sys.stderr", new_callable=io.StringIO) as output:
                self.assertEqual(self.invoke(["local", "--select"]), 1)
            choose.assert_called_once()
            self.assertNotIn("已返回裝置選單", output.getvalue())

    def test_ctrl_c_during_monitor_exits_instead_of_reselecting(self):
        async def cancelled(*args, **kwargs):
            import asyncio
            raise asyncio.CancelledError()

        with patch("uart_gateway.local_ports.choose_port", return_value=AdapterSelector(port="COM3")) as choose, \
                patch("uart_gateway.local_launcher.launch_local", side_effect=cancelled), \
                patch("sys.stderr", io.StringIO()):
            self.assertEqual(self.invoke(["local", "--select"]), 130)
        choose.assert_called_once()

    def test_invalid_launch_settings_fail_before_selecting(self):
        for flags in (["--gateway-port", "80"], ["--startup-timeout", "nan"]):
            with self.subTest(flags=flags), patch("uart_gateway.local_ports.choose_port") as choose, \
                    patch("sys.stderr", io.StringIO()):
                self.assertEqual(self.invoke(["local", "--select", *flags]), 1)
            choose.assert_not_called()

    def test_fixed_port_ended_status_never_returns_to_menu(self):
        with patch("uart_gateway.local_ports.choose_port") as choose, \
                patch("uart_gateway.local_launcher.launch_local", new=AsyncMock(return_value=2)):
            self.assertEqual(self.invoke(["local", "--port", "COM3"]), 2)
        choose.assert_not_called()
