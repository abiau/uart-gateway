"""離線 Epoch 摘要；使用合成錄製，不操作 UART 或使用者錄製檔。"""

import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from uart_gateway.recording_info import EpochSummary, inspect_capture, inspect_capture_summary
from uart_gateway.server.recording import encode_record
from tests.unit import test_recording_text as fixtures


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ExportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.capture = self.fixture.capture
        self.rx = self.fixture.rx
        self.segment = self.fixture.segment

    @staticmethod
    def state(state, epoch=None):
        return {"type": "channel_state", "timestamp_utc": "2026-11-07T00:00:00.000Z",
                "channel": {"state": state, "stream_epoch": epoch}}

    def reopened(self):
        old = [self.rx(b"x" * 34, offset=i * 34, seq=i, epoch="before") for i in range(91)]
        new = [self.rx(b"y" * 34, offset=i * 34, seq=i, epoch="after") for i in range(50)]
        self.segment(1, [self.state("ready", "before"), *old, self.state("lost"),
            self.state("draining"), self.state("closed"), self.state("opening"),
            self.state("ready", "after"), *new, self.state("closed")])

    def command(self):
        env = os.environ.copy()
        env.pop("UART_GATEWAY_TOKEN", None)
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run([sys.executable, "-m", "uart_gateway.cli", "recording-info", str(self.capture)],
                              env=env, capture_output=True, encoding="utf-8", timeout=5)

    def test_reopened_epochs_match_observed_totals_and_keep_loss_unknown(self):
        self.reopened()
        before = (self.capture / "000001.jsonl").read_bytes()
        result = inspect_capture_summary(self.capture)
        self.assertEqual(result["integrity"], "unknown")
        self.assertTrue(result["closed_cleanly"])
        summary = result["summary"]
        self.assertEqual(summary["epochs"], [
            {"epoch": "before", "first_offset": "0", "last_offset": "3094", "rx_chunks": 91, "rx_bytes": 3094},
            {"epoch": "after", "first_offset": "0", "last_offset": "1700", "rx_chunks": 50, "rx_bytes": 1700}])
        self.assertEqual((summary["listed_rx_chunks"], summary["listed_rx_bytes"]), (141, 4794))
        self.assertTrue(summary["totals_match"])
        self.assertEqual([e["kind"] for e in summary["boundaries"]],
                         ["epoch_started", "lost", "draining", "closed", "opening", "epoch_changed", "closed"])
        self.assertEqual(summary["boundaries"][-2]["previous_epoch"], "before")
        self.assertEqual(before, (self.capture / "000001.jsonl").read_bytes())

    def test_single_epoch_binary_bytes_and_segment_rotation_are_not_double_counted(self):
        self.segment(1, [self.rx(bytes(range(256)))], final=False)
        self.segment(2, [self.rx(b"\x00\xff", offset=256, seq=1)])
        result = inspect_capture_summary(self.capture)
        self.assertEqual(result["integrity"], "no_known_gap")
        self.assertEqual(result["summary"]["epochs"], [
            {"epoch": "epoch", "first_offset": "0", "last_offset": "258", "rx_chunks": 2, "rx_bytes": 258}])
        self.assertEqual(len(result["summary"]["boundaries"]), 1)
        self.assertTrue(result["summary"]["totals_match"])

    def test_known_gap_uses_payload_byte_count_instead_of_offset_span(self):
        self.segment(1, [self.rx(b"ab"), {"type": "gap"}, self.rx(b"c", offset=10, seq=1)])
        result = inspect_capture_summary(self.capture)
        item = result["summary"]["epochs"][0]
        self.assertEqual((item["first_offset"], item["last_offset"], item["rx_bytes"]), ("0", "11", 3))
        self.assertEqual(result["integrity"], "incomplete")
        self.assertIn("gap", [e["kind"] for e in result["summary"]["boundaries"]])
        self.assertTrue(result["summary"]["totals_match"])

    def test_missing_footer_keeps_available_stats_and_does_not_claim_clean_capture(self):
        self.segment(1, [self.rx(b"available")])
        path = self.capture / "000001.jsonl"
        path.write_bytes(b"\n".join(path.read_bytes().splitlines()[:-1]) + b"\n")
        result = inspect_capture_summary(self.capture)
        self.assertFalse(result["closed_cleanly"])
        self.assertEqual(result["integrity"], "unknown")
        self.assertEqual(result["summary"]["listed_rx_bytes"], 9)

    def test_bad_hash_is_not_hidden_by_matching_epoch_totals(self):
        self.segment(1, [self.rx(b"abc")])
        path = self.capture / "000001.jsonl"
        lines = path.read_bytes().splitlines()
        footer = json.loads(lines[-1])
        footer["sha256"] = "0" * 64
        path.write_bytes(b"\n".join(lines[:-1]) + b"\n" + encode_record(footer))
        result = inspect_capture_summary(self.capture)
        self.assertEqual(result["integrity"], "incomplete")
        self.assertTrue(result["summary"]["totals_match"])
        self.assertFalse(result["closed_cleanly"])

    def test_empty_capture_and_ready_epoch_without_rx(self):
        self.segment(1, [])
        result = inspect_capture_summary(self.capture)
        self.assertEqual(result["summary"]["epochs"], [])
        self.assertTrue(result["summary"]["totals_match"])
        self.segment(1, [self.state("ready", "silent"), self.state("closed")])
        result = inspect_capture_summary(self.capture)
        self.assertEqual(result["summary"]["epochs"], [
            {"epoch": "silent", "first_offset": None, "last_offset": None, "rx_chunks": 0, "rx_bytes": 0}])

    def test_summary_limits_preserve_full_scan_and_reconcile_omitted_rx(self):
        self.segment(1, [self.rx(b"a", epoch="a"), self.rx(b"bb", epoch="b"),
                         self.rx(b"ccc", epoch="c"), self.rx(b"dddd", epoch="a")])
        summary = EpochSummary(max_epochs=2, max_boundaries=2)
        inspected = inspect_capture(self.capture, on_record=summary.visit)
        result = summary.result(inspected)
        self.assertEqual(inspected["rx_bytes"], 10)
        self.assertEqual((len(result["epochs"]), len(result["boundaries"])), (2, 2))
        self.assertTrue(result["epochs_truncated"])
        self.assertTrue(result["boundaries_truncated"])
        self.assertEqual((result["omitted_rx_chunks"], result["omitted_rx_bytes"]), (1, 3))
        self.assertTrue(result["totals_match"])

    def test_oversized_epoch_is_not_retained_in_summary_but_totals_remain_accounted(self):
        self.segment(1, [self.rx(b"a", epoch="x" * 1000)])
        result = inspect_capture_summary(self.capture)
        self.assertTrue(result["summary"]["epochs_truncated"])
        self.assertFalse(result["summary"]["epochs"])
        self.assertEqual(result["summary"]["omitted_rx_bytes"], 1)
        self.assertTrue(result["summary"]["totals_match"])

    def test_cli_without_token_prints_native_epoch_totals_and_preserves_exit_two_for_loss(self):
        self.reopened()
        result = self.command()
        self.assertEqual(result.returncode, 2, result.stderr)
        for expected in ("連續性未知", "正常收尾：是", "Epoch=before", "Epoch=after", "RX 區塊：91", "RX bytes：3094",
                         "RX 區塊：141；RX bytes：4794", "裝置遺失", "世代切換", "整份 RX 統計核對：一致"):
            self.assertIn(expected, result.stdout)

    def test_cli_healthy_and_invalid_input_exit_codes(self):
        self.segment(1, [self.rx(b"abc")])
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("無已知缺口", result.stdout)
        (self.capture / "000001.jsonl").unlink()
        result = self.command()
        self.assertEqual(result.returncode, 1)

    def test_cli_escapes_metadata_controls_and_reports_summary_truncation(self):
        self.segment(1, [self.rx(b"abc", epoch="bad\x1b[31m\nlabel")])
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("\x1b", result.stdout)
        self.assertIn("\\x1b", result.stdout)
        self.assertIn("\\nlabel", result.stdout)
        self.segment(1, [self.rx(b"x", epoch=f"epoch-{i}") for i in range(1025)])
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("未列 RX 區塊：1；未列 RX bytes：1", result.stdout)
        self.assertIn("256 筆上限", result.stdout)
        self.assertIn("整份 RX 統計核對：一致", result.stdout)

    def test_inspection_wrapper_keeps_legacy_result_and_checks_total_mismatch(self):
        self.segment(1, [self.rx(b"abc")])
        legacy = inspect_capture(self.capture)
        result = inspect_capture_summary(self.capture)
        self.assertEqual({k: v for k, v in result.items() if k != "summary"}, legacy)
        with patch.object(EpochSummary, "visit"):
            result = inspect_capture_summary(self.capture)
        self.assertFalse(result["summary"]["totals_match"])
        self.assertEqual(result["integrity"], "incomplete")
