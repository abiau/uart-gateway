"""錄製清單預覽的有界性、收尾提示與選檔保護。"""

import unittest
from unittest.mock import patch

from uart_gateway.recording_catalog import preview_capture
from tests.unit import test_recording_text as fixtures
from tests.unit import test_workbench as workbench


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ExportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_source_time_epochs_and_footer_are_preview_only(self):
        f = self.fixture
        f.segment(1, [{"type": "channel_state", "channel": {"state": "ready", "stream_epoch": "epoch",
                        "adapter": {"port": "COM3"}}}, f.rx(b"one"), f.rx(b"two", epoch="second")])
        result = preview_capture(f.capture)
        self.assertEqual(result.epochs, 2)
        self.assertTrue(result.complete_scan)
        self.assertTrue(result.footer)
        self.assertIn("主控台", result.source)
        self.assertIn("COM3", result.source)
        self.assertIn("未驗證", result.description)
        # 檔案內容改壞但保留 Footer，預覽不得宣稱完整性通過。
        path = f.capture / "000001.jsonl"
        path.write_bytes(path.read_bytes().replace("主控台".encode(), "其他台".encode()))
        self.assertIn("未驗證", preview_capture(f.capture).description)

    def test_active_truncated_and_limited_files_are_not_claimed_complete(self):
        f = self.fixture
        f.segment(1, [f.rx(b"first"), f.rx(b"second", epoch="next")])
        with patch("uart_gateway.recording_catalog.PREVIEW_BYTES", 350):
            result = preview_capture(f.capture)
        self.assertFalse(result.complete_scan)
        self.assertTrue(result.footer)
        self.assertIn("至少", result.description)
        path = f.capture / "000001.jsonl"
        path.write_bytes(path.read_bytes().rsplit(b'\n', 2)[0] + b"\n")
        self.assertFalse(preview_capture(f.capture).footer)
        with patch("uart_gateway.recording_catalog.PREVIEW_FILES", 0):
            self.assertFalse(preview_capture(f.capture).complete_scan)

    def test_invalid_inputs_do_not_crash_catalog(self):
        for raw in (b"invalid", b"[]\n", b"null\n", b'{"type":"segment_start","channel":null}\n'):
            (self.fixture.capture / "000001.jsonl").write_bytes(raw)
            self.assertFalse(preview_capture(self.fixture.capture).complete_scan)


class CatalogMenuTests(unittest.TestCase):
    def setUp(self):
        self.menu = workbench.WorkbenchTests()
        self.menu.setUp()
        self.addCleanup(self.menu.doCleanups)

    def test_unclosed_requires_explicit_choice_for_play_and_export(self):
        self.menu.capture()
        _, dispatch, messages, _ = self.menu.run_menu(["5", "1", "v", "n", "t", "", "q", "q"])
        dispatch.assert_not_called()
        self.assertIn("可能仍在寫入", "\n".join(messages))

    def test_filter_does_not_auto_choose_first_capture(self):
        self.menu.capture()
        _, dispatch, messages, _ = self.menu.run_menu(["5", "c", "u", "q", "q"])
        dispatch.assert_not_called()
        self.assertIn("未收尾", "\n".join(messages))

    def test_config_entry_uses_structured_path_and_recording_override(self):
        _, dispatch, _, _ = self.menu.run_menu(["6", "m", "custom config.toml", "y", "q"])
        self.assertEqual(dispatch.call_args.args[0], ["start", "--config", "custom config.toml",
                                                    "--record-dir", str(self.menu.root)])
