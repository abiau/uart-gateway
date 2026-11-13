"""Fake UART＋真 TCP；不依賴硬體、Linux 或外部測試套件。"""

from datetime import datetime
import io
import json
import os
from pathlib import Path
import queue
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import client
from client import CompactMonitor, FullMonitor, monitor_batches
from protocol import (MAX_FRAME, ShellMarkers, b64, decode, encode, markers, read_message,
                      shell_payload, unb64, validate_exec)
from server import Gateway, TCPServer, TrafficLog

POSIX_SH = shutil.which("sh")
if not POSIX_SH and os.name == "nt":
    candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/sh.exe"
    if candidate.is_file():
        POSIX_SH = str(candidate)


class FakeSerial:
    port = "FAKE"
    baudrate = 115200
    in_waiting = 0

    def __init__(self):
        self.incoming = queue.Queue()
        self.writes = []
        self.short_write = False
        self.closed = False
        self.auto_reply = None

    def read(self, count):
        try:
            data = self.incoming.get(timeout=0.02)
        except queue.Empty:
            return b""
        if isinstance(data, Exception):
            raise data
        return data

    def write(self, data):
        self.writes.append(data)
        if self.auto_reply:
            command_id = re.search(rb"[0-9a-f]{32}", data)[0].decode()
            self.auto_reply(command_id)
        return len(data) - 1 if self.short_write else len(data)

    def close(self):
        self.closed = True


def framed(command_id, body=b"hello\n", code=0, newline=b"\n"):
    begin, end = markers(command_id)
    return newline + begin + newline + body + newline + end + str(code).encode() + newline


def wait_for(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("等待狀態逾時")


class BurstSerial(FakeSerial):
    """模擬 read(1) 喚醒時，其他 bytes 已同時抵達 Driver Buffer。"""

    def __init__(self):
        super().__init__()
        self.buffer = bytearray()

    @property
    def in_waiting(self):
        return len(self.buffer)

    def read(self, count):
        if not self.buffer:
            self.buffer.extend(super().read(count))
        data = bytes(self.buffer[:count])
        del self.buffer[:count]
        return data


class UARTBatchTests(unittest.TestCase):
    def start_gateway(self, serial):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        gateway = Gateway(serial, TrafficLog(Path(temp.name) / "uart.ndjson"))
        self.addCleanup(gateway.close)
        events = queue.Queue()
        gateway.monitors.add(events)
        gateway.start()
        return gateway, events

    def test_first_byte_and_available_tail_are_one_rx_event(self):
        serial = BurstSerial()
        serial.incoming.put(bytes(range(64)))
        _, events = self.start_gateway(serial)
        event = events.get(timeout=2)
        self.assertEqual(event["type"], "rx")
        self.assertEqual(unb64(event["data_b64"]), bytes(range(64)))

    def test_large_burst_has_bounded_events_and_exact_bytes(self):
        serial = BurstSerial()
        data = bytes(range(256)) * 40
        serial.incoming.put(data)
        _, events = self.start_gateway(serial)
        chunks = [unb64(events.get(timeout=2)["data_b64"]) for _ in range(3)]
        self.assertEqual(list(map(len, chunks)), [4096, 4096, 2048])
        self.assertEqual(b"".join(chunks), data)

    def test_single_byte_does_not_wait_for_another_byte(self):
        serial = BurstSerial()
        serial.incoming.put(b"#")
        _, events = self.start_gateway(serial)
        self.assertEqual(unb64(events.get(timeout=2)["data_b64"]), b"#")

    def test_second_read_failure_preserves_already_received_bytes(self):
        class FailedDrain(BurstSerial):
            def read(self, count):
                if self.buffer:
                    raise OSError("device removed during drain")
                return super().read(count)
        serial = FailedDrain()
        serial.incoming.put(b"known bytes")
        gateway, events = self.start_gateway(serial)
        first = events.get(timeout=2)
        failure = events.get(timeout=2)
        self.assertEqual((first["type"], unb64(first["data_b64"])), ("rx", b"k"))
        self.assertEqual(failure["error"], "uart_error")
        self.assertEqual(gateway.status()["uart"], "error")

    def test_echo_markers_and_prompt_survive_combined_rx(self):
        serial = BurstSerial()
        serial.auto_reply = lambda command_id: serial.incoming.put(
            serial.writes[-1] + framed(command_id, b"binary\x00\xff\r\n", 7) + b"~ # ")
        gateway, events = self.start_gateway(serial)
        execution, error = gateway.execute({"command": "test"})
        self.assertIsNone(error)
        wait_for(lambda: gateway.active is None)
        result = gateway.result(execution)
        self.assertEqual((result["state"], result["exit_code"]), ("completed", 7))
        self.assertEqual(unb64(result["output_b64"]), b"binary\x00\xff\r\n")
        rx = []
        while not events.empty():
            event = events.get_nowait()
            if event["type"] == "rx":
                rx.append(unb64(event["data_b64"]))
        self.assertEqual(len(rx), 1)
        self.assertTrue(rx[0].endswith(b"~ # "))


class MonitorBatchTests(unittest.TestCase):
    def test_delivers_available_frames_and_retains_split_next_frame(self):
        first = {"type": "rx", "data_b64": b64(b"~ # ")}
        second = {"type": "rx", "data_b64": b64("測試".encode())}
        third = {"type": "completed", "exit_code": 0}
        wire = encode(second)
        sock = Mock()
        sock.recv.side_effect = [encode(first) + wire[:12], wire[12:] + encode(third), b""]
        with patch("client.select.select", return_value=([sock], [], [])):
            batches = monitor_batches(sock)
            # Prompt 不等待下一次 recv，也不需要結尾換行。
            self.assertEqual(next(batches), [first])
            self.assertEqual(next(batches), [second, third])
            with self.assertRaises(ValueError):
                next(batches)

    def test_valid_frames_are_delivered_before_later_invalid_frame(self):
        event = {"type": "rx", "data_b64": b64(b"must preserve")}
        sock = Mock()
        sock.recv.return_value = encode(event) + b"invalid\n"
        with patch("client.select.select", return_value=([sock], [], [])):
            batches = monitor_batches(sock)
            self.assertEqual(next(batches), [event])
            with self.assertRaises(ValueError):
                next(batches)

    def test_unterminated_oversized_frame_is_rejected(self):
        wire = b"x" * (MAX_FRAME + 1)
        sock = Mock()
        sock.recv.side_effect = [wire[i:i+65536] for i in range(0, len(wire), 65536)]
        with patch("client.select.select", return_value=([sock], [], [])):
            with self.assertRaises(ValueError):
                next(monitor_batches(sock))

    def test_all_display_modes_flush_once_for_same_received_batch(self):
        class Output:
            def __init__(self):
                self.buffer = self
                self.data = bytearray()
                self.writes = self.flushes = 0
            def write(self, value):
                self.data.extend(value.encode() if isinstance(value, str) else value)
                self.writes += 1
                return len(value)
            def flush(self):
                self.flushes += 1
            def isatty(self):
                return False
        stamp = "2026-11-13T16:00:00+00:00"
        data = "測試\r\n".encode()
        batch = [{"type": "rx", "time": stamp, "data_b64": b64(bytes([byte]))} for byte in data]
        error = {"type": "error", "time": stamp, "error": "test_end"}
        for options in ([], ["--full"], ["--json"]):
            with self.subTest(options=options):
                output = Output()
                with patch("client.socket.create_connection"), patch("client.monitor_batches", return_value=iter([batch, [error]])), \
                        patch("client.sys.stdout", output), patch("client.sys.stderr", io.StringIO()):
                    self.assertEqual(client.main(["monitor", *options]), 2)
                self.assertEqual(output.flushes, 1 if not options else 2)
                self.assertEqual(output.writes, output.flushes)
                if not options:
                    self.assertEqual(output.data, data)
                elif options == ["--json"]:
                    self.assertEqual([json.loads(line) for line in output.data.splitlines()], batch + [error])
                else:
                    self.assertIn("測試\n".encode(), output.data)


class MarkerTests(unittest.TestCase):
    command_id = "a" * 32

    def test_echo_wrong_id_binary_and_every_split(self):
        payload = shell_payload("printf '%s' \"A'B\"", self.command_id)
        begin, end = markers(self.command_id)
        self.assertNotIn(begin, payload)
        self.assertNotIn(end, payload)
        body = "測試".encode() + b"\x00\xff\nno-newline"
        wire = payload + framed("b" * 32) + framed(self.command_id, body, 7) + b"prompt# "
        for size in (1, 2, 3, 7, 64, len(wire)):
            parser = ShellMarkers(self.command_id)
            for start in range(0, len(wire), size):
                parser.feed(wire[start:start + size])
            self.assertEqual((parser.done, parser.exit_code, parser.output), (True, 7, body))

    def test_crlf_no_final_newline_and_empty(self):
        for body in (b"", b"no-newline", b"line\r\n"):
            parser = ShellMarkers(self.command_id)
            parser.feed(framed(self.command_id, body, 0, b"\r\n"))
            self.assertEqual(parser.output, body)

    def test_invalid_marker_and_bounded_large_output(self):
        parser = ShellMarkers(self.command_id, 128)
        parser.feed(shell_payload("true", self.command_id))
        parser.feed(b"\n" + markers(self.command_id)[1] + b"0\n")
        self.assertFalse(parser.started)
        parser.feed(framed(self.command_id, b"x" * 100000, 999))
        self.assertFalse(parser.done)
        self.assertLessEqual(len(parser.pending), 64)
        parser.feed(b"\n" + markers(self.command_id)[1] + b"255\n")
        self.assertTrue(parser.done)
        self.assertTrue(parser.truncated)
        self.assertEqual(len(parser.output), 128)
        exact = ShellMarkers(self.command_id, 128)
        exact.feed(framed(self.command_id, b"x" * 128))
        self.assertFalse(exact.truncated)

    def test_validation(self):
        for command in ("", " ", "uname\n-a", "\x1b", "\udcff", "a" * 4096):
            with self.assertRaises((ValueError, UnicodeError)):
                shell_payload(command, self.command_id)
        for timeout in (True, 0, -1, float("inf"), float("nan"), 301):
            with self.assertRaises(ValueError):
                validate_exec({"command": "true", "timeout": timeout})
        for wire in (b"[]\n", b"{}", b'{"timeout":NaN}\n', b"x" * (MAX_FRAME + 1) + b"\n"):
            with self.assertRaises(ValueError):
                decode(wire)


@unittest.skipUnless(POSIX_SH, "需要 POSIX sh，Windows 可使用 Git for Windows 的 sh")
class CwdShellTests(unittest.TestCase):
    command_id = "a" * 32

    def run_shell(self, root, command, cwd=None):
        payload = shell_payload(command, self.command_id, "lf", cwd=cwd)
        result = subprocess.run([POSIX_SH, "-c", payload.decode()], cwd=root,
                                capture_output=True, timeout=5)
        parser = ShellMarkers(self.command_id)
        parser.feed(result.stdout)
        self.assertTrue(parser.done, (result.stdout, result.stderr))
        return parser

    def test_literal_paths_with_spaces_quotes_shell_syntax_and_leading_dash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("space dir", "quote ' ;$(printf INJECTED)", "-work"):
                with self.subTest(name=name):
                    target = root / name
                    target.mkdir()
                    (target / "marker").write_bytes(b"FOUND")
                    parser = self.run_shell(root, "cat marker; exit 7", cwd=name)
                    self.assertEqual((parser.output, parser.exit_code), (b"FOUND", 7))

    def test_missing_cwd_does_not_execute_any_part_of_command(self):
        with tempfile.TemporaryDirectory() as temp:
            parser = self.run_shell(temp, "printf MUST_NOT_RUN; printf ALSO_MUST_NOT_RUN", cwd="missing")
            self.assertNotEqual(parser.exit_code, 0)
            self.assertNotIn(b"MUST_NOT_RUN", parser.output)
            self.assertTrue(parser.output)

    def test_cwd_only_applies_to_this_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "marker").write_bytes(b"ROOT")
            (root / "child").mkdir()
            (root / "child/marker").write_bytes(b"CHILD")
            self.assertEqual(self.run_shell(root, "cat marker", cwd="child").output, b"CHILD")
            self.assertEqual(self.run_shell(root, "cat marker").output, b"ROOT")

    def test_cdpath_cannot_redirect_a_relative_cwd(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "other/target").mkdir(parents=True)
            (root / "other/target/marker").write_bytes(b"WRONG_DIRECTORY")
            # sh 自己先設置 CDPATH，避免 Windows 與 MSYS 的環境路徑轉換差異。
            payload = shell_payload("cat marker", self.command_id, "lf", cwd="target")
            result = subprocess.run([POSIX_SH, "-c", "export CDPATH=other; " + payload.decode()],
                                    cwd=root, capture_output=True, timeout=5)
            parser = ShellMarkers(self.command_id)
            parser.feed(result.stdout)
            self.assertTrue(parser.done)
            self.assertNotEqual(parser.exit_code, 0)
            self.assertNotIn(b"WRONG_DIRECTORY", parser.output)


class CompactMonitorTests(unittest.TestCase):
    command_id = "a" * 32

    def test_wrapped_echo_markers_and_extra_delimiters_hidden_at_every_split(self):
        payload = shell_payload("ls", self.command_id).replace(b"\r", b"\r\n")
        # 包裝 Echo 可折成多行，不可讓它污染精簡畫面。
        echo = b"\r\n".join(payload[i:i+60] for i in range(0, len(payload), 60))
        body = b"c0  s0  s3\r\nevent  s1  s4\r\n"
        wire = echo + framed(self.command_id, body, newline=b"\r\n") + b"~ # "
        for size in (1, 2, 3, 17, len(wire)):
            compact = CompactMonitor()
            compact.start(self.command_id)
            output = b"".join(compact.feed(wire[i:i+size]) for i in range(0, len(wire), size))
            self.assertEqual(output, body + b"~ # ")
            self.assertEqual(compact.finish(), b"")

    def test_output_bytes_and_unrelated_markers_are_not_removed(self):
        body = (b"\x1b[31m" + "測試".encode() + b"\x1b[0m\x00\xff\r\n"
                + framed("b" * 32) + b"\x1b[2J\x1b[3;5Hno-newline")
        compact = CompactMonitor()
        compact.start(self.command_id)
        output = compact.feed(framed(self.command_id, body, 7) + b"~ # ")
        self.assertEqual(output, body + b"~ # ")

    def test_large_output_is_streamed_with_bounded_pending_bytes(self):
        compact = CompactMonitor()
        compact.start(self.command_id)
        begin, end = markers(self.command_id)
        self.assertEqual(compact.feed(b"\n" + begin + b"\n"), b"")
        for _ in range(100):
            self.assertEqual(compact.feed(b"x" * 4096), b"x" * 4096)
            self.assertLessEqual(len(compact.pending), 80)
        self.assertEqual(compact.feed(b"\n" + end + b"0\n~ # "), b"~ # ")

    def test_join_running_exec_and_preserve_unfinished_output_on_disconnect(self):
        compact = CompactMonitor()
        compact.event({"type": "monitor", "execution": {"id": self.command_id, "started": True}})
        end = markers(self.command_id)[1]
        self.assertEqual(compact.feed(b"remaining\r\n" + end + b"130\r\n~ # "), b"remaining~ # ")
        compact.start(self.command_id, started=True)
        self.assertEqual(compact.feed(b"actual output\n__UART_"), b"actual output")
        self.assertEqual(compact.finish(), b"\n__UART_")

    def test_multiple_exec_and_interrupt_before_begin(self):
        compact = CompactMonitor()
        for command_id in (self.command_id, "b" * 32):
            compact.event({"type": "tx", "id": command_id})
            self.assertEqual(compact.feed(framed(command_id, b"ok\n") + b"~ # "), b"ok\n~ # ")
            compact.event({"type": "completed"})
        compact.start(self.command_id)
        compact.event({"type": "tx", "id": self.command_id, "action": "interrupt"})
        self.assertEqual(compact.feed(b"^C\r\n~ # "), b"^C\r\n~ # ")

    def test_shows_original_command_once_with_or_without_device_echo(self):
        for echo in (True, False):
            compact = CompactMonitor()
            output = compact.feed(b"~ # ")
            command = "printf '%s' '測試'"
            output += compact.event({"type": "tx", "id": self.command_id, "command": command})
            wire = shell_payload(command, self.command_id) if echo else b""
            output += compact.feed(wire + framed(self.command_id, "測試\r\n".encode()) + b"~ # ")
            self.assertEqual(output, b"~ # " + command.encode() + b"\r\n" + "測試\r\n~ # ".encode())

    def test_cwd_label_is_per_command_and_also_shown_when_joining(self):
        for joining in (False, True):
            with self.subTest(joining=joining):
                compact = CompactMonitor()
                output = compact.feed(b"~ # ")
                command = {"id": self.command_id, "command": "ls", "cwd": "/tmp/space ' dir"}
                event = ({"type": "monitor", "execution": dict(command, started=False)} if joining else
                         dict(command, type="tx"))
                output += compact.event(event)
                output += compact.feed(framed(self.command_id, b"files\r\n") + b"~ # ")
                compact.event({"type": "completed"})
                output += compact.event({"type": "tx", "id": "b" * 32, "command": "pwd", "cwd": None})
                output += compact.feed(framed("b" * 32, b"/mnt/ramdisk\r\n") + b"~ # ")
                self.assertEqual(output, ("~ # [cwd=/tmp/space ' dir] ls\r\n"
                                          "files\r\n~ # pwd\r\n/mnt/ramdisk\r\n~ # ").encode())

    def test_join_inside_begin_or_end_marker_and_do_not_replay_prefix(self):
        begin, end = markers(self.command_id)
        for position in range(1, len(begin)):
            compact = CompactMonitor()
            compact.start(self.command_id, prefix=b"\n" + begin[:position])
            self.assertEqual(compact.feed(begin[position:] + b"\nbody\n" + end + b"0\n~ # "), b"body~ # ")
        for position in range(len(end)):
            compact = CompactMonitor()
            compact.start(self.command_id, started=True, prefix=b"\r\n" + end[:position])
            self.assertEqual(compact.feed(end[position:] + b"130\r\n~ # "), b"~ # ")
        compact = CompactMonitor()
        compact.start(self.command_id, started=True, prefix=b"\n__UART_")
        self.assertEqual(compact.feed(b"actually-normal-output\n"), b"actually-normal-output")
        self.assertEqual(compact.finish(), b"\n")


class FullMonitorTests(unittest.TestCase):
    # 使用本機時區的固定時間，讓測試可在 Windows／Linux 不同時區執行。
    stamp = datetime(2026, 11, 13, 22, 18, 19, 123000).astimezone().isoformat()
    later = datetime(2026, 11, 13, 22, 18, 20, 456000).astimezone().isoformat()

    def setUp(self):
        self.output = io.StringIO()
        self.renderer = FullMonitor(self.output)

    def rx(self, data, stamp=None):
        self.renderer.event({"type": "rx", "time": stamp or self.stamp, "data_b64": b64(data)})

    def test_split_rx_gets_one_prefix_per_line_and_prompt_is_immediate(self):
        self.rx(b"[")
        self.assertEqual(self.output.getvalue(), "[22:18:19.123] RX  [")
        self.rx(b"\x1b[")
        self.rx(b"31m*\x1b[0m] ", self.later)
        self.rx("測試".encode()[:1])
        self.rx("測試".encode()[1:] + b"\r")
        self.rx(b"\n~ # ", self.later)
        self.assertEqual(self.output.getvalue(),
                         "[22:18:19.123] RX  [\x1b[31m*\x1b[0m] 測試\n[22:18:20.456] RX  ~ # ")

    def test_controls_are_visible_and_do_not_move_cursor_or_clear_screen(self):
        self.rx(b"old\rNEW\x1b[2J\x1b[3;5H\b\x00\x1b]0;title\x07\n")
        self.assertEqual(self.output.getvalue(),
                         "[22:18:19.123] RX  old<CR>NEW<ESC[2J><ESC[3;5H><BS><0x00><ESC]0;title\\x07>\n")
        self.assertNotIn("\x1b", self.output.getvalue())

    def test_events_separate_partial_line_and_keep_completion_and_tx_details(self):
        self.renderer.event({"type": "monitor", "next_seq": 1})
        self.rx(b"prompt# ")
        self.renderer.event({"type": "tx", "time": self.later, "data_b64": b64(b"uname -a\r")})
        self.renderer.event({"type": "tx", "time": self.later, "action": "interrupt", "data_b64": "Aw=="})
        self.renderer.event({"type": "completed", "time": self.later, "id": "abc", "exit_code": 130})
        self.rx(b"continued\n", self.later)
        text = self.output.getvalue()
        self.assertIn("MONITOR  next_seq=1\n", text)
        self.assertIn("prompt# \n[22:18:20.456] TX  uname -a\\r\n", text)
        self.assertIn("[22:18:20.456] TX  Ctrl+C (0x03)\n", text)
        self.assertIn('[22:18:20.456] DONE  id="abc" exit_code=130\n', text)
        self.assertIn('[22:18:20.456] RX  continued\n', text)

    def test_incomplete_sequences_are_bounded_and_visible_on_disconnect(self):
        self.rx(b"\x1b]" + b"x" * 1000)
        self.assertLess(len(self.renderer.escape), 256)
        self.rx(b"\x1b[")
        self.renderer.finish()
        self.assertIn("<ESC[>", self.output.getvalue())
        self.assertNotIn("\x1b", self.output.getvalue())


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.log_path = Path(self.temp.name) / "uart.ndjson"
        self.serial = FakeSerial()
        self.gateway = Gateway(self.serial, TrafficLog(self.log_path))
        self.server = TCPServer(("127.0.0.1", 0), self.gateway)
        self.address = self.server.server_address
        self.gateway.start()
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()
        self.sockets = []

    def tearDown(self):
        for sock, stream in self.sockets:
            stream.close()
            sock.close()
        self.server.shutdown()
        self.server.server_close()
        self.gateway.close()
        self.thread.join(2)
        self.temp.cleanup()

    def connect(self, request):
        sock = socket.create_connection(self.address, timeout=2)
        stream = sock.makefile("rb")
        self.sockets.append((sock, stream))
        sock.sendall(encode(request))
        return sock, stream

    def request(self, request):
        _, stream = self.connect(request)
        return read_message(stream)

    def begin(self, timeout=1, maximum=1024, command="test command", cwd=None):
        sock, stream = self.connect({"op": "exec", "command": command, "timeout": timeout, "max_output": maximum, "cwd": cwd})
        accepted = read_message(stream)
        self.assertEqual(accepted["type"], "accepted")
        return accepted["id"], sock, stream

    def complete(self, command_id, body=b"hello\n", code=0):
        self.serial.incoming.put(framed(command_id, body, code))

    def test_exec_monitor_echo_busy_and_raw_log(self):
        _, monitor = self.connect({"op": "monitor"})
        self.assertEqual(read_message(monitor)["type"], "monitor")
        command_id, _, stream = self.begin()
        self.assertEqual(self.request({"op": "exec", "command": "other"})["error"], "busy")
        payload = self.serial.writes[0]
        self.serial.incoming.put(payload)
        self.complete(command_id, b"a\x00\xff\n", 7)
        result = read_message(stream)
        self.assertEqual((result["state"], result["exit_code"]), ("completed", 7))
        self.assertEqual(unb64(result["output_b64"]), b"a\x00\xff\n")
        self.assertTrue(result["output_complete"])
        events = []
        while not events or events[-1]["type"] != "completed":
            events.append(read_message(monitor))
        self.assertEqual(events[0]["type"], "tx")
        self.assertEqual(unb64(events[0]["data_b64"]), payload)
        self.assertTrue(any(e["type"] == "rx" for e in events))
        self.assertEqual([e["seq"] for e in events], sorted(e["seq"] for e in events))
        records = [json.loads(line) for line in self.log_path.read_text().splitlines()]
        self.assertEqual([e["seq"] for e in events], [e["seq"] for e in records])
        self.assertEqual(self.request({"op": "status"})["shell"], "idle")
        self.assertEqual(len(self.serial.writes), 1)

    def test_timeout_holds_lock_late_marker_and_next_exec(self):
        command_id, _, stream = self.begin(timeout=0.1)
        result = read_message(stream)
        self.assertEqual((result["state"], result["reason"]), ("unknown", "timeout"))
        self.assertEqual(self.request({"op": "status"})["shell"], "uncertain")
        self.assertEqual(self.request({"op": "exec", "command": "other"})["error"], "busy")
        time.sleep(0.15)
        self.assertEqual(len(self.serial.writes), 1)
        self.complete(command_id)
        wait_for(lambda: self.gateway.active is None)
        self.assertEqual(self.request({"op": "status"})["last_execution"]["state"], "completed")
        next_id, _, next_stream = self.begin()
        self.complete(next_id)
        self.assertEqual(read_message(next_stream)["state"], "completed")

    def test_interrupt_sends_one_byte_and_waits_for_original_marker(self):
        _, monitor = self.connect({"op": "monitor"})
        read_message(monitor)
        command_id, _, stream = self.begin()
        reply = self.request({"op": "interrupt"})
        self.assertEqual((reply["state"], reply["id"], reply["accepted_bytes"]),
                         ("written_to_driver", command_id, 1))
        self.assertEqual(self.serial.writes[1:], [b"\x03"])
        self.assertEqual(self.request({"op": "status"})["shell"], "running")
        self.assertEqual(self.request({"op": "exec", "command": "other"})["error"], "busy")
        self.complete(command_id, b"interrupted", 130)
        result = read_message(stream)
        self.assertEqual((result["state"], result["exit_code"], result["interrupt_requested"]),
                         ("completed", 130, True))
        self.assertEqual(self.request({"op": "status"})["shell"], "idle")
        events = []
        while not events or events[-1]["type"] != "completed":
            events.append(read_message(monitor))
        interrupts = [e for e in events if e.get("action") == "interrupt" and e["type"] == "tx"]
        self.assertEqual(len(interrupts), 1)
        self.assertEqual(unb64(interrupts[0]["data_b64"]), b"\x03")

    def test_interrupt_timeout_and_disconnect_do_not_unlock_or_retry(self):
        command_id, _, stream = self.begin(timeout=0.1)
        self.assertEqual(read_message(stream)["reason"], "timeout")
        sock, interrupt_stream = self.connect({"op": "interrupt"})
        sock.shutdown(socket.SHUT_RDWR)
        interrupt_stream.close()
        sock.close()
        wait_for(lambda: self.gateway.last["interrupt_requested"] and not self.gateway.interrupting)
        time.sleep(0.1)
        self.assertEqual(self.serial.writes[1:], [b"\x03"])
        self.assertEqual(self.request({"op": "status"})["shell"], "uncertain")
        self.assertEqual(self.request({"op": "exec", "command": "other"})["error"], "busy")
        self.complete(command_id, code=130)
        wait_for(lambda: self.gateway.active is None)
        self.assertEqual(self.request({"op": "status"})["last_execution"]["exit_code"], 130)

    def test_interrupt_without_active_execution_and_cli(self):
        process = subprocess.run([sys.executable, "client.py", "--port", str(self.address[1]), "interrupt"],
                                 capture_output=True, timeout=5)
        self.assertEqual(process.returncode, 0, process.stderr)
        reply = json.loads(process.stdout)
        self.assertEqual(reply["state"], "written_to_driver")
        self.assertIsNone(reply["id"])
        self.assertEqual(self.serial.writes, [b"\x03"])
        self.assertEqual(self.request({"op": "status"})["shell"], "unknown")

    def test_interrupt_failed_write_faults_uart_without_retry(self):
        command_id, _, stream = self.begin()
        self.serial.short_write = True
        reply = self.request({"op": "interrupt"})
        self.assertEqual((reply["state"], reply["accepted_bytes"]), ("unknown", 0))
        self.assertEqual(read_message(stream)["reason"], "uart_error")
        self.assertEqual(self.request({"op": "interrupt"})["error"], "uart_unavailable")
        self.assertEqual(self.serial.writes[1:], [b"\x03"])

    def test_slow_interrupt_serializes_tx_but_does_not_block_rx(self):
        command_id, _, stream = self.begin()
        release = threading.Event()
        entered = threading.Event()
        original_write = self.serial.write
        def slow_write(data):
            count = original_write(data)
            entered.set()
            if not release.wait(2):
                raise OSError("中斷測試寫入逾時")
            return count
        self.serial.write = slow_write
        _, interrupt_stream = self.connect({"op": "interrupt"})
        try:
            self.assertTrue(entered.wait(1))
            self.complete(command_id, code=130)
            self.assertEqual(read_message(stream)["exit_code"], 130)
            self.assertEqual(self.request({"op": "exec", "command": "other"})["error"], "busy")
            self.assertEqual(self.request({"op": "interrupt"})["error"], "tx_busy")
            self.assertEqual(self.serial.writes[1:], [b"\x03"])
        finally:
            release.set()
        self.assertEqual(read_message(interrupt_stream)["state"], "written_to_driver")

    def test_monitor_connection_cannot_send_interrupt(self):
        sock, stream = self.connect({"op": "monitor"})
        read_message(stream)
        sock.sendall(encode({"op": "interrupt"}))
        wait_for(lambda: not self.gateway.monitors)
        self.assertEqual(stream.read(1), b"")
        self.assertFalse(self.serial.writes)

    def test_disconnect_holds_lock_and_status_is_readonly(self):
        command_id, sock, stream = self.begin()
        sock.shutdown(socket.SHUT_RDWR)
        stream.close()
        sock.close()
        wait_for(lambda: self.gateway.last["reason"] == "client_disconnected")
        for _ in range(2):
            self.assertEqual(self.request({"op": "status"})["shell"], "uncertain")
        self.assertEqual(len(self.serial.writes), 1)
        self.assertEqual(self.request({"op": "exec", "command": "other"})["error"], "busy")
        self.complete(command_id)
        wait_for(lambda: self.gateway.active is None)

    def test_uart_error_and_partial_write_never_retry(self):
        self.serial.short_write = True
        _, _, stream = self.begin()
        result = read_message(stream)
        self.assertEqual((result["state"], result["reason"]), ("unknown", "uart_error"))
        self.assertEqual(self.request({"op": "exec", "command": "other"})["error"], "uart_unavailable")
        self.assertEqual(len(self.serial.writes), 1)

    def test_uart_read_error(self):
        _, _, stream = self.begin()
        self.serial.incoming.put(OSError("UART removed"))
        self.assertEqual(read_message(stream)["reason"], "uart_error")
        self.assertEqual(self.request({"op": "status"})["uart"], "error")

    def test_slow_write_does_not_block_rx_or_allow_second_writer(self):
        release_write = threading.Event()
        entered_write = threading.Event()
        original_write = self.serial.write

        def slow_write(data):
            count = original_write(data)
            entered_write.set()
            if not release_write.wait(2):
                raise OSError("測試寫入等待逾時")
            return count

        self.serial.write = slow_write
        _, stream = self.connect({"op": "exec", "command": "slow driver"})
        try:
            self.assertTrue(entered_write.wait(1))
            command_id = self.gateway.last["id"]
            self.complete(command_id)
            wait_for(lambda: self.gateway.last["parser"].done)
            self.assertIsNotNone(self.gateway.active)
            self.assertEqual(self.request({"op": "exec", "command": "other"})["error"], "busy")
            self.assertEqual(self.request({"op": "interrupt"})["error"], "tx_busy")
            self.assertEqual(len(self.serial.writes), 1)
        finally:
            release_write.set()
        self.assertEqual(read_message(stream)["type"], "accepted")
        self.assertEqual(read_message(stream)["state"], "completed")

    def test_known_result_survives_later_uart_error(self):
        command_id, _, stream = self.begin()
        self.complete(command_id)
        self.assertEqual(read_message(stream)["state"], "completed")
        self.serial.incoming.put(OSError("removed"))
        wait_for(lambda: self.gateway.fault)
        self.assertEqual(self.request({"op": "status"})["last_execution"]["exit_code"], 0)

    def test_truncated_output_still_reads_completion(self):
        command_id, _, stream = self.begin(maximum=8)
        self.complete(command_id, b"x" * 50000)
        result = read_message(stream)
        self.assertEqual(result["state"], "completed")
        self.assertTrue(result["output_truncated"])
        self.assertFalse(result["output_complete"])
        self.assertEqual(len(unb64(result["output_b64"])), 8)

    def test_monitor_cannot_exec_and_overflow_does_not_block_rx(self):
        sock, stream = self.connect({"op": "monitor"})
        read_message(stream)
        sock.sendall(encode({"op": "exec", "command": "must not run"}))
        wait_for(lambda: not self.gateway.monitors)
        self.assertEqual(stream.read(1), b"")
        self.assertFalse(self.serial.writes)
        # 模擬完全不消費的 Monitor 佇列，驗證 UART 廣播不會等待它。
        slow = queue.Queue(maxsize=1)
        with self.gateway.condition:
            self.gateway.monitors.add(slow)
        command_id, _, execution_stream = self.begin()
        self.complete(command_id)
        self.assertEqual(read_message(execution_stream)["state"], "completed")
        self.assertNotIn(slow, self.gateway.monitors)

    def test_malformed_protocol_and_readonly_status(self):
        sock = socket.create_connection(self.address, timeout=2)
        with sock, sock.makefile("rb") as stream:
            sock.sendall(b"[]\n")
            self.assertEqual(read_message(stream)["error"], "invalid_request")
        for request in ({"op": "bad"}, {"op": "exec", "command": "bad\ncommand"}):
            self.assertEqual(self.request(request)["error"], "invalid_request")
        self.assertEqual(self.request({"op": "status"})["shell"], "unknown")
        self.assertFalse(self.serial.writes)

    def test_client_cli_exit_codes_json(self):
        self.serial.auto_reply = lambda command_id: self.complete(command_id, b"CLI output", 7)
        command = [sys.executable, "client.py", "--port", str(self.address[1])]
        result = subprocess.run(command + ["exec", "test", "--json"], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)["exit_code"], 7)
        result = subprocess.run(command + ["status"], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["shell"], "idle")
        self.serial.auto_reply = None
        result = subprocess.run(command + ["exec", "test", "--timeout", "0.1"], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 2)

    def test_cwd_cli_protocol_status_and_monitor_metadata(self):
        self.serial.auto_reply = lambda command_id: self.complete(command_id, b"ok")
        _, monitor = self.connect({"op": "monitor"})
        read_message(monitor)
        command = [sys.executable, "client.py", "--port", str(self.address[1]), "exec"]
        cwd = "/tmp/space ' dir"
        result = subprocess.run(command + ["--cwd", cwd, "pwd", "--json"], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["cwd"], cwd)
        event = read_message(monitor)
        self.assertEqual((event["type"], event["command"], event["cwd"]), ("tx", "pwd", cwd))
        self.assertEqual(self.request({"op": "status"})["last_execution"]["cwd"], cwd)
        result = subprocess.run(command + ["pwd", "--json"], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(json.loads(result.stdout)["cwd"])

    def test_cwd_rejects_legacy_server_before_sending_exec(self):
        requests = []
        class LegacyHandler(socketserver.StreamRequestHandler):
            def handle(self):
                request = read_message(self.rfile)
                requests.append(request)
                # 模擬舊版：會忽略 cwd；但未知 op 只回錯誤，不能寫 UART。
                response = ({"type": "result", "state": "completed", "exit_code": 0}
                            if request.get("op") == "exec" else
                            {"type": "error", "error": "invalid_request"})
                self.request.sendall(encode(response))
        with socketserver.TCPServer(("127.0.0.1", 0), LegacyHandler) as legacy:
            thread = threading.Thread(target=legacy.serve_forever, kwargs={"poll_interval": 0.01})
            thread.start()
            try:
                result = subprocess.run([sys.executable, "client.py", "--port", str(legacy.server_address[1]),
                                         "exec", "--cwd", "/tmp", "pwd", "--json"],
                                        capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(json.loads(result.stdout)["error"], "cwd_unsupported")
                self.assertEqual(requests, [{"op": "hello"}])
            finally:
                legacy.shutdown()
                thread.join(2)
        self.assertFalse(self.serial.writes)

    def test_cwd_feature_check_and_execution_use_the_same_connection(self):
        sock, stream = self.connect({"op": "hello"})
        hello = read_message(stream)
        self.assertIn("cwd", hello["features"])
        self.assertFalse(self.serial.writes)
        sock.sendall(encode({"op": "exec", "command": "pwd", "cwd": "/tmp"}))
        accepted = read_message(stream)
        self.assertEqual(accepted["type"], "accepted")
        self.complete(accepted["id"], b"/tmp\r\n")
        result = read_message(stream)
        self.assertEqual((result["cwd"], result["exit_code"]), ("/tmp", 0))
        self.assertEqual(len(self.serial.writes), 1)

    def test_invalid_cwd_and_oversized_payload_never_write_uart(self):
        for cwd in ("", " ", 123, {}, "bad\ncwd", "bad\x00cwd", "x" * 4097, "x" * 4096):
            with self.subTest(cwd=repr(cwd)[:40]):
                reply = self.request({"op": "exec", "command": "true", "cwd": cwd})
                self.assertEqual(reply["error"], "invalid_request")
        self.assertFalse(self.serial.writes)

    def test_client_monitor_streams_without_buffering(self):
        process = subprocess.Popen([sys.executable, "client.py", "--port", str(self.address[1]),
                                    "monitor", "--json"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        lines = queue.Queue()
        def collect():
            for line in process.stdout:
                lines.put(line)
        reader = threading.Thread(target=collect)
        reader.start()
        try:
            self.assertEqual(json.loads(lines.get(timeout=3))["type"], "monitor")
            self.serial.incoming.put(b"monitor output\x1b[31m")
            event = json.loads(lines.get(timeout=3))
            self.assertEqual(event["type"], "rx")
            self.assertEqual(unb64(event["data_b64"]), b"monitor output\x1b[31m")
            self.assertFalse(self.serial.writes)
        finally:
            process.terminate()
            process.wait(timeout=3)
            reader.join(3)
            process.stdout.close()
            process.stderr.close()

    def test_client_monitor_preserves_split_ansi_utf8_and_cursor_bytes(self):
        process = subprocess.Popen([sys.executable, "client.py", "--port", str(self.address[1]),
                                    "monitor"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            wait_for(lambda: bool(self.gateway.monitors))
            with self.gateway.condition:
                self.gateway._event("tx", data_b64=b64(b"printf test\r"))
                start = self.gateway.sequence
            # 刻意拆在 CSI 與 UTF-8 中間；包括 CR、LF、游標移動、清畫面、退格。
            chunks = [b"[\x1b[", b"31m*\x1b[0m] ", b"\xe6", b"\xb8\xac\xe8\xa9", b"\xa6\r\n",
                      b"progress 10%\rprogress 20%\x1b[2K\x1b[3;5H\b!"]
            for chunk in chunks:
                self.serial.incoming.put(chunk)
            wait_for(lambda: self.gateway.sequence >= start + len(chunks))
            with self.gateway.condition:
                self.gateway._event("error", error="test_end")
            stdout, stderr = process.communicate(timeout=3)
            self.assertEqual(process.returncode, 2)
            self.assertEqual(stdout, b"".join(chunks))
            self.assertNotIn(b"[TX]", stderr)
            self.assertNotIn(b"[TX]", stdout)
            self.assertNotIn(b"RX", stdout)
            self.assertNotIn(b"\\x1b", stdout)
            self.assertFalse(self.serial.writes)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=3)
            process.stdout.close()
            process.stderr.close()

    def test_client_full_monitor_streams_partial_prompt_and_timestamps_events(self):
        process = subprocess.Popen([sys.executable, "client.py", "--port", str(self.address[1]),
                                    "monitor", "--full"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        received = queue.Queue()
        def collect():
            while True:
                byte = process.stdout.read(1)
                if not byte:
                    return
                received.put(byte)
        reader = threading.Thread(target=collect)
        reader.start()
        captured = bytearray()
        try:
            wait_for(lambda: bool(self.gateway.monitors))
            self.serial.incoming.put(b"~ # ")
            while not captured.endswith(b"~ # "):
                captured.extend(received.get(timeout=3))
            # 沒有結尾換行，也不等待下一個事件，Prompt 應已顯示。
            self.assertRegex(captured.decode(), r"\[\d{2}:\d{2}:\d{2}\.\d{3}\] RX  ~ # $")
            with self.gateway.condition:
                self.gateway._event("tx", data_b64=b64(b"uname -a\r"))
                self.gateway._event("completed", id="test", exit_code=0)
                self.gateway._event("error", error="test_end")
            process.wait(timeout=3)
            reader.join(3)
            while not received.empty():
                captured.extend(received.get_nowait())
            text = captured.decode()
            self.assertIn("TX  uname -a\\r", text)
            self.assertIn('DONE  id="test" exit_code=0', text)
            self.assertIn('ERROR  error="test_end"', text)
            self.assertEqual(process.returncode, 2)
            self.assertFalse(self.serial.writes)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=3)
            reader.join(3)
            process.stdout.close()
            process.stderr.close()

    def test_client_compact_monitor_shows_original_tx_and_rx_without_wrappers(self):
        process = subprocess.Popen([sys.executable, "client.py", "--port", str(self.address[1]), "monitor"],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            wait_for(lambda: bool(self.gateway.monitors))
            self.serial.incoming.put(b"~ # ")
            wait_for(lambda: self.gateway.last_rx is not None)
            expected = b"~ # "
            for command, body, cwd in (("uname -a", b"Linux Network-Camera aarch64 GNU/Linux\r\n", None),
                                       ("ls", b"apply_patch_after  display.sh\r\n", "/mnt/flash2/patch")):
                command_id, _, stream = self.begin(command=command, cwd=cwd)
                initial_sequence = self.gateway.sequence
                payload = self.serial.writes[-1].replace(b"\r", b"\r\n")
                wire = payload + framed(command_id, body, newline=b"\r\n") + b"~ # "
                for i in range(0, len(wire), 7):
                    self.serial.incoming.put(wire[i:i+7])
                self.assertEqual(read_message(stream)["state"], "completed")
                wait_for(lambda: self.gateway.sequence >= initial_sequence + (len(wire) + 6) // 7 + 1)
                wait_for(lambda: all(events.empty() for events in self.gateway.monitors))
                label = f"[cwd={cwd}] " if cwd is not None else ""
                expected += (label + command).encode() + b"\r\n" + body + b"~ # "
            with self.gateway.condition:
                self.gateway._event("error", error="test_end")
            output, _ = process.communicate(timeout=3)
            self.assertEqual(output, expected)
            self.assertEqual(len(self.serial.writes), 2)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=3)
            process.stdout.close()
            process.stderr.close()

    def test_ctrl_c_exits_idle_monitor_in_all_modes_without_uart_tx(self):
        for options in ([], ["--full"], ["--json"]):
            with self.subTest(options=options):
                args = ["--port", str(self.address[1]), "monitor", *options]
                script = ("import _thread,threading,time,client\n"
                          "def interrupt():\n    time.sleep(0.4)\n    _thread.interrupt_main()\n"
                          "threading.Thread(target=interrupt,daemon=True).start()\n"
                          f"raise SystemExit(client.main({args!r}))\n")
                result = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=3)
                self.assertEqual(result.returncode, 130, result.stderr)
                self.assertNotIn(b"Traceback", result.stderr)
                wait_for(lambda: not self.gateway.monitors)
                self.assertFalse(self.serial.writes)

    def test_ctrl_c_exits_while_ndjson_frame_is_incomplete(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(2)
        finished = threading.Event()
        def serve_partial():
            conn, _ = listener.accept()
            with conn:
                conn.recv(4096)
                conn.sendall(b'{"type":"rx","data_b64":"')
                finished.wait(3)
        thread = threading.Thread(target=serve_partial)
        thread.start()
        try:
            args = ["--port", str(listener.getsockname()[1]), "monitor"]
            script = ("import _thread,threading,time,client\n"
                      "def interrupt():\n    time.sleep(0.4)\n    _thread.interrupt_main()\n"
                      "threading.Thread(target=interrupt,daemon=True).start()\n"
                      f"raise SystemExit(client.main({args!r}))\n")
            result = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=3)
            self.assertEqual(result.returncode, 130, result.stderr)
            self.assertNotIn(b"Traceback", result.stderr)
        finally:
            finished.set()
            listener.close()
            thread.join(3)

    def test_monitor_greeting_carries_partial_marker_context_for_late_join(self):
        command_id, _, stream = self.begin()
        begin, end = markers(command_id)
        self.serial.incoming.put(b"\n" + begin[:16])
        wait_for(lambda: len(self.gateway.last["parser"].pending) == 16)
        _, monitor = self.connect({"op": "monitor"})
        compact = CompactMonitor()
        compact.event(read_message(monitor))
        self.serial.incoming.put(begin[16:] + b"\r\ndata\r\n\r\n" + end[:20])
        event = read_message(monitor)
        self.assertEqual(compact.event(event), b"data\r\n")
        _, late_monitor = self.connect({"op": "monitor"})
        late = CompactMonitor()
        late.event(read_message(late_monitor))
        self.serial.incoming.put(end[20:] + b"0\r\n~ # ")
        self.assertEqual(compact.event(read_message(monitor)), b"~ # ")
        self.assertEqual(late.event(read_message(late_monitor)), b"~ # ")
        self.assertEqual(read_message(stream)["state"], "completed")

    def test_monitor_full_and_json_are_mutually_exclusive(self):
        result = subprocess.run([sys.executable, "client.py", "monitor", "--full", "--json"],
                                capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"not allowed with argument", result.stderr)

    def test_log_failure_visible_without_losing_rx(self):
        self.gateway.log.handler.stream.close()
        # 使用實際 OSError，而非 logging 預設錯誤吞沒。
        class FailedStream:
            def tell(self):
                return 0

            def write(self, value):
                raise OSError("disk full")

            def flush(self):
                pass

            def close(self):
                pass
        self.gateway.log.handler.stream = FailedStream()
        self.serial.incoming.put(b"still receiving")
        wait_for(lambda: self.gateway.log.error is not None)
        self.assertIn("disk full", self.request({"op": "status"})["log_error"])
        command_id, _, stream = self.begin()
        self.complete(command_id)
        self.assertEqual(read_message(stream)["state"], "completed")


if __name__ == "__main__":
    unittest.main()
