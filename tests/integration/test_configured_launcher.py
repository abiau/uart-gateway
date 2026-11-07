"""真子程序／TCP／PTY 的配置啟動；不是 Win11 實體驗證。"""

import asyncio
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from uart_gateway.configured_launcher import config_text, launch_config
from uart_gateway.local_launcher import LauncherError, PortLock
from uart_gateway.recording_info import inspect_capture_summary
from tests.integration.test_local_launcher import free_port
from tests.integration import test_workbench as console
import tomllib


@unittest.skipUnless(os.name == "posix" and importlib.util.find_spec("serial"), "需要 POSIX PTY／pySerial")
class ConfiguredProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_dual_config_ready_and_existing_listener_is_preserved(self):
        pairs = [os.openpty(), os.openpty()]
        for pair in pairs:
            for fd in pair:
                self.addCleanup(os.close, fd)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "dual.toml"
            config = tomllib.loads(Path("examples/gateway.dual.rx-reopen.toml").read_text())
            port = free_port()
            config["network"]["port"] = port
            for channel, pair in zip(config["channels"], pairs):
                channel["adapter"] = {"port": os.ttyname(pair[1])}
            path.write_text(config_text(config))

            async def monitor(connect, channels, instance):
                self.assertEqual(len(channels), 2)
                self.assertTrue(all(c["state"] == "ready" and c["reopen_rx_available"] for c in channels))
                async with await connect() as client:
                    for index, channel in enumerate(channels):
                        await client.subscribe(channel["channel_id"])
                        os.write(pairs[index][0], f"source-{index}".encode())
                        async with asyncio.timeout(3):
                            while True:
                                event = await client.next_event()
                                if event["event"] == "rx" and event["channel_id"] == channel["channel_id"]:
                                    break
                return 0

            with patch.dict(os.environ, {"UART_GATEWAY_TOKEN": ""}):
                self.assertEqual(await launch_config(path, monitor, no_record=True, startup_timeout=5), 0)
                # 既有 Listener 是獨立服務；失敗不能關閉它或其端點鎖。
                async with await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", port) as existing:
                    with self.assertRaises(LauncherError):
                        await launch_config(path, monitor, no_record=True, startup_timeout=2)
                    self.assertTrue(existing.is_serving())
                    for pair in pairs:
                        with PortLock(os.ttyname(pair[1])):
                            pass

    async def test_record_ready_duplicate_cleanup_and_no_tx(self):
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        os.set_blocking(master, False)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = tomllib.loads(Path("examples/gateway.com3.rx-reopen.toml").read_text())
            config["channels"][0]["adapter"] = {"port": os.ttyname(slave)}
            port = free_port()
            config["network"]["port"] = port
            path = root / "gateway.toml"
            path.write_text(config_text(config))
            snapshots = []

            async def monitor(connect, channels, instance):
                self.assertEqual(channels[0]["state"], "ready")
                self.assertTrue(channels[0]["reopen_rx_available"])
                self.assertEqual(channels[0]["binding"]["status"], "unconfirmed")
                snapshots.extend(channels)
                with self.assertRaises(LauncherError):
                    with PortLock(os.ttyname(slave)):
                        pass
                async with await connect() as client:
                    self.assertEqual(client.instance, instance)
                    self.assertEqual(client.auth["grants"][0]["permissions"], ["configure", "read"])
                    await client.subscribe("ch_uart_1")
                    os.write(master, b"boot\r\n")
                    async with asyncio.timeout(5):
                        while (await client.next_event())["event"] != "rx":
                            pass
                with self.assertRaises(BlockingIOError):
                    os.read(master, 4096)
                return 0

            with patch.dict(os.environ, {"UART_GATEWAY_TOKEN": ""}):
                result = await launch_config(path, monitor, record_directory=root / "recordings", startup_timeout=5)
            self.assertEqual(result, 0)
            with self.assertRaises(OSError):
                await asyncio.open_connection("127.0.0.1", port)
            captures = list((root / "recordings").glob("capture-*/ch_uart_1"))
            self.assertEqual(len(captures), 1)
            result = inspect_capture_summary(captures[0])
            self.assertTrue(result["closed_cleanly"])
            self.assertEqual(result["rx_bytes"], 6)


@unittest.skipUnless(os.name == "posix" and importlib.util.find_spec("serial"), "需要 POSIX PTY／pySerial")
class StartConsoleTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = console.WorkbenchProcessTests.asyncSetUp
    asyncTearDown = console.WorkbenchProcessTests.asyncTearDown
    start = console.WorkbenchProcessTests.start
    expect = console.WorkbenchProcessTests.expect
    answer = console.WorkbenchProcessTests.answer
    finish = console.WorkbenchProcessTests.finish

    async def test_simple_start_remembers_choice_and_opens_only_own_recording(self):
        import sys
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        config = tomllib.loads(Path("examples/gateway.com3.toml").read_text())
        config["channels"][0]["adapter"] = {"port": os.ttyname(slave)}
        config["network"]["port"] = free_port()
        path = self.root / "gateway.toml"
        path.write_text(config_text(config))
        root = self.root / "recordings"
        preferences = self.root / "preferences.json"
        command = [sys.executable, "-m", "uart_gateway.cli", "menu", "--recordings-dir", str(root),
                   "--preferences-file", str(preferences)]
        self.start(command)
        await self.expect("輸入功能編號")
        self.answer("s")
        await self.expect("Enter 開始")
        self.answer("m")
        await self.expect("輸入 Gateway TOML")
        self.answer(str(path))
        await self.expect("Enter 開始")
        self.answer("r")
        await self.expect("錄製：開啟")
        await self.expect("Enter 開始")
        self.answer("")
        await self.expect('"subscription":')
        os.write(master, b"owned-recording\n")
        await self.expect("owned-recording")
        self.answer("q")
        await self.expect("已清理本次啟動的 Gateway")
        await self.expect("輸入功能編號")
        # 新建較新的無關目錄，L 仍須使用 Launcher 的真實回報。
        unrelated = root / "capture-unrelated" / "ch_uart_1"
        unrelated.mkdir(parents=True)
        (unrelated / "000001.jsonl").write_text("bad unrelated data")
        self.answer("l")
        await self.expect("I 檢查資訊")
        self.answer("i")
        await self.expect("正常收尾：是")
        await self.expect("I 檢查資訊")
        self.answer("q")
        await self.expect("輸入功能編號")
        await self.finish()
        saved = __import__("json").loads(preferences.read_text())
        self.assertEqual(saved["config"], str(path))
        self.assertEqual(saved["recording"], "on")
        self.assertNotIn("token", preferences.read_text())
        self.pending.clear()
        self.start(command)
        await self.expect("輸入功能編號")
        self.answer("s")
        await self.expect("錄製：開啟")
        await self.expect("Enter 開始")
        self.answer("")
        await self.expect('"subscription":')
        self.answer("q")
        await self.expect("已清理本次啟動的 Gateway")
        await self.expect("輸入功能編號")
        await self.finish()
        owned = [p for p in root.glob("capture-*/ch_uart_1") if p != unrelated]
        self.assertEqual(len(owned), 2)
        self.assertTrue(all(inspect_capture_summary(p)["closed_cleanly"] for p in owned))

    async def test_recording_filter_search_and_export_real_menu_without_gateway(self):
        import sys
        from tests.unit import test_recording_text as fixtures
        fixture = fixtures.ExportTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.segment(1, [fixture.rx(b"outside\n"), fixture.rx(b"boot selected\n", epoch="second")])
        root = self.root / "recordings"
        channel = root / "capture-test" / "ch_uart_1"
        channel.mkdir(parents=True)
        (channel / "000001.jsonl").write_bytes((fixture.capture / "000001.jsonl").read_bytes())
        destination = self.root / "filtered.txt"
        self.start([sys.executable, "-m", "uart_gateway.cli", "menu", "--recordings-dir", str(root)])
        await self.expect("輸入功能編號")
        self.answer("5")
        await self.expect("Epoch：2")
        await self.expect("輸入錄製編號")
        self.answer("1")
        await self.expect("I 檢查資訊")
        self.answer("f")
        await self.expect("Epoch（")
        self.answer("2")
        await self.expect("起始時間（")
        self.answer("")
        await self.expect("結束時間（")
        self.answer("")
        await self.expect("I 檢查資訊")
        self.answer("s")
        await self.expect("輸入字面關鍵字")
        self.answer("boot")
        await self.expect("搜尋結果：已列出 1 筆")
        await self.expect("輸入命中編號")
        self.answer("1")
        await self.expect("前後各 30 秒")
        await self.expect("Enter 或 V")
        self.answer("t")
        await self.expect("輸入新的")
        self.answer("")
        await self.expect("已匯出文字")
        await self.expect("Enter 或 V")
        self.answer("q")
        await self.expect("輸入命中編號")
        self.answer("q")
        await self.expect("I 檢查資訊")
        self.answer("t")
        await self.expect("輸入新的")
        self.answer(str(destination))
        await self.expect("已匯出文字")
        await self.expect("I 檢查資訊")
        self.answer("q")
        await self.expect("輸入功能編號")
        await self.finish()
        self.assertIn("boot selected", destination.read_text())
        self.assertNotIn("outside", destination.read_text())
        automatic = list((root / "exports").glob("*.txt"))
        self.assertEqual(len(automatic), 1)
        self.assertIn("boot selected", automatic[0].read_text())
        self.assertNotIn("outside", automatic[0].read_text())
        self.assertNotIn("本次建立的 Gateway", self.output.decode(errors="replace"))

    async def test_menu_start_q_cleans_owned_gateway_and_returns(self):
        import sys
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        config = tomllib.loads(Path("examples/gateway.com3.toml").read_text())
        config["channels"][0]["adapter"] = {"port": os.ttyname(slave)}
        config["network"]["port"] = free_port()
        path = self.root / "gateway.toml"
        path.write_text(config_text(config))
        self.start([sys.executable, "-m", "uart_gateway.cli", "menu"])
        await self.expect("輸入功能編號")
        self.answer("6")
        await self.expect("輸入設定編號")
        self.answer("m")
        await self.expect("輸入 Gateway TOML")
        self.answer(str(path))
        await self.expect("錄製：C")
        self.answer("n")
        await self.expect('"subscription":')
        os.write(master, b"hello-start\n")
        await self.expect("hello-start")
        self.answer("s 2")
        await self.expect("通道編號超出")
        self.answer("q")
        await self.expect("已清理本次啟動的 Gateway")
        await self.expect("輸入功能編號")
        # 同一工作台再次啟動；Ctrl+C 必須等待清理後才能接受下一個選擇。
        self.answer("6")
        await self.expect("輸入設定編號")
        self.answer("m")
        await self.expect("輸入 Gateway TOML")
        self.answer(str(path))
        await self.expect("錄製：C")
        self.answer("n")
        await self.expect('"subscription":')
        import signal
        os.kill(self.process.pid, signal.SIGINT)
        await self.expect("已清理本次啟動的 Gateway")
        await self.expect("輸入功能編號")
        await self.finish()
