"""真 SDK／TCP 與 Textual 無頭 UI；UART 使用 Fake，Win11 實機另測。"""

import asyncio
import importlib.util
import secrets
import threading
import unittest
from unittest.mock import patch

from uart_gateway.client import Client
from uart_gateway.core.models import ChannelConfig
from uart_gateway.fake_serial import FakeSerial
from uart_gateway.server.auth import Principal
from uart_gateway.server.tcp import Gateway
from tests.fakes import eventually

HAS_TUI = importlib.util.find_spec("textual") is not None and importlib.util.find_spec("pyte") is not None


@unittest.skipUnless(HAS_TUI, "需要 [tui] 可選依賴")
class TUIIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from uart_gateway.tui_app import UARTApp
        self.app_type = UARTApp
        self.token = secrets.token_urlsafe(32)
        self.devices = [FakeSerial(echo=False), FakeSerial(echo=False)]
        self.gateway = Gateway([(ChannelConfig(cid, cid), device) for cid, device in zip(("a", "b"), self.devices)],
            (Principal("operator", self.token, {"a": frozenset({"read", "configure"}), "b": frozenset({"read"})}),))
        self.port = await self.gateway.start(port=0)

    async def asyncTearDown(self):
        await self.gateway.close()

    async def connect(self):
        return await Client.connect("127.0.0.1", self.port, self.token)

    async def ready(self, app):
        await eventually(lambda: len(app.controller.panes) == 2 and all(app.controller.active(cid) for cid in ("a", "b")), timeout=4)
        await eventually(lambda: all(c.subscriptions for c in self.gateway.channels.values()), timeout=4)

    async def test_fixed_panes_keep_sources_separate_and_q_preserves_remote_gateway(self):
        from uart_gateway.tui_widgets import SerialView, UARTPane
        app = self.app_type(self.connect)
        async with app.run_test(size=(140, 42)) as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await self.ready(app)
            self.devices[0].inject(b"left-only\r\n")
            self.devices[1].inject(b"right-only\r\n")
            await eventually(lambda: all(p.rx_bytes for p in app.controller.panes.values()))
            await pilot.pause()
            panes = list(app.query(UARTPane))
            await eventually(lambda: all(v.version == v.model.version for v in app.query(SerialView)))
            displays = ["".join(segment.text for segment in pane.query_one(SerialView).render_line(0)) for pane in panes]
            self.assertIn("left-only", displays[0])
            self.assertIn("right-only", displays[1])
            self.assertEqual(len(panes), 2)
            self.assertLess(panes[0].region.right, panes[1].region.right)
            self.assertEqual(app.query_one("#heading").region.bottom, app.query_one("#hint").region.y)
            self.assertLessEqual(app.query_one("#header").region.bottom, panes[0].region.y)
            self.assertLessEqual(panes[0].region.bottom, app.query_one("#footer").region.y)
            self.assertLessEqual(app.query_one("#notice").region.bottom, app.query_one("#actions").region.y)
            before = app.query_one("#actions").region
            self.devices[0].inject(b"line\r\n" * 500)
            self.devices[0].inject(b"line\r\n" * 500)
            await eventually(lambda: app.controller.panes["a"].rx_bytes > 5000)
            await pilot.pause()
            self.assertEqual(before, app.query_one("#actions").region)
            left = "".join(app.controller.panes["a"].terminal.screen.display)
            right = "".join(app.controller.panes["b"].terminal.screen.display)
            self.assertNotIn("right-only", left)
            self.assertIn("right-only", right)
            self.assertNotIn("left-only", right)
            await pilot.resize_terminal(76, 30)
            await pilot.pause()
            self.assertTrue(app.query_one("#panes").has_class("stacked"))
            self.assertGreaterEqual(panes[1].region.y, panes[0].region.bottom)
            self.assertLessEqual(panes[1].region.bottom, app.query_one("#footer").region.y)
            await pilot.press("q")
        await eventually(lambda: not self.gateway.sessions)
        self.assertTrue(self.gateway.listener.is_serving())
        self.assertTrue(all(d.write_count == 0 for d in self.devices))

    async def test_pause_freezes_only_selected_view_and_data_reception_continues(self):
        from uart_gateway.tui_widgets import SerialView
        app = self.app_type(self.connect, channels=["a", "b"])
        async with app.run_test(size=(140, 42)) as pilot:
            await self.ready(app)
            self.devices[0].inject(b"before\n")
            await eventually(lambda: app.controller.panes["a"].rx_bytes > 0)
            await pilot.press("p")
            view = list(app.query(SerialView))[0]
            frozen = view.frozen
            self.assertIsNotNone(frozen)
            self.devices[0].inject(b"after\n")
            self.devices[1].inject(b"peer\n")
            await eventually(lambda: app.controller.panes["a"].rx_bytes > 6 and app.controller.panes["b"].rx_bytes > 0)
            self.assertIs(view.frozen, frozen)
            await pilot.press("end")
            self.assertIsNone(view.frozen)
            await pilot.press("q")

    async def test_reopen_confirmation_and_slow_driver_keep_peer_alive(self):
        from uart_gateway.tui_widgets import ConfirmDialog
        app = self.app_type(self.connect, channels=["a", "b"])
        gate, entered = threading.Event(), threading.Event()
        try:
            async with app.run_test(size=(140, 42)) as pilot:
                await self.ready(app)
                peer_epoch = self.gateway.channels["b"].epoch
                peer_subscriptions = dict(self.gateway.channels["b"].subscriptions)
                await pilot.press("p")
                self.devices[0].close()
                await eventually(lambda: self.gateway.channels["a"].state == "lost")
                await pilot.press("o")
                await eventually(lambda: isinstance(app.screen, ConfirmDialog))
                self.devices[1].inject(b"during-confirm\n")
                await eventually(lambda: app.controller.panes["b"].rx_bytes > 0)
                await pilot.press("escape")
                self.assertEqual(self.gateway.channels["a"].state, "lost")
                original = self.devices[0].open
                def delayed():
                    entered.set()
                    if not gate.wait(3):
                        raise OSError("測試逾時")
                    original()
                with patch.object(self.devices[0], "open", side_effect=delayed):
                    await pilot.press("o")
                    await eventually(lambda: isinstance(app.screen, ConfirmDialog))
                    await pilot.press("y")
                    await eventually(entered.is_set)
                    before = app.controller.panes["b"].rx_bytes
                    self.devices[1].inject(b"during-open\n")
                    await eventually(lambda: app.controller.panes["b"].rx_bytes > before)
                    gate.set()
                    await eventually(lambda: self.gateway.channels["a"].state == "ready")
                    await eventually(lambda: self.gateway.channels["a"].subscriptions)
                    await pilot.pause()
                    from uart_gateway.tui_widgets import SerialView
                    self.assertIsNone(list(app.query(SerialView))[0].frozen)
                self.assertEqual(self.gateway.channels["b"].epoch, peer_epoch)
                self.assertEqual(self.gateway.channels["b"].subscriptions, peer_subscriptions)
                self.assertFalse(self.gateway.channels["a"].confirmed)
                self.assertTrue(all(d.write_count == 0 for d in self.devices))
                await pilot.press("q")
        finally:
            gate.set()

    async def test_read_only_pane_cannot_open_or_obtain_tx(self):
        app = self.app_type(self.connect, channels=["a", "b"])
        async with app.run_test(size=(140, 42)) as pilot:
            await self.ready(app)
            await pilot.press("2", "o")
            await eventually(lambda: not app._uart_busy)
            self.assertIn("沒有有效", app._uart_notice)
            self.assertTrue(app.query_one("#reopen").disabled)
            self.assertIsNone(self.gateway.channels["b"].lease.owner)
            self.assertEqual(self.devices[1].write_count, 0)
            await pilot.press("q")

    async def test_dual_large_replay_does_not_overflow_or_mix_with_operating_area(self):
        payload = b"\x1b[32mboot replay\x1b[0m\r\n" * 32000
        for cid, device in zip(("a", "b"), self.devices):
            for start in range(0, len(payload), 4096):
                device.inject(payload[start:start + 4096])
                await eventually(lambda: self.gateway.channels[cid].offset == min(start + 4096, len(payload)))
        await eventually(lambda: all(c.offset == len(payload) for c in self.gateway.channels.values()), timeout=8)
        app = self.app_type(self.connect, channels=["a", "b"])
        async with app.run_test(size=(140, 42)) as pilot:
            await eventually(lambda: len(app.controller.panes) == 2 and
                all(p.rx_bytes == len(payload) for p in app.controller.panes.values()), timeout=20)
            for cid in ("a", "b"):
                self.assertTrue(app.controller.active(cid))
                self.assertFalse(app.controller.panes[cid].error)
                self.assertLessEqual(len(app.controller.panes[cid].terminal.screen.history.top), 300)
            self.devices[1].inject(b"live-after-replay\r\n")
            await eventually(lambda: app.controller.panes["b"].rx_bytes > len(payload))
            await pilot.press("r", "q")

    async def test_four_channels_fit_existing_gateway_limits(self):
        await self.gateway.close()
        ids = ("a", "b", "c", "d")
        self.devices = [FakeSerial(echo=False) for _ in ids]
        self.gateway = Gateway([(ChannelConfig(cid, cid), device) for cid, device in zip(ids, self.devices)],
            (Principal("reader", self.token, {cid: frozenset({"read"}) for cid in ids}),))
        self.port = await self.gateway.start(port=0)
        app = self.app_type(self.connect, channels=list(ids))
        async with app.run_test(size=(150, 48)) as pilot:
            await eventually(lambda: all(c.subscriptions for c in self.gateway.channels.values()), timeout=4)
            for cid, device in zip(ids, self.devices):
                device.inject((cid + "-independent\r\n").encode())
            await eventually(lambda: len(app.controller.panes) == 4 and all(p.rx_bytes for p in app.controller.panes.values()))
            await pilot.pause()
            from uart_gateway.tui_widgets import UARTPane
            positions = {(p.region.x, p.region.y) for p in app.query(UARTPane)}
            self.assertEqual(len(positions), 4)
            self.assertLessEqual(len(self.gateway.sessions), 4)
            await pilot.press("4", "q")
        self.assertTrue(self.gateway.listener.is_serving())

    async def test_instance_change_requires_explicit_reselection(self):
        app = self.app_type(self.connect, channels=["a", "b"])
        async with app.run_test(size=(140, 42)) as pilot:
            await self.ready(app)
            self.devices[0].inject(b"old-instance\n")
            await eventually(lambda: app.controller.panes["a"].rx_bytes > 0)
            await self.gateway.close()
            self.gateway = Gateway([(ChannelConfig(cid, cid), d) for cid, d in zip(("a", "b"), self.devices)],
                (Principal("reader", self.token, {cid: frozenset({"read"}) for cid in ("a", "b")}),))
            self.port = await self.gateway.start(port=0)
            await pilot.press("r")
            await eventually(lambda: not app.controller.valid)
            self.assertFalse(any(c.subscriptions for c in self.gateway.channels.values()))
            self.assertEqual(app.controller.panes["a"].receiving, "舊 Gateway 畫面")
            self.assertEqual(app.controller.panes["a"].snapshot["state"], "unavailable")
            await pilot.press("c")
            from uart_gateway.tui_widgets import ChannelPicker
            await eventually(lambda: isinstance(app.screen, ChannelPicker))
            await pilot.press("enter")
            await self.ready(app)
            self.assertTrue(app.controller.valid)
            self.assertEqual(app.controller.panes["a"].rx_bytes, 0)
            await pilot.press("q")

    async def test_cancel_during_local_start_waits_for_launcher_cleanup(self):
        started, cleaned = asyncio.Event(), asyncio.Event()
        async def launch(monitor, report):
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.set()
        async def preview():
            return "測試本機設定；未操作實體 UART。"
        app = self.app_type(local_launch=launch, local_preview=preview)
        async with app.run_test(size=(120, 36)) as pilot:
            from uart_gateway.tui_widgets import ConfirmDialog
            await eventually(lambda: isinstance(app.screen, ConfirmDialog))
            await pilot.press("enter")
            await asyncio.wait_for(started.wait(), 2)
            await pilot.press("q")
        self.assertTrue(cleaned.is_set())
        self.assertIsNone(app.failure)
        self.assertTrue(self.gateway.listener.is_serving())

    async def test_local_cleanup_failure_is_preserved_for_workbench(self):
        from uart_gateway.local_launcher import LauncherCleanupError
        from uart_gateway.remote_workbench import query_channels
        instance, channels = await query_channels(self.connect)
        async def launch(monitor, report):
            try:
                return await monitor(self.connect, channels, instance)
            finally:
                raise LauncherCleanupError("測試：無法確認子程序已結束。")
        async def preview():
            return "測試本機設定"
        app = self.app_type(local_launch=launch, local_preview=preview)
        async with app.run_test(size=(120, 36)) as pilot:
            from uart_gateway.tui_widgets import ConfirmDialog
            await eventually(lambda: isinstance(app.screen, ConfirmDialog))
            await pilot.press("enter")
            await self.ready(app)
            await pilot.press("q")
        self.assertIsInstance(app.failure, LauncherCleanupError)
        self.assertEqual(app.return_value, 1)
        self.assertTrue(self.gateway.listener.is_serving())

    async def test_local_picker_and_record_toggle_do_not_start_gateway(self):
        from unittest.mock import AsyncMock
        from uart_gateway.core.models import AdapterInfo
        from uart_gateway.serial_backend import AdapterDetails
        from uart_gateway.tui_widgets import ConfirmDialog, DevicePicker
        from textual.widgets import SelectionList
        devices = [AdapterDetails(AdapterInfo(port="COM3"), "模擬裝置")]
        preview = AsyncMock(return_value="本機 UART：COM3；錄製：關閉。")
        launch, select, record = AsyncMock(), AsyncMock(), AsyncMock()
        app = self.app_type(local_launch=launch, local_preview=preview,
            local_devices=AsyncMock(return_value=(devices, set())), local_select=select, local_record=record)
        async with app.run_test(size=(120, 36)) as pilot:
            await eventually(lambda: isinstance(app.screen, ConfirmDialog))
            await pilot.click("#devices")
            await eventually(lambda: isinstance(app.screen, DevicePicker))
            self.assertEqual(app.screen.query_one(SelectionList).selected, [])
            await pilot.press("escape")
            await eventually(lambda: isinstance(app.screen, ConfirmDialog))
            select.assert_not_called()
            await pilot.click("#devices")
            await eventually(lambda: isinstance(app.screen, DevicePicker))
            await pilot.press("space")
            await pilot.click("#apply")
            await eventually(lambda: isinstance(app.screen, ConfirmDialog))
            select.assert_awaited_once_with(devices)
            await pilot.click("#record")
            await eventually(lambda: record.await_count == 1)
            await pilot.press("q")
        launch.assert_not_called()
