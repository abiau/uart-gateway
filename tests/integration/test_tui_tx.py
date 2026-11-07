"""TX 多窗格的實際 UI 操作；使用真 TCP 與 Fake Serial。"""

import asyncio
import importlib.util
import threading
import unittest

from tests.fakes import eventually
from tests.integration import test_tx_workflow as fixture

HAS_TUI = all(importlib.util.find_spec(name) is not None for name in ("textual", "pyte"))


@unittest.skipUnless(HAS_TUI, "需要可選 [tui]；Win11 實機另測")
class TUITxTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.TxWorkflowTests.asyncSetUp
    asyncTearDown = fixture.TxWorkflowTests.asyncTearDown
    connect = fixture.TxWorkflowTests.connect

    def app(self):
        from uart_gateway.tui_app import UARTApp
        return UARTApp(self.connect, channels=["a", "b"], tx_journal=self.workflow.journal)

    async def ready(self, app):
        await eventually(lambda: all(c.subscriptions for c in self.gateway.channels.values()))

    async def wait_for_control(self, app, dialog, selector):
        await eventually(lambda: isinstance(app.screen, dialog) and
                         any(widget.region.width and widget.region.height for widget in app.screen.query(selector)))

    async def compose_tx(self, app, pilot):
        from uart_gateway.tui_tx import ComposeDialog, TargetDialog
        await pilot.press("f2")
        await eventually(lambda: isinstance(app.screen, (TargetDialog, ComposeDialog)))
        if isinstance(app.screen, TargetDialog):
            await self.wait_for_control(app, TargetDialog, "#tx-target-confirm")
            self.assertTrue(app.screen.query_one("#tx-target-confirm").disabled)
            await pilot.click("#tx-ack")
            await pilot.click("#tx-target-confirm")
        await self.wait_for_control(app, ComposeDialog, "#tx-text")

    async def test_typing_qy_enter_preview_cancel_and_send_query_are_simple_and_exact(self):
        from textual.widgets import Input, Select
        from uart_gateway.tui_tx import PreviewDialog, ResultsDialog
        app = self.app()
        async with app.run_test(size=(140, 42)) as pilot:
            await self.ready(app)
            peer_epoch = self.gateway.channels["b"].epoch
            await self.compose_tx(app, pilot)
            await pilot.press("q", "y", "end")
            self.assertEqual(app.screen.query_one(Input).value, "qy")
            self.devices[1].inject(b"peer-during-compose\r\n")
            await eventually(lambda: app.controller.panes["b"].rx_bytes > 0)
            await pilot.press("enter")
            await self.wait_for_control(app, PreviewDialog, "#tx-cancel")
            self.assertEqual(app.screen.payload, b"qy")
            self.assertIsNone(self.gateway.channels["a"].lease.owner)
            await pilot.press("enter")  # 預設取消，不會以 Enter 直接傳送。
            await eventually(lambda: len(app.screen_stack) == 1)
            self.assertEqual(self.devices[0].write_count, 0)
            await self.compose_tx(app, pilot)
            app.screen.query_one(Input).value = "serial-check"
            app.screen.query_one("#tx-newline", Select).value = "crlf"
            await pilot.press("enter")
            await self.wait_for_control(app, PreviewDialog, "#tx-send-once")
            self.assertEqual(app.screen.payload, b"serial-check\r\n")
            await pilot.click("#tx-send-once")
            await self.wait_for_control(app, ResultsDialog, "#tx-query")
            self.assertEqual(self.devices[0].write_count, 1)
            self.assertEqual(list(self.devices[0].writes), [b"serial-check\r\n"])
            self.assertIsNone(self.gateway.channels["a"].lease.owner)
            await pilot.click("#tx-query")
            await eventually(lambda: isinstance(app.screen, ResultsDialog) and app.tx.journal.list()[0].last_query is not None)
            self.assertEqual(self.devices[0].write_count, 1)
            self.assertEqual(self.gateway.channels["b"].epoch, peer_epoch)
            await pilot.press("q")
        self.assertTrue(self.gateway.listener.is_serving())

    async def test_stale_preview_cannot_send_to_a_new_binding(self):
        from textual.widgets import Input
        from uart_gateway.tui_tx import PreviewDialog
        app = self.app()
        async with app.run_test(size=(140, 42)) as pilot:
            await self.ready(app)
            await self.compose_tx(app, pilot)
            app.screen.query_one(Input).value = "do-not-send"
            await pilot.press("enter")
            await self.wait_for_control(app, PreviewDialog, "#tx-send-once")
            async with await self.connect() as other:
                closed = await other.rebind(await other.get_channel("a"))
                await other.confirm(closed, "another-target")
            await pilot.click("#tx-send-once")
            await eventually(lambda: not app._uart_busy)
            self.assertIn("已改變", app._uart_notice)
            self.assertEqual(self.devices[0].write_count, 0)
            await pilot.press("q")

    async def test_ctrl_c_during_write_preserves_receipt_and_peer_rx(self):
        from textual.widgets import Input
        from uart_gateway.tui_tx import PreviewDialog
        app = self.app()
        self.devices[0].write_gate = threading.Event()
        try:
            async with app.run_test(size=(140, 42)) as pilot:
                await self.ready(app)
                await self.compose_tx(app, pilot)
                app.screen.query_one(Input).value = "inflight"
                await pilot.press("enter")
                await self.wait_for_control(app, PreviewDialog, "#tx-send-once")
                await pilot.click("#tx-send-once")
                await eventually(self.devices[0].write_entered.is_set)
                self.devices[1].inject(b"peer-during-write\n")
                await eventually(lambda: app.controller.panes["b"].rx_bytes > 0)
                await pilot.press("ctrl+c")
            self.assertIsNone(self.gateway.channels["a"].lease.owner)
            self.assertEqual(len(app.tx.journal.list()), 1)
        finally:
            self.devices[0].write_gate.set()

    async def test_read_only_tui_has_no_tx_controls_and_cannot_confirm_or_write(self):
        from uart_gateway.tui_app import UARTApp
        app = UARTApp(self.connect, channels=["a"])
        async with app.run_test(size=(120, 36)) as pilot:
            await eventually(lambda: bool(self.gateway.channels["a"].subscriptions))
            self.assertFalse(list(app.query("#tx-send")))
            await pilot.press("f2")
            self.assertEqual(len(app.screen_stack), 1)
            self.assertFalse(self.gateway.channels["a"].confirmed)
            self.assertEqual(self.devices[0].write_count, 0)
            await pilot.press("q")

    async def test_small_window_keeps_tx_buttons_visible_and_cancel_does_not_write(self):
        from textual.widgets import Checkbox, Input
        from uart_gateway.tui_tx import ComposeDialog, PreviewDialog, TargetDialog
        app = self.app()
        async with app.run_test(size=(80, 24)) as pilot:
            await self.ready(app)
            await pilot.press("f2")
            await self.wait_for_control(app, TargetDialog, "#tx-ack")
            app.screen.query_one(Checkbox).focus()
            await pilot.press("space")
            await pilot.pause()
            self.assertLessEqual(app.screen.query_one("#tx-target-confirm").region.bottom, 24)
            await pilot.click("#tx-target-confirm")
            await self.wait_for_control(app, ComposeDialog, "#tx-text")
            app.screen.query_one(Input).value = "preview-only"
            await pilot.press("enter")
            await self.wait_for_control(app, PreviewDialog, "#tx-send-once")
            self.assertLessEqual(app.screen.query_one("#tx-send-once").region.bottom, 24)
            await pilot.click("#tx-cancel")
            self.assertEqual(self.devices[0].write_count, 0)
            await pilot.press("q")
