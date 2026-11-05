"""雙 PTY、真子程序與 SDK；不是 Win11 雙實體 UART 驗收。"""

import asyncio
import importlib.util
import io
import os
from pathlib import Path
import signal
import socket
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from uart_gateway.cli import watch_channels
from uart_gateway.local_launcher import LauncherError, OwnedGateway, PortLock, launch_channels
from uart_gateway.local_profile import LocalChannel, save_profile
from uart_gateway.serial_backend import AdapterSelector
from tests.fakes import eventually
from tests.integration.test_local_launcher import free_port


@unittest.skipUnless(os.name == "posix" and importlib.util.find_spec("serial") is not None,
                     "需要 POSIX PTY 與 [server]；Win11 雙實體 UART 另行實測")
class MultiLauncherTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pairs = [os.openpty(), os.openpty()]
        self.fds = {fd for pair in self.pairs for fd in pair}
        self.channels = tuple(LocalChannel(f"ch_uart_{index + 1}", label, AdapterSelector(port=os.ttyname(pair[1])))
                              for index, (pair, label) in enumerate(zip(self.pairs, ("主控台", "MCU"))))
        self.temporary = tempfile.TemporaryDirectory()
        self.locks = Path(self.temporary.name) / "locks"
        self.messages, self.owned = [], []
        start = OwnedGateway.start

        def record(owned):
            self.owned.append(owned)
            start(owned)

        self.record_patch = patch.object(OwnedGateway, "start", record)
        self.record_patch.start()
        for master, _ in self.pairs:
            os.set_blocking(master, False)

    async def asyncTearDown(self):
        self.record_patch.stop()
        for owned in self.owned:
            await asyncio.to_thread(owned.stop)
        for fd in self.fds:
            os.close(fd)
        self.temporary.cleanup()

    async def launch(self, monitor, **options):
        return await launch_channels(self.channels, monitor, gateway_port=options.pop("gateway_port", free_port()),
            startup_timeout=5, lock_directory=self.locks, reporter=self.messages.append, **options)

    def assert_reaped(self):
        self.assertEqual(len(self.owned), 1)
        owned = self.owned[0]
        self.assertEqual(owned.process.poll(), 0)
        self.assertFalse(Path(owned.command[owned.command.index("--config") + 1]).exists())
        for channel in self.channels:
            with PortLock(channel.adapter.port, self.locks):
                pass

    async def test_two_rx_then_one_unplug_keeps_other_session_and_epoch_alive(self):
        stdout, stderr = io.StringIO(), io.StringIO()

        async def monitor(clients, channels):
            self.assertEqual(clients[0].instance, clients[1].instance)
            self.assertIsNot(clients[0].transport, clients[1].transport)
            expected = [{"channel_id": c.id, "permissions": ["read"]} for c in channels]
            snapshots = [await client.get_channel(channel.id) for client, channel in zip(clients, channels)]
            for client, snapshot in zip(clients, snapshots):
                self.assertEqual(client.auth["grants"], expected)
                self.assertEqual(snapshot["binding"]["status"], "unconfirmed")
                self.assertEqual(snapshot["state"], "ready")
            task = asyncio.create_task(watch_channels(clients, channels, duration=5, terminal_mode=True))
            try:
                await eventually(lambda: stderr.getvalue().count('"subscription":') == 2)
                os.write(self.pairs[0][0], "\x1b[32m第一路\x1b[0m\r\n".encode())
                os.write(self.pairs[1][0], "第二路\r\n".encode())
                await eventually(lambda: "第一路" in stdout.getvalue() and "第二路" in stdout.getvalue())
                for master, _ in self.pairs:
                    with self.assertRaises(BlockingIOError):
                        os.read(master, 4096)
                master = self.pairs[0][0]
                os.close(master)
                self.fds.remove(master)
                await eventually(lambda: "裝置已失去連線" in stderr.getvalue())
                self.assertFalse(task.done())
                self.assertIsNone(self.owned[0].process.poll())
                os.write(self.pairs[1][0], "拔除後仍接收\n".encode())
                await eventually(lambda: "拔除後仍接收" in stdout.getvalue())
                remaining = await clients[1].get_channel(channels[1].id)
                self.assertEqual(remaining["stream_epoch"], snapshots[1]["stream_epoch"])
                self.assertEqual(remaining["state"], "ready")
                self.assertIn("[ch_uart_1／主控台]", stdout.getvalue())
                self.assertIn("[ch_uart_2／MCU] 拔除後仍接收", stdout.getvalue())
                master = self.pairs[1][0]
                os.close(master)
                self.fds.remove(master)
                return await asyncio.wait_for(task, 3)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            self.assertEqual(await self.launch(monitor), 2)
        self.assert_reaped()

    async def test_second_busy_fails_whole_startup_and_releases_first(self):
        import serial
        busy = serial.Serial(self.channels[1].adapter.port, exclusive=True)
        monitor = AsyncMock()
        try:
            with self.assertRaisesRegex(LauncherError, "MobaXterm"):
                await self.launch(monitor)
            monitor.assert_not_awaited()
            self.assertTrue(busy.is_open)
            with serial.Serial(self.channels[0].adapter.port, exclusive=True):
                pass
            self.assert_reaped()
            self.assertFalse(any("已就緒；" in line for line in self.messages))
        finally:
            busy.close()

    async def test_duplicate_group_does_not_start_or_affect_original(self):
        async def monitor(clients, channels):
            other_monitor = AsyncMock()
            with self.assertRaisesRegex(LauncherError, "已有本機啟動器"):
                await self.launch(other_monitor)
            other_monitor.assert_not_awaited()
            self.assertEqual(len(self.owned), 1)
            for client, channel in zip(clients, channels):
                self.assertEqual((await client.get_channel(channel.id))["state"], "ready")
            return 0

        self.assertEqual(await self.launch(monitor), 0)
        self.assert_reaped()

    async def test_second_lock_conflict_releases_first_without_starting_child(self):
        ordered = sorted(self.channels, key=lambda c: c.adapter.port)
        with PortLock(ordered[1].adapter.port, self.locks):
            with self.assertRaises(LauncherError):
                await self.launch(AsyncMock())
            self.assertFalse(self.owned)
            with PortLock(ordered[0].adapter.port, self.locks):
                pass

    async def test_existing_listener_is_untouched_and_all_locks_release(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            with self.assertRaisesRegex(LauncherError, "不會接管"):
                await self.launch(AsyncMock(), gateway_port=port)
            self.assertFalse(self.owned)
            self.assertEqual(listener.getsockname()[1], port)
            for channel in self.channels:
                with PortLock(channel.adapter.port, self.locks):
                    pass

    async def test_real_cli_profile_sigint_reaps_gateway_and_both_ports_can_reopen(self):
        import serial
        path = Path(self.temporary.name) / "bench.local.toml"
        save_profile(path, self.channels)
        # PTY 不在一般 USB 枚舉清單；只替換 CLI 枚舉，子程序及 Serial 保持真實。
        code = """import sys
from unittest.mock import patch
from uart_gateway.local_profile import load_profile
from uart_gateway.core.models import AdapterInfo
from uart_gateway.cli import main
from pathlib import Path
profile = Path(sys.argv[1])
inventory = [AdapterInfo(port=c.adapter.port) for c in load_profile(profile)]
sys.argv = ['uart', 'local', '--profile', str(profile), '--gateway-port', sys.argv[2]]
with patch('uart_gateway.local_profile.list_adapters', return_value=inventory):
    main()
"""
        process = await asyncio.create_subprocess_exec(sys.executable, "-c", code, str(path), str(free_port()),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
        pid, messages = None, []
        try:
            async with asyncio.timeout(5):
                while True:
                    line = (await process.stderr.readline()).decode()
                    self.assertTrue(line, repr(messages))
                    messages.append(line)
                    if "PID " in line:
                        pid = int(line.rsplit("PID ", 1)[1])
                    if '"subscription":' in line and sum('"subscription":' in text for text in messages) == 2:
                        break
            process.send_signal(signal.SIGINT)
            _, error = await asyncio.wait_for(process.communicate(), 5)
            self.assertEqual(process.returncode, 130, error.decode())
            self.assertIn("已清理本次", error.decode())
            self.assertIsNotNone(pid)
            self.assertFalse(Path(f"/proc/{pid}").exists())
            for channel in self.channels:
                with serial.Serial(channel.adapter.port, exclusive=True):
                    pass
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def test_cancellation_cleans_both_sessions_and_single_child(self):
        started = asyncio.Event()
        observed_clients = []

        async def monitor(clients, channels):
            observed_clients.extend(clients)
            started.set()
            return await watch_channels(clients, channels, duration=None, terminal_mode=False)

        with patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()):
            task = asyncio.create_task(self.launch(monitor))
            try:
                await asyncio.wait_for(started.wait(), 5)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        for client in observed_clients:
            self.assertTrue(client.transport.reader_task.done())
        self.assert_reaped()

    async def test_dual_replay_survives_briefly_blocked_display(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        displayed, release = asyncio.Event(), asyncio.Event()
        payloads = [("A" * 140 + "\n").encode() * 400, ("B" * 140 + "\n").encode() * 400]
        from uart_gateway.cli import _display

        async def paused_display(text, *, stream=None):
            if stream is None:
                displayed.set()
                await release.wait()
            await _display(text, stream=stream)

        async def monitor(clients, channels):
            for (master, _), payload, client, channel in zip(self.pairs, payloads, clients, channels):
                # 小段灌入 PTY，等待 Gateway 已讀取再開始回播。
                for offset in range(0, len(payload), 1000):
                    os.write(master, payload[offset:offset + 1000])
                    await asyncio.sleep(0.001)
                async with asyncio.timeout(3):
                    while int((await client.get_channel(channel.id))["rx"]["next_offset"]) < len(payload):
                        await asyncio.sleep(0.005)
            task = asyncio.create_task(watch_channels(clients, channels, duration=0.5, terminal_mode=False))
            try:
                await asyncio.wait_for(displayed.wait(), 2)
                await asyncio.sleep(0.2)
                for client, channel in zip(clients, channels):
                    self.assertIsNone(client.transport.failure)
                    self.assertEqual((await client.get_channel(channel.id))["state"], "ready")
                release.set()
                return await asyncio.wait_for(task, 3)
            finally:
                release.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        with patch("sys.stdout", stdout), patch("sys.stderr", stderr), patch("uart_gateway.cli._display", side_effect=paused_display):
            self.assertEqual(await self.launch(monitor), 0)
        self.assertEqual(stdout.getvalue().count("A"), 140 * 400)
        self.assertEqual(stdout.getvalue().count("B"), 140 * 400)
        self.assert_reaped()
