"""真終端、CLI、Launcher／pySerial PTY；不代表 Windows 或實體 COM 測試。"""

import asyncio
import importlib.util
import os
from pathlib import Path
import re
import signal
import socket
import struct
import sys
import unittest

from uart_gateway.local_launcher import _profile
from uart_gateway.recording_info import inspect_capture
from tests.integration import test_workbench as console

HAS_TUI = all(importlib.util.find_spec(name) is not None for name in ("textual", "pyte", "serial"))


@unittest.skipUnless(os.name == "posix" and HAS_TUI, "需要 POSIX PTY 與 [server,tui]；Windows 另測")
class TUIProcessTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = console.WorkbenchProcessTests.asyncSetUp
    asyncTearDown = console.WorkbenchProcessTests.asyncTearDown
    start = console.WorkbenchProcessTests.start
    expect = console.WorkbenchProcessTests.expect

    def config(self):
        serial_master, serial_slave = os.openpty()
        self.addCleanup(os.close, serial_master)
        self.addCleanup(os.close, serial_slave)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        endpoint = os.ttyname(serial_slave)
        path = self.root / "gateway.toml"
        path.write_text(_profile(endpoint, port), encoding="utf-8")
        return serial_master, endpoint, port, path

    def command(self, config):
        import fcntl
        import termios
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack("HHHH", 32, 120, 0, 0))
        return [sys.executable, "-m", "uart_gateway.cli", "tui", "--local", "--config", str(config),
                "--preferences-file", str(self.root / "preferences.json"), "--record-dir", str(self.root / "recordings")]

    async def close_app(self, key=b"q", *, interrupt=False):
        if interrupt:
            self.process.send_signal(signal.SIGINT)
        else:
            os.write(self.master, key)
        # 持續讀取全螢幕輸出，避免 PTY 緩衝區反壓阻礙清理。
        async with asyncio.timeout(15):
            while self.process.poll() is None:
                try:
                    data = os.read(self.master, 65536)
                    self.output.extend(data)
                except BlockingIOError:
                    pass
                await asyncio.sleep(0.01)
        self.assertEqual(self.process.returncode, 0, self.output.decode(errors="replace"))

    async def run_local(self, key=b"q", *, interrupt=False):
        import termios
        serial_master, endpoint, port, config = self.config()
        before = termios.tcgetattr(self.slave)
        self.start(self.command(config))
        await self.expect("開始監看")
        os.write(self.master, b"\r")
        await self.expect("等待即時")
        os.write(serial_master, b"tui-local-RX\r\n")
        await self.expect("tui-local-RX")
        await self.close_app(key, interrupt=interrupt)
        self.assertEqual(termios.tcgetattr(self.slave), before)
        for pid in re.findall(rb"PID ([0-9]+)", self.output):
            self.assertFalse(Path(f"/proc/{int(pid)}").exists())
        with socket.socket() as probe:
            self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0)
        import serial
        with serial.Serial(endpoint, exclusive=True):
            pass
        captures = list((self.root / "recordings").glob("capture-*/ch_uart_1"))
        self.assertEqual(len(captures), 1)
        info = inspect_capture(captures[0])
        self.assertTrue(info["closed_cleanly"])
        self.assertEqual(info["rx_bytes"], 14)

    async def test_local_q_cleans_process_recording_and_restores_terminal(self):
        await self.run_local()

    async def test_local_ctrl_c_key_cleans_process_recording_and_restores_terminal(self):
        await self.run_local(b"\x03")

    async def test_local_sigint_cleans_process_recording_and_restores_terminal(self):
        await self.run_local(interrupt=True)

    async def test_cancel_before_start_never_opens_serial_or_gateway(self):
        _, _, port, config = self.config()
        self.start(self.command(config))
        await self.expect("開始監看")
        await self.close_app()
        self.assertNotIn(b"PID ", self.output)
        self.assertFalse((self.root / "recordings").exists())
        with socket.socket() as probe:
            self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0)

    async def test_busy_listener_is_preserved_and_retry_is_manual(self):
        _, _, port, config = self.config()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", port))
            listener.listen()
            self.start(self.command(config))
            await self.expect("開始監看")
            os.write(self.master, b"\r")
            await self.expect("不會接管或關閉它")
            self.assertNotIn(b"PID ", self.output)
            os.write(self.master, b"q")
            async with asyncio.timeout(10):
                while self.process.poll() is None:
                    try:
                        self.output.extend(os.read(self.master, 65536))
                    except BlockingIOError:
                        pass
                    await asyncio.sleep(0.01)
            self.assertEqual(self.process.returncode, 1)
            self.assertEqual(listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN), 1)
