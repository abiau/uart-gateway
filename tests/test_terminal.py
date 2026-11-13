"""互動終端：真 TCP、Fake UART、獨占、密碼 Log、恢復與雙向資料。"""
from collections import deque
import io
import json
import socket
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock, patch

import client
from client import terminal_session
from protocol import b64, encode, read_message, unb64
import test_gateway as fixtures

wait_for = fixtures.wait_for


def until(stream, kind):
    for _ in range(64):
        message = read_message(stream)
        if message["type"] == kind:
            return message
        if message["type"] == "error":
            raise AssertionError(message)
    raise AssertionError("等待終端事件超過上限")


class TerminalTests(unittest.TestCase):
    setUp = fixtures.GatewayTests.setUp
    tearDown = fixtures.GatewayTests.tearDown
    connect = fixtures.GatewayTests.connect
    request = fixtures.GatewayTests.request
    begin = fixtures.GatewayTests.begin
    complete = fixtures.GatewayTests.complete

    def open(self):
        sock, stream = self.connect({"op": "terminal"})
        self.assertEqual(read_message(stream)["type"], "terminal")
        return sock, stream

    def close(self, sock, stream):
        sock.sendall(encode({"op": "terminal_close"}))
        self.assertEqual(until(stream, "terminal_closed")["shell"], "uncertain")
        wait_for(lambda: self.gateway.terminal is None)

    def test_exclusive_writer_with_readonly_monitor(self):
        _, monitor = self.connect({"op": "monitor"})
        read_message(monitor)
        sock, stream = self.open()
        self.assertEqual(self.request({"op": "status"})["shell"], "terminal")
        self.assertEqual(self.request({"op": "terminal"})["error"], "busy")
        self.assertEqual(self.request({"op": "exec", "command": "pwd"})["error"], "terminal_busy")
        self.assertEqual(self.request({"op": "interrupt"})["error"], "terminal_busy")
        raw = b"admin\rsecret\r\x03\x1b[A\b"
        sock.sendall(encode({"op": "terminal_write", "data_b64": b64(raw)}))
        self.assertEqual(until(stream, "terminal_tx")["accepted_bytes"], len(raw))
        tx = until(monitor, "tx")
        self.assertTrue(tx["redacted"])
        self.assertNotIn("data_b64", tx)
        compact = client.CompactMonitor()
        self.assertEqual(compact.event(tx), b"")
        full_output = io.StringIO()
        client.FullMonitor(full_output).event(tx)
        self.assertIn("互動輸入已隱去", full_output.getvalue())
        rx = b"\x1b[32mBoot\x1b[0m\r\nlogin: \x00\xff"
        self.serial.incoming.put(rx)
        self.assertEqual(unb64(until(stream, "rx")["data_b64"]), rx)
        self.assertEqual(compact.event(until(monitor, "rx")), rx)
        self.assertEqual(self.serial.writes, [raw])
        self.close(sock, stream)
        self.assertEqual(self.request({"op": "exec", "command": "pwd"})["error"], "shell_uncertain")

    def test_private_log_includes_no_tx_rx_payload_even_after_disconnect(self):
        sock, stream = self.open()
        secret = b"PASSWORD_DO_NOT_RECORD_7391"
        sock.sendall(encode({"op": "terminal_write", "data_b64": b64(secret)}))
        until(stream, "terminal_tx")
        self.serial.incoming.put(secret)
        until(stream, "rx")
        self.close(sock, stream)
        before = self.gateway.last_rx
        self.serial.incoming.put(secret)
        wait_for(lambda: self.gateway.last_rx != before)
        records = [json.loads(line) for line in self.log_path.read_text().splitlines()]
        private = [record for record in records if record["type"] in ("tx", "rx")]
        self.assertTrue(private)
        self.assertTrue(all(record["redacted"] and "data_b64" not in record for record in private))
        text = self.log_path.read_bytes()
        self.assertNotIn(secret, text)
        self.assertNotIn(b64(secret).encode(), text)
        self.assertTrue(self.request({"op": "status"})["log_redacted"])
        reply = self.request({"op": "terminal_ready", "confirmed": True})
        self.assertEqual(reply["type"], "shell_ready")
        self.assertEqual(self.request({"op": "status"})["shell"], "idle")
        self.assertFalse(self.request({"op": "status"})["log_redacted"])
        before = self.gateway.last_rx
        self.serial.incoming.put(b"public output")
        wait_for(lambda: self.gateway.last_rx != before)
        last = json.loads(self.log_path.read_text().splitlines()[-1])
        self.assertEqual(unb64(last["data_b64"]), b"public output")

    def test_disconnect_keeps_uncertain_and_allows_manual_recovery_terminal(self):
        sock, stream = self.open()
        sock.shutdown(socket.SHUT_RDWR)
        stream.close()
        sock.close()
        wait_for(lambda: self.gateway.terminal is None)
        self.assertEqual(self.request({"op": "status"})["shell"], "uncertain")
        self.assertEqual(self.request({"op": "exec", "command": "pwd"})["error"], "shell_uncertain")
        next_sock, next_stream = self.open()
        self.assertEqual(self.request({"op": "terminal_ready", "confirmed": True})["error"], "busy")
        self.close(next_sock, next_stream)
        self.assertEqual(self.request({"op": "terminal_ready"})["error"], "invalid_request")
        result = subprocess.run([sys.executable, "client.py", "--port", str(self.address[1]),
                                 "terminal", "--shell-ready"], capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["type"], "shell_ready")
        self.assertFalse(self.serial.writes)

    def test_running_exec_rejected_but_unknown_exec_can_be_taken_over(self):
        command_id, _, execution_stream = self.begin(timeout=0.1)
        self.assertEqual(self.request({"op": "terminal"})["error"], "busy")
        self.assertEqual(read_message(execution_stream)["reason"], "timeout")
        sock, stream = self.open()
        last = self.request({"op": "status"})["last_execution"]
        self.assertEqual((last["id"], last["state"], last["reason"]), (command_id, "unknown", "terminal_takeover"))
        self.assertIsNone(self.gateway.active)
        sock.sendall(encode({"op": "terminal_write", "data_b64": "Aw=="}))
        until(stream, "terminal_tx")
        self.close(sock, stream)
        self.request({"op": "terminal_ready", "confirmed": True})
        next_id, _, next_stream = self.begin()
        self.complete(next_id)
        self.assertEqual(read_message(next_stream)["state"], "completed")
        self.assertEqual(len(self.serial.writes), 3)

    def test_partial_write_faults_uart_and_is_never_retried(self):
        sock, stream = self.open()
        self.serial.short_write = True
        sock.sendall(encode({"op": "terminal_write", "data_b64": b64(b"test")}))
        self.assertEqual(until(stream, "error")["error"], "uart_error")
        wait_for(lambda: self.gateway.terminal is None)
        self.assertEqual(self.serial.writes, [b"test"])
        self.assertEqual(self.request({"op": "terminal"})["error"], "uart_unavailable")
        self.assertEqual(self.request({"op": "terminal_ready", "confirmed": True})["error"], "uart_unavailable")

    def test_uart_read_failure_ends_terminal(self):
        _, stream = self.open()
        self.serial.incoming.put(OSError("device removed"))
        self.assertIn(until(stream, "error")["error"], ("uart_error", "terminal_lost"))
        wait_for(lambda: self.gateway.terminal is None)
        self.assertTrue(self.request({"op": "status"})["log_redacted"])

    def test_invalid_terminal_writes_close_session_without_uart_tx(self):
        for request in ({"op": "exec", "command": "wrong"},
                        {"op": "terminal_write", "data_b64": ""},
                        {"op": "terminal_write", "data_b64": b64(b"x" * 4097)},
                        {"op": "terminal_write", "data_b64": "@@not-base64@@"}):
            with self.subTest(op=request["op"], length=len(request.get("data_b64", ""))):
                sock, stream = self.open()
                sock.sendall(encode(request))
                self.assertEqual(until(stream, "error")["error"], "invalid_request")
                wait_for(lambda: self.gateway.terminal is None)
                self.assertFalse(self.serial.writes)

    def test_monitor_cannot_write_or_take_terminal_ownership(self):
        for operation in ("terminal", "terminal_write", "terminal_ready"):
            sock, stream = self.connect({"op": "monitor"})
            read_message(stream)
            sock.sendall(encode({"op": operation, "data_b64": b64(b"must not write"), "confirmed": True}))
            self.assertEqual(stream.read(1), b"")
        self.assertFalse(self.serial.writes)
        self.assertIsNone(self.gateway.terminal)

    def test_overflow_revokes_writes_but_uart_reader_keeps_running(self):
        terminal, error = self.gateway.open_terminal()
        self.assertIsNone(error)
        with self.gateway.condition:
            for _ in range(130):
                self.gateway._event("rx", data_b64=b64(b"data"))
        self.assertEqual(self.gateway.write_terminal(terminal, b"must not write")["error"], "terminal_unavailable")
        self.assertFalse(self.serial.writes)
        self.gateway.close_terminal(terminal)
        before = self.gateway.last_rx
        self.serial.incoming.put(b"still alive")
        wait_for(lambda: self.gateway.last_rx != before)

    def test_tx_event_that_overflows_terminal_queue_does_not_write_uart(self):
        terminal, _ = self.gateway.open_terminal()
        while not terminal["events"].full():
            terminal["events"].put_nowait({"type": "rx"})
        self.assertEqual(self.gateway.write_terminal(terminal, b"must not write")["error"], "terminal_unavailable")
        self.assertFalse(self.serial.writes)
        self.gateway.close_terminal(terminal)

    def test_client_duplex_login_boot_ctrl_c_newline_and_local_exit(self):
        self.gateway.newline = "crlf"
        sock, _ = self.connect({"op": "terminal"})
        output = bytearray()
        stage = 0
        def keyboard():
            nonlocal stage
            if stage == 0:
                self.serial.incoming.put(b"\x1b[32mBoot\x1b[0m\r\nlogin: ")
                stage = 1
            elif stage == 1 and output.endswith(b"login: "):
                stage = 2
                return b"admin\r"
            elif stage == 2 and len(self.serial.writes) == 1:
                self.serial.incoming.put(b"Password: ")
                stage = 3
            elif stage == 3 and output.endswith(b"Password: "):
                stage = 4
                return b"private-password\r"
            elif stage == 4 and len(self.serial.writes) == 2:
                stage = 5
                return b"\x03"
            elif stage == 5 and len(self.serial.writes) == 3:
                self.serial.incoming.put(b"^C\r\n~ # ")
                stage = 6
            elif stage == 6 and output.endswith(b"~ # "):
                return b"\x1d"
            return b""
        watchdog = threading.Timer(3, lambda: sock.shutdown(socket.SHUT_RDWR))
        watchdog.start()
        try:
            with patch("client.sys.stderr", io.StringIO()):
                self.assertEqual(terminal_session(sock, keyboard, output.extend), 0)
        finally:
            watchdog.cancel()
        self.assertEqual(self.serial.writes, [b"admin\r\n", b"private-password\r\n", b"\x03"])
        self.assertEqual(output, b"\x1b[32mBoot\x1b[0m\r\nlogin: Password: ^C\r\n~ # ")
        self.assertEqual(self.request({"op": "status"})["shell"], "uncertain")


class TerminalClientTests(unittest.TestCase):
    def test_compact_monitor_resets_exec_filter_when_terminal_takes_over(self):
        monitor = client.CompactMonitor()
        monitor.start("a" * 32, started=True)
        self.assertEqual(monitor.feed(b"old output\n"), b"old output")
        self.assertEqual(monitor.event({"type": "terminal_opened"}), b"\n")
        self.assertEqual(monitor.event({"type": "rx", "data_b64": b64(b"login: ")}), b"login: ")

    def test_tx_timeout_does_not_resend_keyboard_input(self):
        sock = Mock()
        sock.recv.return_value = encode({"type": "terminal", "newline": "cr"})
        keys = deque([b"command\r", b""])
        with patch("client.select.select", side_effect=[([sock], [], []), ([], [], [])]), \
                patch("client.time.monotonic", side_effect=[0, 0, 1, 2, 8]), patch("client.sys.stderr", io.StringIO()):
            with self.assertRaises(TimeoutError):
                terminal_session(sock, lambda: keys.popleft() if keys else b"", lambda data: None)
        self.assertEqual(sock.sendall.call_count, 1)

    def test_disconnect_after_input_does_not_resend(self):
        sock = Mock()
        sock.recv.side_effect = [encode({"type": "terminal", "newline": "cr"}), b""]
        keys = deque([b"command\r", b""])
        with patch("client.select.select", return_value=([sock], [], [])), patch("client.sys.stderr", io.StringIO()):
            with self.assertRaises(ValueError):
                terminal_session(sock, lambda: keys.popleft() if keys else b"", lambda data: None)
        self.assertEqual(sock.sendall.call_count, 1)
        self.assertEqual(json.loads(sock.sendall.call_args.args[0]),
                         {"op": "terminal_write", "data_b64": b64(b"command\r")})

    def test_terminal_refuses_redirected_stdin_before_connecting(self):
        with patch("client.sys.stdin.isatty", return_value=False), patch("client.socket.create_connection") as connect, \
                patch("client.sys.stderr", io.StringIO()):
            self.assertEqual(client.main(["terminal"]), 2)
        connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
