"""明確指定 COM 才執行的實機測試；一次只啟動一個單 UART Server。"""

import argparse
import json
import os
from pathlib import Path
import queue
import shlex
import socket
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import DEFAULT_PORT, b64, encode, markers, read_message, unb64


def check_port(port, tcp_port, test_interrupt=False, test_cwd=False, test_terminal=False):
    env = dict(os.environ, PYTHONUTF8="1")
    address = ("127.0.0.1", tcp_port)
    process = subprocess.Popen([sys.executable, "server.py", "--serial", port, "--port", str(tcp_port),
                                "--log", f"logs/hardware-{port}.ndjson"], cwd=ROOT, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    monitor = stream = reader = None
    events = queue.Queue()

    def cli(*arguments, expected=0):
        result = subprocess.run([sys.executable, "client.py", "--port", str(tcp_port), *arguments],
                                cwd=ROOT, env=env, capture_output=True, timeout=12)
        assert result.returncode == expected, (result.returncode, result.stdout, result.stderr)
        return json.loads(result.stdout)

    def wait_idle():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = cli("status")
            if status["shell"] == "idle":
                return status
            time.sleep(0.05)
        raise AssertionError("原命令未觀察到完成；不會重送或繼續測試")

    def collect():
        try:
            while True:
                events.put(read_message(stream))
        except (OSError, ValueError):
            pass

    def wait_status(shell):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if cli("status")["shell"] == shell:
                return
            time.sleep(0.02)
        raise AssertionError(f"未出現 Shell 狀態 {shell}")

    try:
        deadline = time.monotonic() + 5
        while True:
            if process.poll() is not None:
                raise AssertionError(process.stderr.read().decode("utf-8", "replace"))
            try:
                probe = socket.create_connection(address, timeout=0.2)
                probe.close()
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        status = cli("status")
        assert status["port"] == port and status["uart"] == "open", status
        # Server 已獨占 COM，第二個開啟者應失敗。
        import serial
        try:
            second = serial.Serial(port, timeout=0.1)
        except serial.SerialException:
            pass
        else:
            second.close()
            raise AssertionError("COM 沒有被獨占")
        monitor = socket.create_connection(address, timeout=10)
        stream = monitor.makefile("rb")
        monitor.sendall(encode({"op": "monitor"}))
        assert read_message(stream)["type"] == "monitor"
        reader = threading.Thread(target=collect)
        reader.start()
        results = []
        for command, expected_output, code in (
                ("uname -a", b"Linux", 0),
                ("printf 'uart-smoke\\n'; printf 'stderr-smoke\\n' >&2; exit 7", b"stderr-smoke", 7),
                ("printf '%s' \"A'B\"", b"A'B", 0),
                ("printf '\\377\\000ABC'", b"\xff\x00ABC", 0)):
            result = cli("exec", command, "--json", expected=0 if code == 0 else 1)
            assert result["state"] == "completed" and result["exit_code"] == code, result
            output = unb64(result["output_b64"])
            assert expected_output in output, (command, output)
            assert b"__UART_BEGIN_" not in output and b"__UART_END_" not in output, output
            assert result["output_complete"], result
            results.append({"test": command, "exit_code": code, "output_bytes": len(output)})
        timeout = cli("exec", "sleep 1", "--timeout", "0.1", "--json", expected=2)
        assert timeout["state"] == "unknown" and timeout["reason"] == "timeout", timeout
        busy = cli("exec", "printf must-not-run", "--json", expected=2)
        assert busy["error"] == "busy", busy
        recovered = wait_idle()
        assert recovered["last_execution"]["id"] == timeout["id"]
        assert recovered["last_execution"]["exit_code"] == 0
        sock = socket.create_connection(address, timeout=3)
        with sock, sock.makefile("rb") as execution_stream:
            sock.sendall(encode({"op": "exec", "command": "sleep 1", "timeout": 5}))
            accepted = read_message(execution_stream)
            assert accepted["type"] == "accepted", accepted
            sock.shutdown(socket.SHUT_RDWR)
        wait_status("uncertain")
        recovered = wait_idle()
        assert recovered["last_execution"]["id"] == accepted["id"]
        assert recovered["last_execution"]["exit_code"] == 0
        cwd_results = None
        if test_cwd:
            original = cli("exec", "pwd", "--json")
            scoped = cli("exec", "--cwd", "/tmp", "pwd", "--json")
            assert unb64(scoped["output_b64"]).strip() == b"/tmp", scoped
            assert scoped["cwd"] == "/tmp", scoped
            missing = "/__uart_cwd_missing_" + uuid.uuid4().hex
            failed = cli("exec", "--cwd", missing, "printf MUST_NOT_RUN; printf ALSO_MUST_NOT_RUN", "--json", expected=1)
            assert failed["state"] == "completed" and failed["exit_code"] != 0, failed
            assert b"MUST_NOT_RUN" not in unb64(failed["output_b64"]), failed
            restored = cli("exec", "pwd", "--json")
            assert restored["output_b64"] == original["output_b64"], restored
            assert restored["cwd"] is None, restored
            cwd_results = {"cwd": "/tmp", "missing_directory_blocks_all_commands": True,
                           "next_exec_preserves_original_directory": True}
        terminal_results = None
        if test_terminal:
            original = cli("exec", "pwd", "--json")
            original_path = unb64(original["output_b64"]).decode("utf-8").strip()
            assert original_path.startswith("/") and all(char.isprintable() for char in original_path), original
            terminal_sock = socket.create_connection(address, timeout=3)
            with terminal_sock, terminal_sock.makefile("rb") as terminal_stream:
                terminal_sock.sendall(encode({"op": "terminal"}))
                assert read_message(terminal_stream)["type"] == "terminal"
                def send_raw(data):
                    terminal_sock.sendall(encode({"op": "terminal_write", "data_b64": b64(data)}))
                def wait_prompt():
                    data = bytearray()
                    for _ in range(256):
                        message = read_message(terminal_stream)
                        assert message["type"] != "error", message
                        if message["type"] == "rx":
                            data.extend(unb64(message["data_b64"]))
                            if data.endswith(b" # "):
                                return bytes(data)
                    raise AssertionError("未觀察到 Shell 提示符，不繼續發送測試命令")
                send_raw(b"\r")
                wait_prompt()
                send_raw(b"pwd\r")
                assert original_path.encode() in wait_prompt()
                send_raw(b"cd /tmp\r")
                wait_prompt()
                send_raw(b"pwd\r")
                assert b"\r\n/tmp\r\n" in wait_prompt()
                send_raw(b"sleep 5\r")
                while read_message(terminal_stream)["type"] != "terminal_tx":
                    pass
                time.sleep(0.2)
                start = time.monotonic()
                send_raw(b"\x03")
                assert b"^C" in wait_prompt()
                assert time.monotonic() - start < 3
                send_raw(("cd " + shlex.quote(original_path) + "\r").encode())
                wait_prompt()
                send_raw(b"pwd\r")
                assert original_path.encode() in wait_prompt()
                terminal_sock.sendall(encode({"op": "terminal_close"}))
                while read_message(terminal_stream)["type"] != "terminal_closed":
                    pass
            blocked = cli("exec", "pwd", "--json", expected=2)
            assert blocked["error"] == "shell_uncertain", blocked
            ready = cli("terminal", "--shell-ready")
            assert ready["type"] == "shell_ready", ready
            restored = cli("exec", "pwd", "--json")
            assert restored["output_b64"] == original["output_b64"], restored
            terminal_results = {"raw_pwd": True, "persistent_cd": True, "ctrl_c": True,
                                "original_directory_restored": True, "explicit_shell_ready": True}
        interrupt_results = []
        if test_interrupt:
            # 使用有固定終點的 sleep，測試失敗時也不留下無限執行的命令。
            sock = socket.create_connection(address, timeout=3)
            with sock, sock.makefile("rb") as execution_stream:
                request = {"op": "exec", "command": "sleep 5", "timeout": 8}
                if test_cwd:
                    request["cwd"] = "/tmp"
                sock.sendall(encode(request))
                accepted = read_message(execution_stream)
                assert accepted["type"] == "accepted", accepted
                begin = markers(accepted["id"])[0]
                deadline = time.monotonic() + 2
                while True:
                    with events.mutex:
                        rx = b"".join(unb64(e["data_b64"]) for e in events.queue if e["type"] == "rx")
                    if begin in rx:
                        break
                    if time.monotonic() >= deadline:
                        raise AssertionError("未看到開始標記，不送出中斷")
                    time.sleep(0.01)
                time.sleep(0.1)
                started = time.monotonic()
                interrupt = cli("interrupt")
                assert interrupt["state"] == "written_to_driver" and interrupt["accepted_bytes"] == 1, interrupt
                assert interrupt["id"] == accepted["id"], interrupt
                result = read_message(execution_stream)
                assert result["state"] == "completed" and result["exit_code"] == 130, result
                assert result["interrupt_requested"], result
                assert time.monotonic() - started < 3, result
                interrupt_results.append({"case": "running", "exit_code": result["exit_code"]})
            timeout = cli("exec", "sleep 5", "--timeout", "0.1", "--json", expected=2)
            assert timeout["reason"] == "timeout", timeout
            interrupt = cli("interrupt")
            assert interrupt["id"] == timeout["id"] and interrupt["state"] == "written_to_driver", interrupt
            recovered = wait_idle()
            result = recovered["last_execution"]
            assert result["id"] == timeout["id"] and result["exit_code"] == 130, result
            interrupt_results.append({"case": "timeout", "exit_code": result["exit_code"]})
            result = cli("exec", "printf interrupt-recovered", "--json")
            assert unb64(result["output_b64"]) == b"interrupt-recovered", result
        captured = list(events.queue)
        tx = [e for e in captured if e["type"] == "tx"]
        assert len(tx) == (11 if test_interrupt else 6) + (4 if test_cwd else 0) + (10 if test_terminal else 0), ("命令不可重送，busy 請求不可寫入", len(tx))
        if test_interrupt:
            assert [unb64(e["data_b64"]) for e in tx if e.get("action") == "interrupt"] == [b"\x03", b"\x03"]
        assert any(e["type"] == "rx" for e in captured)
        assert not recovered["log_error"], recovered
        return {"port": port, "passed": True, "commands": results,
                "exclusive_com": True, "timeout_late_completion": True,
                "disconnect_late_completion": True, "monitor_tx_count": len(tx), "no_retry": True,
                "interrupt_results": interrupt_results, "cwd_results": cwd_results, "terminal_results": terminal_results}
    finally:
        if monitor:
            monitor.shutdown(socket.SHUT_RDWR)
        if reader:
            reader.join(3)
        if stream:
            stream.close()
        if monitor:
            monitor.close()
        # 僅終止本測試建立的 Server；各正常命令均已觀察到完成。
        process.terminate()
        process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ports", nargs="+", required=True, help="已登入 POSIX Shell 的 COM，例如 COM3 COM4")
    parser.add_argument("--tcp-port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--interrupt", action="store_true", help="另測試 Ctrl+C 中斷 sleep 與逾時恢復")
    parser.add_argument("--test-cwd", action="store_true", help="另測試 /tmp 工作目錄、失敗保護及不保存目錄")
    parser.add_argument("--test-terminal", action="store_true", help="另測試互動終端、cd、Ctrl+C 及手動恢復 exec；不重開機或登入")
    args = parser.parse_args()
    results = []
    for port in args.ports:
        result = check_port(port, args.tcp_port, args.interrupt, args.test_cwd, args.test_terminal)
        results.append(result)
        print(json.dumps(result, ensure_ascii=True), flush=True)
    Path("logs").mkdir(exist_ok=True)
    Path("logs/hardware-summary.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
