"""只讀錄製的容量、故障與離線完整性；不操作實體 UART。"""

import asyncio
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from uart_gateway.protocol.codec import binary_encode
from uart_gateway.recording_info import inspect_capture
from uart_gateway.server.recording import Recorder, RecordingConfig
from tests.fakes import eventually


def snapshot(state="ready"):
    return {"channel_id": "a", "state": state, "label": "測試", "stream_epoch": "epoch"}


def rx(offset=0, payload=b"raw\x00\xff\x1b[31m\r\n", epoch="epoch", seq=0):
    return {"timestamp_utc": "2026-11-05T00:00:00.000Z", "binding_revision": "1",
            "stream_epoch": epoch, "seq": str(seq), "offset": str(offset),
            "next_offset": str(offset + len(payload)), "data_b64": binary_encode(payload)}


class ConfigTests(unittest.TestCase):
    def test_invalid_settings_and_path_fail_before_disk_access(self):
        for kwargs in ({"directory": Path("relative")}, {"queue_bytes": True}, {"segment_bytes": 65535},
                       {"max_capture_bytes": 65536, "segment_bytes": 131072}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                RecordingConfig(**({"directory": Path(tempfile.gettempdir())} | kwargs))


class RecorderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.changes = []
        self.recorder = Recorder(RecordingConfig(self.root), "instance", "a", self.changes.append)

    async def asyncTearDown(self):
        await self.recorder.close()
        self.temporary.cleanup()

    async def test_binary_roundtrip_clean_footer_and_repeated_close(self):
        await self.recorder.start(snapshot())
        self.recorder.rx(rx())
        await self.recorder.close()
        await self.recorder.close()
        info = inspect_capture(self.recorder.directory)
        self.assertEqual(info["integrity"], "no_known_gap")
        self.assertTrue(info["closed_cleanly"])
        self.assertEqual(info["rx_bytes"], 12)
        rows = [json.loads(line) for line in (self.recorder.directory / "000001.jsonl").read_bytes().splitlines()]
        self.assertEqual(rows[1]["data_b64"], rx()["data_b64"])
        self.assertEqual(self.recorder.used, 0)

    async def test_segment_rotation_and_total_capacity_fail_instead_of_overwriting(self):
        self.recorder = Recorder(RecordingConfig(self.root, segment_bytes=65536, max_capture_bytes=131072),
                                 "rotation", "a", self.changes.append)
        await self.recorder.start(snapshot())
        payload = b"x" * 4096
        for number in range(32):
            self.recorder.rx(rx(number * len(payload), payload, seq=number))
            await eventually(lambda: self.recorder.used == 0)
        await self.recorder.close()
        files = sorted(self.recorder.directory.glob("*.jsonl"))
        self.assertGreaterEqual(len(files), 2)
        self.assertTrue(all(path.stat().st_size <= 65536 for path in files))
        self.assertLessEqual(sum(path.stat().st_size for path in files), 131072)
        self.assertEqual(self.recorder.health, {"state": "failed", "integrity": "incomplete"})
        self.assertEqual(self.changes[-1]["reason_code"], "RECORDING_IO_FAILED")
        self.assertNotEqual(inspect_capture(self.recorder.directory)["integrity"], "no_known_gap")

    async def test_overflow_includes_inflight_bytes_and_writes_incomplete_footer(self):
        entered, release = threading.Event(), threading.Event()
        original = self.recorder._write

        def slow(raw, snap):
            entered.set()
            if not release.wait(3):
                raise OSError("測試未釋放磁碟")
            original(raw, snap)

        await self.recorder.start(snapshot())
        with patch.object(self.recorder, "_write", side_effect=slow):
            try:
                self.recorder.rx(rx())
                await eventually(entered.is_set)
                self.assertGreater(self.recorder.used, 0)
                for index in range(2000):
                    self.recorder.rx(rx(index * 4096, b"x" * 4096, seq=index))
                self.assertLessEqual(self.recorder.used, self.recorder.config.queue_bytes)
                self.assertLessEqual(self.recorder.queue.qsize(), 1024)
                self.assertEqual(self.changes[-1]["reason_code"], "RECORDING_OVERFLOW")
            finally:
                release.set()
                await self.recorder.close()
        self.assertEqual(inspect_capture(self.recorder.directory)["integrity"], "incomplete")

    async def test_io_and_short_write_failures_are_visible_without_raising_to_producer(self):
        await self.recorder.start(snapshot())
        with patch.object(self.recorder, "_write", side_effect=OSError("模擬磁碟已滿")):
            self.recorder.rx(rx())
            await eventually(lambda: self.recorder.state == "failed")
        self.assertEqual(self.changes[-1]["reason_code"], "RECORDING_IO_FAILED")
        await self.recorder.close()
        self.assertIsNone(self.recorder.file)
        self.assertNotEqual(inspect_capture(self.recorder.directory)["integrity"], "no_known_gap")

    async def test_missing_directory_permissions_report_failed(self):
        with patch.object(self.recorder, "_start", side_effect=PermissionError):
            await self.recorder.start(snapshot())
        self.assertEqual(self.recorder.state, "failed")
        self.assertIsNone(self.recorder.task)

    async def test_epochs_reset_cursor_but_known_gap_and_device_loss_stay_visible(self):
        await self.recorder.start(snapshot())
        self.recorder.rx(rx(0, b"a"))
        self.recorder.rx(rx(0, b"b", epoch="new"))
        self.recorder.rx(rx(4, b"c", epoch="new", seq=1))
        self.recorder.channel_state(snapshot("lost"))
        await self.recorder.close()
        result = inspect_capture(self.recorder.directory)
        self.assertEqual(result["integrity"], "incomplete")
        self.assertTrue(result["closed_cleanly"])
        self.assertEqual(result["rx_bytes"], 3)

    async def test_device_loss_is_unknown_even_if_file_closes_normally(self):
        await self.recorder.start(snapshot())
        self.recorder.rx(rx())
        self.recorder.channel_state(snapshot("lost"))
        await self.recorder.close()
        self.assertEqual(inspect_capture(self.recorder.directory)["integrity"], "unknown")

    async def test_partial_line_missing_footer_and_modified_payload_are_never_clean(self):
        await self.recorder.start(snapshot())
        self.recorder.rx(rx())
        await self.recorder.close()
        path = self.recorder.directory / "000001.jsonl"
        raw = path.read_bytes()
        for damaged in (raw[:raw.rfind(b'{"type":"segment_end"')], raw[:-12],
                        raw.replace(b'"seq":"0"', b'"seq":"1"'), raw + b'{unfinished'):
            path.write_bytes(damaged)
            result = inspect_capture(self.recorder.directory)
            self.assertNotEqual(result["integrity"], "no_known_gap")
            self.assertFalse(result["closed_cleanly"])
        path.write_bytes(raw)

    async def test_short_write_does_not_retry_unknown_partial_data(self):
        from unittest.mock import Mock
        file = Mock()
        file.write.return_value = 2
        self.recorder.file = file
        with self.assertRaises(OSError):
            self.recorder._raw_write(b"1234")
        file.write.assert_called_once_with(b"1234")
        self.recorder.file = None

    async def test_clean_rotation_keeps_offsets_hashes_and_binary_count(self):
        self.recorder = Recorder(RecordingConfig(self.root, segment_bytes=65536), "clean-rotation", "a", self.changes.append)
        await self.recorder.start(snapshot())
        for number in range(20):
            self.recorder.rx(rx(number * 4096, b"\xff" * 4096, seq=number))
            await eventually(lambda: self.recorder.used == 0)
        await self.recorder.close()
        result = inspect_capture(self.recorder.directory)
        self.assertGreater(result["segments"], 1)
        self.assertEqual(result["integrity"], "no_known_gap")
        self.assertEqual(result["rx_bytes"], 20 * 4096)

    async def test_fsync_failure_is_reported_and_normal_footer_is_removed_if_possible(self):
        await self.recorder.start(snapshot())
        self.recorder.rx(rx())
        with patch("uart_gateway.server.recording.os.fsync", side_effect=OSError("模擬刷新失敗")):
            await self.recorder.close()
        self.assertEqual(self.recorder.state, "failed")
        result = inspect_capture(self.recorder.directory)
        self.assertFalse(result["closed_cleanly"])
        self.assertNotEqual(result["integrity"], "no_known_gap")
