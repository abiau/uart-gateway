"""離線文字匯出：跨分段解碼、失敗與原始檔保護。"""

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from uart_gateway.protocol.codec import binary_encode
from uart_gateway.recording_text import export_text
from uart_gateway.server.recording import encode_record


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.capture = self.root / "capture"
        self.capture.mkdir()
        self.output = self.root / "readable.txt"

    def segment(self, number, rows, *, final=True):
        header = {"type": "segment_start", "recording_format_version": 1, "segment": number,
                  "capture_id": "capture:a", "gateway_instance_id": "instance", "channel_id": "a",
                  "channel": {"label": "主控台"}}
        raw = b"".join(encode_record(row) for row in [header] + rows)
        raw += encode_record({"type": "segment_end", "sha256": hashlib.sha256(raw).hexdigest(),
                             "final": final, "integrity": "no_known_gap"})
        (self.capture / f"{number:06d}.jsonl").write_bytes(raw)

    def rx(self, payload, *, offset=0, seq=0, epoch="epoch"):
        return {"type": "rx", "timestamp_utc": "2026-11-06T01:02:03.000Z", "stream_epoch": epoch,
                "binding_revision": "1", "offset": str(offset), "next_offset": str(offset + len(payload)),
                "seq": str(seq), "data_b64": binary_encode(payload)}

    def test_utf8_spans_segments_and_source_bytes_are_unchanged(self):
        payload = "開".encode()
        self.segment(1, [self.rx(payload[:2])], final=False)
        self.segment(2, [self.rx(payload[2:] + b"\r\n\xff", offset=2, seq=1)])
        before = [p.read_bytes() for p in sorted(self.capture.glob("*.jsonl"))]
        result = export_text(self.capture, self.output)
        text = self.output.read_text()
        self.assertEqual(result["integrity"], "no_known_gap")
        self.assertIn("開\\r", text)
        self.assertIn("\\xff", text)
        self.assertIn("含前區塊續字元", text)
        self.assertIn("原區塊=2..6", text)
        self.assertIn("2026-11-06T01:02:03.000Z", text)
        self.assertEqual(before, [p.read_bytes() for p in sorted(self.capture.glob("*.jsonl"))])

    def test_control_characters_cannot_execute_in_exported_text(self):
        self.segment(1, [self.rx(b"boot\x1b[31m\x00\b\r\n\tend")])
        export_text(self.capture, self.output)
        text = self.output.read_text()
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\x00", text)
        self.assertIn("\\x1b[31m", text)
        self.assertIn("\\x08", text)
        self.assertIn("Epoch=epoch", text)

    def test_gap_does_not_join_partial_character(self):
        self.segment(1, [self.rx(b"\xe9\x96"), self.rx(b"\x8b", offset=4, seq=1)])
        result = export_text(self.capture, self.output)
        text = self.output.read_text()
        self.assertEqual(result["integrity"], "incomplete")
        self.assertNotIn("開", text)
        self.assertIn("\\xe9\\x96", text)
        self.assertIn("資料不完整", text)

    def test_epoch_change_flushes_partial_character(self):
        self.segment(1, [self.rx(b"\xe9\x96"), self.rx(b"\x8b", epoch="second")])
        export_text(self.capture, self.output)
        text = self.output.read_text()
        self.assertNotIn("開", text)
        self.assertIn("未完成字元", text)
        self.assertIn("Epoch=second", text)

    def test_corrupt_and_unfinished_recordings_are_exported_with_warning(self):
        self.segment(1, [self.rx(b"recoverable")])
        with (self.capture / "000001.jsonl").open("ab") as file:
            file.write(b"{unfinished")
        result = export_text(self.capture, self.output)
        self.assertNotEqual(result["integrity"], "no_known_gap")
        self.assertIn("recoverable", self.output.read_text())
        self.assertIn("連續性未知", self.output.read_text())

    def test_existing_output_and_recording_paths_are_protected(self):
        self.segment(1, [self.rx(b"boot")])
        self.output.write_text("existing")
        with self.assertRaisesRegex(ValueError, "已存在"):
            export_text(self.capture, self.output)
        self.assertEqual(self.output.read_text(), "existing")
        with self.assertRaisesRegex(ValueError, "jsonl|混淆"):
            export_text(self.capture, self.capture / "000002.jsonl")
        self.assertFalse((self.capture / "000002.jsonl").exists())

    def test_output_failure_is_not_mislabeled_as_corrupt_input(self):
        self.segment(1, [self.rx(b"boot")])
        fake = Mock()
        fake.__enter__ = Mock(return_value=fake)
        fake.__exit__ = Mock(return_value=False)
        def write(text):
            if "boot" in text:
                raise OSError("disk full")
            return len(text)
        fake.write.side_effect = write
        original = Path.open
        def open_file(path, *args, **kwargs):
            return fake if path == self.output else original(path, *args, **kwargs)
        with patch.object(Path, "open", new=open_file):
            with self.assertRaisesRegex(ValueError, "文字輸出失敗"):
                export_text(self.capture, self.output)

    def test_real_cli_needs_no_token_and_returns_0_or_2(self):
        self.segment(1, [self.rx("開機\n".encode())])
        env = os.environ.copy()
        env.pop("UART_GATEWAY_TOKEN", None)
        command = [sys.executable, "-m", "uart_gateway.cli", "recording-text", str(self.capture), "--output"]
        result = subprocess.run(command + [str(self.output)], env=env, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("已匯出文字", result.stdout)
        with (self.capture / "000001.jsonl").open("ab") as file:
            file.write(b"{unfinished")
        result = subprocess.run(command + [str(self.root / "damaged.txt")], env=env, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 2, result.stderr)
