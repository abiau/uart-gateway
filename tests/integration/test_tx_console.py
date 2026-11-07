"""真 CLI／POSIX 終端鍵盤與 SDK；Serial 使用 Fake，不是 Win11 實機。"""

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import unittest

from uart_gateway.auth_setup import initialize
from uart_gateway.fake_serial import FakeSerial
from uart_gateway.protected_files import read_private, write_private
from uart_gateway.server.app import load
from tests.integration import test_workbench as fixture

HAS_TUI = all(importlib.util.find_spec(name) is not None for name in ("textual", "pyte"))


@unittest.skipUnless(os.name == "posix" and HAS_TUI, "需要 POSIX 終端與可選 [tui]")
class TxConsoleTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.WorkbenchProcessTests.asyncSetUp
    start = fixture.WorkbenchProcessTests.start
    expect = fixture.WorkbenchProcessTests.expect

    async def asyncTearDown(self):
        await fixture.WorkbenchProcessTests.asyncTearDown(self)
        if getattr(self, "gateway", None) is not None:
            await self.gateway.close()

    async def test_cli_tx_f2_typing_preview_once_f3_and_exit(self):
        import fcntl
        import termios
        authority = self.root / "authority"
        config = initialize(Path("examples/gateway.com3.toml"), directory=authority, enable_tx=True)
        self.gateway, _, _ = load(config, fake=False)
        device = FakeSerial(echo=False)
        self.gateway.channels["ch_uart_1"].port = device
        port = await self.gateway.start(port=0)
        path = config.parent / "client.json"
        profile = json.loads(read_private(path, 16384))
        profile["port"] = port
        write_private(path, json.dumps(profile).encode(), replace=True)
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack("HHHH", 36, 120, 0, 0))
        self.start([sys.executable, "-m", "uart_gateway.cli", "--connections-dir", str(authority), "tx", "--channels", "ch_uart_1"])
        await self.expect("等待即時")
        os.write(self.master, b"\x1bOQ")  # F2
        await self.expect("確認 TX 目標")
        os.write(self.master, b" ")
        await self.expect("已勾選")
        os.write(self.master, b"\t\t\r")  # 核對後 Tab 至確認；沒有 TX。
        await self.expect("輸入內容")
        os.write(self.master, b"qy\r")
        await self.expect("送出前預覽")
        self.assertEqual(device.write_count, 0)
        os.write(self.master, b"\t\r")  # 預設取消；Tab 選送出一次。
        await self.expect("Driver 已接受全部")
        self.assertEqual(list(device.writes), [b"qy"])
        os.write(self.master, b"\x1b")  # 返回監看
        await self.expect("等待即時")
        os.write(self.master, b"\x1bOR")  # F3，只開收據，不寫入。
        await self.expect("TX 結果")
        self.assertEqual(device.write_count, 1)
        os.write(self.master, b"q")
        async with asyncio.timeout(10):
            while self.process.poll() is None:
                try:
                    self.output.extend(os.read(self.master, 65536))
                except BlockingIOError:
                    pass
                await asyncio.sleep(0.01)
        self.assertEqual(self.process.returncode, 0)
        self.assertTrue(self.gateway.listener.is_serving())
        self.assertIsNone(self.gateway.channels["ch_uart_1"].lease.owner)
