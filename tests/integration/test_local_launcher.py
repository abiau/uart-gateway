"""真子程序與 POSIX PTY 啟動器測試；不是 Windows／COM3 驗收。"""

import asyncio
import importlib.util
import io
import os
from pathlib import Path
import secrets
import signal
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

from uart_gateway.core.models import ChannelConfig
from uart_gateway.local_launcher import LauncherError, OwnedGateway, PortLock, launch_local
from uart_gateway.local_ports import choose_port
from uart_gateway.protocol.codec import binary_decode
from uart_gateway.serial_backend import AdapterDetails, AdapterSelector, PySerialBackend
from uart_gateway.core.models import AdapterInfo, SerialSettings
from uart_gateway.server.auth import Principal
from uart_gateway.server.tcp import Gateway
from uart_gateway.client import Client
from tests.fakes import eventually
from uart_gateway.cli import main, watch_client


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(os.name == "posix" and importlib.util.find_spec("serial") is not None,
                     "需要 POSIX PTY 與 [server]；Windows 的 CMD／COM3 另行實測")
class LauncherProcessTests(unittest.IsolatedAsyncioTestCase):
    def select(self, endpoint):
        with patch("sys.stdin.isatty", return_value=True), \
                patch("uart_gateway.local_ports.list_adapter_details", return_value=[AdapterDetails(AdapterInfo(port=endpoint), "虛擬端點")]):
            return choose_port(read=lambda: "1", report=self.messages.append)

    async def test_selected_virtual_port_ready_rx_and_shutdown(self):
        selected = self.select(self.endpoint)

        async def monitor(client, channel_id):
            channel = await client.get_channel(channel_id)
            self.assertEqual(channel["state"], "ready")
            self.assertEqual(client.auth["grants"], [{"channel_id": channel_id, "permissions": ["read"]}])
            await client.subscribe(channel_id)
            os.write(self.master, b"selected RX\n")
            async with asyncio.timeout(2):
                received = b""
                while len(received) < len(b"selected RX\n"):
                    event = await client.next_event()
                    if event["event"] == "rx":
                        received += binary_decode(event["data"]["data_b64"])
            self.assertEqual(received, b"selected RX\n")
            return 0

        self.assertEqual(await self.launch(monitor, selector=selected), 0)
        self.assert_reaped()

    async def test_selected_port_removed_before_open_reaps_child(self):
        link = Path(self.temporary.name) / "selected-serial"
        link.symlink_to(self.endpoint)
        selected = self.select(str(link))
        link.unlink()
        from unittest.mock import AsyncMock
        monitor = AsyncMock()
        with self.assertRaisesRegex(LauncherError, "找不到"):
            await launch_local(str(link), monitor, selector=selected, gateway_port=free_port(),
                startup_timeout=5, lock_directory=self.lock_directory, reporter=self.messages.append)
        monitor.assert_not_awaited()
        self.assert_reaped()

    async def test_cli_select_ctrl_c_during_input_starts_no_gateway(self):
        process = await asyncio.create_subprocess_exec(sys.executable, "-m", "uart_gateway.cli", "local", "--select",
            stdin=self.slave, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
        output = []
        try:
            async with asyncio.timeout(5):
                while True:
                    line = (await process.stderr.readline()).decode()
                    self.assertTrue(line, repr(output))
                    output.append(line)
                    if "輸入裝置編號" in line:
                        break
            process.send_signal(signal.SIGINT)
            stdout, stderr = await asyncio.wait_for(process.communicate(), 3)
            self.assertEqual(process.returncode, 130, stderr.decode())
            self.assertFalse(stdout)
            self.assertNotIn("本次建立的 Gateway", "".join(output) + stderr.decode())
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()



    async def asyncSetUp(self):
        self.master, self.slave = os.openpty()
        os.set_blocking(self.master, False)
        self.endpoint = os.ttyname(self.slave)
        self.temporary = tempfile.TemporaryDirectory()
        self.lock_directory = Path(self.temporary.name) / "locks"
        self.owned = []
        self.messages = []
        original_start = OwnedGateway.start

        def record(owned):
            self.owned.append(owned)
            original_start(owned)

        self.record_patch = patch.object(OwnedGateway, "start", record)
        self.record_patch.start()

    async def asyncTearDown(self):
        self.record_patch.stop()
        for owned in self.owned:
            await asyncio.to_thread(owned.stop)
        self.temporary.cleanup()
        os.close(self.master)
        os.close(self.slave)

    async def launch(self, monitor, **options):
        return await launch_local(self.endpoint, monitor, gateway_port=options.pop("gateway_port", free_port()),
            startup_timeout=options.pop("startup_timeout", 5), lock_directory=self.lock_directory,
            reporter=self.messages.append, **options)

    def assert_reaped(self):
        self.assertTrue(self.owned)
        for owned in self.owned:
            self.assertIsNotNone(owned.process.poll())
            self.assertEqual(owned.process.returncode, 0)
            profile = Path(owned.command[owned.command.index("--config") + 1])
            self.assertFalse(profile.exists())
        with PortLock(self.endpoint, self.lock_directory):
            pass

    async def test_real_startup_ready_readonly_rx_and_normal_shutdown(self):
        async def monitor(client, channel_id):
            status = await client.get_channel(channel_id)
            self.assertEqual(status["state"], "ready")
            self.assertEqual(status["binding"]["status"], "unconfirmed")
            self.assertIsNone(status["tx"]["owner_session_id"])
            self.assertEqual(client.auth["grants"], [{"channel_id": channel_id, "permissions": ["read"]}])
            owned = self.owned[0]
            profile = Path(owned.command[owned.command.index("--config") + 1])
            self.assertNotIn(owned.token, profile.read_text(encoding="utf-8"))
            self.assertNotIn(owned.token, repr(owned.command))
            sub = await client.subscribe(channel_id)
            payload = "模擬開機\n".encode("utf-8") + b"\x00\xff"
            os.write(self.master, payload)
            data = bytearray()
            async with asyncio.timeout(2):
                while len(data) < len(payload):
                    event = await client.next_event()
                    if event["event"] == "rx" and event["data"]["subscription_id"] == sub["subscription_id"]:
                        data.extend(binary_decode(event["data"]["data_b64"]))
            self.assertEqual(bytes(data), payload)
            try:
                self.assertEqual(os.read(self.master, 1024), b"")
            except BlockingIOError:
                pass
            return 0

        self.assertEqual(await self.launch(monitor), 0)
        self.assert_reaped()
        self.assertTrue(any("已就緒" in message for message in self.messages))

    async def test_duplicate_launcher_cannot_start_another_child(self):
        ready = asyncio.Event()
        release = asyncio.Event()

        async def monitor(*_):
            ready.set()
            await release.wait()
            return 0

        first = asyncio.create_task(self.launch(monitor))
        try:
            await asyncio.wait_for(ready.wait(), 5)
            with self.assertRaisesRegex(LauncherError, "已有本機啟動器"):
                await self.launch(monitor)
            self.assertEqual(len(self.owned), 1)
            self.assertIsNone(self.owned[0].process.poll())
        finally:
            release.set()
            await first
        self.assert_reaped()

    async def test_restart_after_shutdown_reuses_rpc_and_serial(self):
        port = free_port()

        async def monitor(*_):
            return 0

        for _ in range(2):
            self.assertEqual(await self.launch(monitor, gateway_port=port), 0)
            self.assert_reaped()
        self.assertEqual(len(self.owned), 2)

    async def test_cancel_after_ready_gracefully_closes_child(self):
        ready = asyncio.Event()

        async def monitor(*_):
            ready.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(self.launch(monitor))
        await asyncio.wait_for(ready.wait(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assert_reaped()

    async def test_cancel_while_starting_also_cleans_child(self):
        async def monitor(*_):
            await asyncio.Event().wait()

        task = asyncio.create_task(self.launch(monitor))
        await eventually(lambda: bool(self.owned) and self.owned[0].process is not None)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assert_reaped()

    async def test_existing_listener_is_untouched_and_no_child_created(self):
        server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            with self.assertRaisesRegex(LauncherError, "已被使用"):
                await self.launch(lambda *_: None, gateway_port=port)
            self.assertFalse(self.owned)
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            self.assertTrue(server.is_serving())
        finally:
            server.close()
            await server.wait_closed()

    async def test_busy_serial_failure_preserves_original_gateway(self):
        token = secrets.token_urlsafe(32)
        original = Gateway([(ChannelConfig("original", "original"),
            PySerialBackend(AdapterSelector(port=self.endpoint), SerialSettings()))],
            (Principal("reader", token, {"original": frozenset({"read"})}),))
        port = await original.start(port=0)
        try:
            for selected in (None, self.select(self.endpoint)):
                with self.subTest(interactive=selected is not None), self.assertRaisesRegex(LauncherError, "MobaXterm"):
                    await self.launch(lambda *_: None, selector=selected)
                self.assert_reaped()
            async with await Client.connect("127.0.0.1", port, token) as client:
                self.assertEqual((await client.get_channel("original"))["state"], "ready")
        finally:
            await original.close()

    async def test_nonexistent_port_fails_without_monitor_and_reaps_child(self):
        monitored = False

        async def monitor(*_):
            nonlocal monitored
            monitored = True
            return 0

        with self.assertRaisesRegex(LauncherError, "找不到"):
            await launch_local(str(Path(self.temporary.name) / "missing"), monitor,
                gateway_port=free_port(), startup_timeout=5, lock_directory=self.lock_directory,
                reporter=self.messages.append)
        self.assertFalse(monitored)
        self.assert_reaped()

    async def test_cli_ctrl_c_stops_owned_gateway_process(self):
        process = await asyncio.create_subprocess_exec(sys.executable, "-m", "uart_gateway.cli", "local",
            "--port", self.endpoint, "--gateway-port", str(free_port()), "--read-only", "--watch",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
        gateway_pid = None
        output = []
        try:
            async with asyncio.timeout(5):
                while True:
                    line = (await process.stderr.readline()).decode("utf-8")
                    self.assertTrue(line, repr(output))
                    output.append(line)
                    if "PID " in line:
                        gateway_pid = int(line.rsplit("PID ", 1)[1])
                    if "已就緒；" in line:
                        break
            process.send_signal(signal.SIGINT)
            stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
            self.assertEqual(process.returncode, 130, stderr.decode())
            self.assertIn("已清理本次", stderr.decode())
            self.assertFalse(stdout)
            self.assertIsNotNone(gateway_pid)
            self.assertFalse(Path(f"/proc/{gateway_pid}").exists())
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()


@unittest.skipUnless(os.name == "posix" and importlib.util.find_spec("serial") is not None,
                     "需要 POSIX PTY 與 [server]；Windows 插拔恢復另行實測")
class InteractiveRecoveryTests(unittest.TestCase):
    def test_unplug_cleanup_reselect_another_port_and_receive_again(self):
        first_master, first_slave = os.openpty()
        second_master, second_slave = os.openpty()
        first_port, second_port = os.ttyname(first_slave), os.ttyname(second_slave)
        descriptors = {first_master, first_slave, second_master, second_slave}
        inventory = [AdapterDetails(AdapterInfo(port=first_port), "第一個虛擬端點")]
        owned = []
        instances, epochs = [], []
        choices = 0
        stdout, stderr = io.StringIO(), io.StringIO()
        original_start = OwnedGateway.start

        def start(gateway):
            owned.append(gateway)
            original_start(gateway)

        def read():
            nonlocal choices
            choices += 1
            if choices == 2:
                self.assertEqual(owned[0].process.poll(), 0)
                config = Path(owned[0].command[owned[0].command.index("--config") + 1])
                self.assertFalse(config.exists())
                with PortLock(first_port):
                    pass
            self.assertLessEqual(choices, 2)
            return "1"

        async def monitor(client, channel_id, **options):
            number = len(instances)
            channel = await client.get_channel(channel_id)
            self.assertEqual(channel["adapter"]["port"], (first_port, second_port)[number])
            self.assertEqual(channel["rx"]["next_offset"], "0")
            self.assertEqual(client.auth["grants"], [{"channel_id": channel_id, "permissions": ["read"]}])
            instances.append(client.instance)
            epochs.append(channel["stream_epoch"])
            subscribed = asyncio.Event()
            original_subscribe = client.subscribe

            async def subscribe(*args, **kwargs):
                result = await original_subscribe(*args, **kwargs)
                subscribed.set()
                return result

            options["duration"] = None if number == 0 else 0.3
            with patch.object(client, "subscribe", side_effect=subscribe):
                task = asyncio.create_task(watch_client(client, channel_id, **options))
                try:
                    await asyncio.wait_for(subscribed.wait(), 2)
                    payload = b"first RX\n" if number == 0 else b"second RX\n"
                    os.write(first_master if number == 0 else second_master, payload)
                    await eventually(lambda: payload.decode() in stdout.getvalue())
                    if number == 0:
                        inventory[:] = [AdapterDetails(AdapterInfo(port=second_port), "第二個虛擬端點")]
                        os.close(first_master)
                        descriptors.remove(first_master)
                    return await asyncio.wait_for(task, 5)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        try:
            with patch("sys.argv", ["uart", "local", "--select", "--gateway-port", str(free_port())]), \
                    patch("sys.stdin.isatty", return_value=True), patch("sys.stdout", stdout), patch("sys.stderr", stderr), \
                    patch("uart_gateway.local_ports.list_adapter_details", side_effect=lambda: list(inventory)), \
                    patch("builtins.input", side_effect=read), patch.object(OwnedGateway, "start", side_effect=start, autospec=True), \
                    patch("uart_gateway.cli.watch_client", side_effect=monitor):
                with self.assertRaises(SystemExit) as result:
                    main()
            self.assertEqual(result.exception.code, 0)
            self.assertEqual(choices, 2)
            self.assertEqual(len(owned), 2)
            self.assertNotEqual(instances[0], instances[1])
            self.assertNotEqual(epochs[0], epochs[1])
            self.assertNotEqual(owned[0].token, owned[1].token)
            self.assertTrue(all(gateway.process.poll() == 0 for gateway in owned))
            self.assertIn("裝置已失去連線", stderr.getvalue())
            self.assertIn("已返回裝置選單", stderr.getvalue())
            self.assertIn("第一個虛擬端點", stderr.getvalue())
            self.assertIn("第二個虛擬端點", stderr.getvalue())
        finally:
            for gateway in owned:
                gateway.stop()
            for descriptor in descriptors:
                os.close(descriptor)
