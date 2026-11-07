"""區段回看、匯出及有界搜尋共用整份完整性掃描。"""

import io
import asyncio
import os
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

from uart_gateway.recording_play import play_capture
from uart_gateway.recording_search import SearchCancelled, search_capture
from uart_gateway.recording_selection import RXSelection
from uart_gateway.recording_text import export_text
from tests.unit import test_recording_text as fixtures
from tests.unit import test_workbench as menu_fixture


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ExportTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_timezones_inclusive_boundaries_missing_time_and_invalid_arguments(self):
        row = self.f.rx(b"test")
        exact = RXSelection(from_time="2026-11-06T09:02:03+08:00", to_time="2026-11-06T01:02:03Z")
        self.assertTrue(exact.accepts(row))
        self.assertFalse(exact.accepts(dict(row, timestamp_utc="bad")))
        self.assertFalse(exact.accepts(dict(row, timestamp_utc=None)))
        for options in ({"from_time": "2026-11-06T01:00:00"}, {"from_time": "invalid"},
                        {"from_time": "2026-11-07T00:00:00Z", "to_time": "2026-11-06T00:00:00Z"},
                        {"epoch": "\x1b[31m"}, {"epoch": "e" * 129}):
            with self.assertRaises(ValueError):
                RXSelection(**options)

    def test_filtered_play_export_keep_full_integrity_and_source_unchanged(self):
        f = self.f
        rows = [f.rx(b"outside\n"), f.rx("選取文字\n".encode(), epoch="selected")]
        f.segment(1, rows)
        path = f.capture / "000001.jsonl"
        # 範圍外的 Footer 損壞也必須被發現。
        path.write_bytes(path.read_bytes().replace(b'"sha256":"', b'"sha256":"bad'))
        before = path.read_bytes()
        selection = RXSelection(epoch="selected")
        result = export_text(f.capture, f.output, selection=selection)
        self.assertEqual(result["integrity"], "incomplete")
        self.assertEqual(result["selected_chunks"], 1)
        self.assertEqual(result["rx_chunks"], 2)
        text = f.output.read_text()
        self.assertNotIn("outside", text)
        self.assertIn("選取文字", text)
        self.assertIn("整份錄製", text)
        out, err = io.StringIO(), io.StringIO()
        result = play_capture(f.capture, terminal=True, stop=threading.Event(), stdout=out, stderr=err,
                              selection=selection)
        self.assertEqual(result["integrity"], "incomplete")
        self.assertNotIn("outside", out.getvalue())
        self.assertIn("選取文字", out.getvalue())
        self.assertIn("SHA-256", err.getvalue())
        self.assertEqual(before, path.read_bytes())

    def test_excluded_chunk_does_not_join_partial_utf8(self):
        f = self.f
        rows = [f.rx(b"\xe9\x96"), f.rx(b"skip", offset=2, seq=1), f.rx(b"\x8b", offset=6, seq=2)]
        rows[1]["timestamp_utc"] = "2026-11-06T01:03:00Z"
        f.segment(1, rows)
        selection = RXSelection(to_time="2026-11-06T01:02:03Z")
        result = export_text(f.capture, f.output, selection=selection)
        self.assertEqual(result["selected_chunks"], 2)
        self.assertNotIn("開", f.output.read_text())
        self.assertNotIn("skip", f.output.read_text())

    def test_empty_selection_still_checks_entire_capture(self):
        f = self.f
        f.segment(1, [f.rx(b"boot")])
        with (f.capture / "000001.jsonl").open("ab") as file:
            file.write(b"unfinished")
        result = export_text(f.capture, f.output, selection=RXSelection(epoch="absent"))
        self.assertEqual(result["selected_bytes"], 0)
        self.assertNotEqual(result["integrity"], "no_known_gap")

    def test_split_utf8_keyword_across_segments_and_safe_preview(self):
        f = self.f
        data = "\x1b[31m開機成功\r\n".encode()
        split = 7  # 中文 UTF-8 字元切在區塊中。
        f.segment(1, [f.rx(data[:split])], final=False)
        f.segment(2, [f.rx(data[split:], offset=split, seq=1)])
        result = search_capture(f.capture, "開機成功")
        matches = result["search"]["matches"]
        self.assertEqual(len(matches), 1)
        self.assertEqual((matches[0]["offset"], matches[0]["next_offset"]), (5, 17))
        self.assertNotIn("\x1b", matches[0]["preview"])
        self.assertEqual(result["integrity"], "no_known_gap")

    def test_search_does_not_cross_epoch_gap_or_excluded_chunks(self):
        f = self.f
        f.segment(1, [f.rx(b"ab"), f.rx(b"cd", epoch="second")])
        self.assertEqual(search_capture(f.capture, "abcd")["search"]["matches"], [])
        f.segment(1, [f.rx(b"ab"), f.rx(b"cd", offset=4, seq=1)])
        result = search_capture(f.capture, "abcd")
        self.assertEqual(result["search"]["matches"], [])
        self.assertEqual(result["integrity"], "incomplete")
        rows = [f.rx(b"early"), f.rx(b"later", offset=5, seq=1)]
        rows[1]["timestamp_utc"] = "2026-11-06T01:04:00Z"
        f.segment(1, rows)
        selection = RXSelection(to_time="2026-11-06T01:03:00Z")
        self.assertEqual(search_capture(f.capture, "later", selection=selection)["search"]["matches"], [])
        self.assertEqual(len(search_capture(f.capture, "early", selection=selection)["search"]["matches"]), 1)

    def test_nonoverlapping_matches_limit_does_not_skip_corrupt_tail(self):
        f = self.f
        f.segment(1, [f.rx(b"aaaa"), f.rx(b"aaa", offset=4, seq=1)])
        result = search_capture(f.capture, "aa")
        self.assertEqual([m["offset"] for m in result["search"]["matches"]], [0, 2, 4])
        with (f.capture / "000001.jsonl").open("ab") as file:
            file.write(b"bad")
        result = search_capture(f.capture, "a", max_results=1)
        self.assertTrue(result["search"]["limited"])
        self.assertEqual(len(result["search"]["matches"]), 1)
        self.assertNotEqual(result["integrity"], "no_known_gap")

    def test_search_validation_and_cancellation(self):
        f = self.f
        f.segment(1, [f.rx(b"test")])
        for keyword in ("", "a" * 4097, "\x1b", "a\nb"):
            with self.assertRaises(ValueError):
                search_capture(f.capture, keyword)
        for limit in (0, 1001):
            with self.assertRaises(ValueError):
                search_capture(f.capture, "a", max_results=limit)
        stop = threading.Event()
        stop.set()
        with self.assertRaises(SearchCancelled):
            search_capture(f.capture, "test", stop=stop)

    def test_search_result_metadata_is_bounded_even_for_untrusted_records(self):
        f = self.f
        row = f.rx(b"boot", epoch="e" * 8000)
        row["timestamp_utc"] = "t" * 8000
        f.segment(1, [row])
        match = search_capture(f.capture, "boot")["search"]["matches"][0]
        self.assertLessEqual(len(match["epoch"]), 129)
        self.assertLessEqual(len(match["timestamp_utc"]), 129)
        self.assertTrue(match["epoch"].endswith("…"))

    def test_real_cli_without_token_filtered_export_and_search(self):
        f = self.f
        f.segment(1, [f.rx("開機\n".encode())])
        env = dict(os.environ)
        env.pop("UART_GATEWAY_TOKEN", None)
        for args in (["recording-search", str(f.capture), "--keyword", "開機", "--epoch", "epoch"],
                     ["recording-text", str(f.capture), "--output", str(f.output),
                      "--from-time", "2026-11-06T09:02:03+08:00"]):
            result = subprocess.run([sys.executable, "-m", "uart_gateway.cli", *args], env=env,
                                    text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("開機", f.output.read_text())


class SelectionMenuTests(unittest.TestCase):
    def test_filter_search_export_and_clear_route_structured_arguments(self):
        menu = menu_fixture.WorkbenchTests()
        menu.setUp()
        self.addCleanup(menu.doCleanups)
        selected = menu.capture()
        destination = menu.root / "part.txt"
        _, dispatch, _, _ = menu.run_menu(["5", "1", "f", "new-epoch", "", "", "s", "--help", "y",
                                          "t", "y", str(destination), "f", "", "", "", "i", "q", "q"])
        calls = [call.args[0] for call in dispatch.call_args_list]
        self.assertEqual(calls[0], ["recording-search", str(selected), "--keyword=--help", "--epoch=new-epoch"])
        self.assertEqual(calls[1][-1], "--epoch=new-epoch")
        self.assertEqual(calls[2], ["recording-info", str(selected)])


class SearchCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_waits_for_worker_and_never_reports_completed_inspection(self):
        from uart_gateway.recording_search import search
        started, finished = threading.Event(), threading.Event()

        def scan(directory, *, on_record):
            started.set()
            try:
                while True:
                    on_record(None)
                    threading.Event().wait(0.001)
            finally:
                finished.set()

        with patch("uart_gateway.recording_search.inspect_capture", scan):
            task = asyncio.create_task(search(None, "boot", selection=RXSelection()))
            async with asyncio.timeout(3):
                while not started.is_set():
                    await asyncio.sleep(0.001)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertTrue(finished.is_set())
