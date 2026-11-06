"""真 CLI 與 POSIX 主控台；不宣稱 Windows 雙擊或實體 UART 通過。"""

import asyncio
import importlib.util
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import unittest

from uart_gateway.recording_info import inspect_capture
from tests.unit import test_recording_text as fixtures


@unittest.skipUnless(os.name == "posix", "需要 POSIX PTY；Windows 主控台另行驗證")
class WorkbenchProcessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.master, self.slave = os.openpty()
        os.set_blocking(self.master, False)
        self.process = None
        self.pending = bytearray()
        self.output = bytearray()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    async def asyncTearDown(self):
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
            await asyncio.to_thread(self.process.wait)
        os.close(self.master)
        os.close(self.slave)
        self.temp.cleanup()

    def start(self, command):
        env = os.environ.copy()
        env.pop("UART_GATEWAY_TOKEN", None)
        env["PYTHONIOENCODING"] = "utf-8"
        self.process = subprocess.Popen(command, stdin=self.slave, stdout=self.slave, stderr=self.slave,
                                        env=env, start_new_session=True)

    async def expect(self, text):
        needle = text.encode()
        async with asyncio.timeout(6):
            while needle not in self.pending:
                try:
                    chunk = os.read(self.master, 4096)
                except BlockingIOError:
                    await asyncio.sleep(0.005)
                    continue
                self.assertTrue(chunk, self.output.decode(errors="replace"))
                self.pending.extend(chunk)
                self.output.extend(chunk)
                self.assertLess(len(self.output), 1048576)
            end = self.pending.index(needle) + len(needle)
            del self.pending[:end]

    def answer(self, text):
        os.write(self.master, (text + "\n").encode())

    async def finish(self):
        self.answer("q")
        code = await asyncio.wait_for(asyncio.to_thread(self.process.wait), 5)
        self.assertEqual(code, 0, self.output.decode(errors="replace"))

    async def test_browse_check_play_export_with_real_cli_and_no_gateway(self):
        fixture = fixtures.ExportTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        # Fixture 生成真實有雜湊的錄製分段，再放到工作台約定的兩層目錄。
        fixture.segment(1, [fixture.rx("開機正常\r\n".encode())])
        root = self.root / "recordings"
        channel = root / "capture-test" / "ch_uart_1"
        channel.mkdir(parents=True)
        (channel / "000001.jsonl").write_bytes((fixture.capture / "000001.jsonl").read_bytes())
        destination = self.root / "view with spaces.txt"
        self.start([sys.executable, "-m", "uart_gateway.cli", "menu", "--recordings-dir", str(root)])
        await self.expect("輸入功能編號")
        self.answer("5")
        await self.expect("輸入錄製編號")
        self.answer("1")
        await self.expect("I 檢查資訊")
        self.answer("i")
        await self.expect("對已觀測資料無已知缺口")
        await self.expect("I 檢查資訊")
        self.answer("v")
        await self.expect("離線回看完成")
        await self.expect("I 檢查資訊")
        self.answer("t")
        await self.expect("輸入新的")
        self.answer(str(destination))
        await self.expect("已匯出文字")
        await self.expect("I 檢查資訊")
        self.answer("q")
        await self.expect("輸入功能編號")
        await self.finish()
        self.assertIn("開機正常", destination.read_text())
        self.assertNotIn("本次建立的 Gateway", self.output.decode(errors="replace"))

    @unittest.skipUnless(importlib.util.find_spec("serial") is not None, "需要 [server] 的 pySerial")
    async def test_record_ctrl_c_cleanup_then_return_to_workbench(self):
        serial_master, serial_slave = os.openpty()
        endpoint = os.ttyname(serial_slave)
        root = self.root / "recordings"
        # 只替換裝置清單，Gateway 子程序、pySerial、SDK 與清理均保持真實。
        code = """import sys
from unittest.mock import patch
from uart_gateway.core.models import AdapterInfo
from uart_gateway.serial_backend import AdapterDetails
from uart_gateway.cli import main
endpoint, root = sys.argv[1:]
sys.argv = ['uart', 'menu', '--recordings-dir', root]
with patch('uart_gateway.local_ports.list_adapter_details', return_value=[AdapterDetails(AdapterInfo(port=endpoint), '虛擬 UART')]):
    main()
"""
        try:
            self.start([sys.executable, "-c", code, endpoint, str(root)])
            await self.expect("輸入功能編號")
            self.answer("3")
            await self.expect("選擇 UART 數量")
            self.answer("")
            await self.expect("保存端點配置")
            self.answer("")
            await self.expect("輸入裝置編號")
            self.answer("1")
            await self.expect("已就緒；")
            os.write(serial_master, b"workbench RX\n")
            await self.expect("workbench RX")
            self.process.send_signal(signal.SIGINT)
            await self.expect("已清理本次啟動的 Gateway")
            await self.expect("輸入功能編號")
            pids = re.findall(rb"PID ([0-9]+)", self.output)
            self.assertEqual(len(pids), 1)
            self.assertFalse(Path(f"/proc/{int(pids[0])}").exists())
            captures = list(root.glob("capture-*/ch_uart_1"))
            self.assertEqual(len(captures), 1)
            result = inspect_capture(captures[0])
            self.assertEqual(result["integrity"], "no_known_gap")
            self.assertEqual(result["rx_bytes"], 13)
            import serial
            with serial.Serial(endpoint, exclusive=True):
                pass
            await self.finish()
        finally:
            os.close(serial_master)
            os.close(serial_slave)
