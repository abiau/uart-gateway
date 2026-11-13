"""Windows 單 UART Server；UART Reader 不等待任何 TCP Client。"""

import argparse
from datetime import datetime, timezone
import json
import logging.handlers
from pathlib import Path
import queue
import select
import socket
import socketserver
import sys
import threading
import time
import uuid

from protocol import (DEFAULT_HOST, DEFAULT_PORT, ShellMarkers, b64, encode,
                      read_message, shell_payload, validate_exec)


class TrafficLog:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        self.error = None

    def write(self, event):
        if self.error:
            return
        # 直接寫入，避免 logging 預設吞掉磁碟寫入錯誤。
        try:
            wire = json.dumps(event, ensure_ascii=True, separators=(",", ":"))
            record = logging.LogRecord("uart", logging.INFO, "", 0, wire, (), None)
            if self.handler.shouldRollover(record):
                self.handler.doRollover()
            self.handler.stream.write(wire + "\n")
            self.handler.flush()
        except OSError as exc:
            self.error = str(exc)

    def close(self):
        self.handler.close()


class Gateway:
    def __init__(self, serial_port, log, newline="cr"):
        self.serial = serial_port
        self.log = log
        self.newline = newline
        self.condition = threading.Condition(threading.RLock())
        self.stopping = threading.Event()
        self.active = self.last = None
        self.interrupting = False
        self.fault = None
        self.last_rx = None
        self.sequence = 0
        self.monitors = set()
        self.reader = threading.Thread(target=self._read_uart, name="uart-rx", daemon=True)

    def start(self):
        self.reader.start()

    def _event(self, kind, **fields):
        # 呼叫者持有 condition；序號、Log 與 Monitor 使用同一順序。
        self.sequence += 1
        event = {"type": kind, "seq": self.sequence,
                 "time": datetime.now(timezone.utc).isoformat(), **fields}
        self.log.write(event)
        for monitor in tuple(self.monitors):
            try:
                monitor.put_nowait(event)
            except queue.Full:
                self.monitors.remove(monitor)
        return event

    def _fail(self, exc):
        self.fault = str(exc)
        if self.active:
            self.active["reason"] = "uart_error"
        self._event("error", error="uart_error", message=self.fault)
        self.condition.notify_all()

    def _read_uart(self):
        while not self.stopping.is_set():
            data = b""
            read_error = None
            try:
                data = self.serial.read(max(1, min(self.serial.in_waiting, 4096)))
                if data and len(data) < 4096:
                    # 首 byte 喚醒後立即取走已到達的資料；不額外等待、不跨越 4096 bytes。
                    available = min(self.serial.in_waiting, 4096 - len(data))
                    if available:
                        data += self.serial.read(available)
            except OSError as exc:
                read_error = exc
            with self.condition:
                if data:
                    self.last_rx = datetime.now(timezone.utc).isoformat()
                    self._event("rx", data_b64=b64(data))
                    if self.active and not self.fault and not self.active["parser"].done:
                        execution = self.active
                        execution["parser"].feed(data)
                        if execution["parser"].done:
                            execution["reason"] = None
                            if not execution["writing"]:
                                self.active = None
                            self._event("completed", id=execution["id"],
                                        exit_code=execution["parser"].exit_code)
                if read_error:
                    # 第二次讀取失敗仍先發布第一次已收到的 bytes，不能因合併而丟資料。
                    if not self.stopping.is_set():
                        self._fail(read_error)
                    return
                if (self.active and not self.active["parser"].done and not self.active["reason"]
                        and time.monotonic() >= self.active["deadline"]):
                    self.active["reason"] = "timeout"
                    self._event("uncertain", id=self.active["id"], reason="timeout")
                self.condition.notify_all()

    def result(self, execution):
        with self.condition:
            parser = execution["parser"]
            completed = parser.done
            return {"type": "result", "id": execution["id"],
                    "state": "completed" if completed else "unknown" if execution["reason"] else "running",
                    "reason": execution["reason"], "exit_code": parser.exit_code if completed else None,
                    "output_b64": b64(parser.output), "output_bytes": parser.observed_bytes,
                    "output_complete": completed and not parser.truncated,
                    "output_truncated": parser.truncated, "may_still_be_running": not completed,
                    "interrupt_requested": execution["interrupt_requested"], "cwd": execution["cwd"]}

    def status(self):
        with self.condition:
            shell = "uncertain" if self.fault else (
                "uncertain" if self.active and self.active["reason"] else "running" if self.active else
                "idle" if self.last else "unknown")
            return {"type": "status", "uart": "error" if self.fault else "open",
                    "features": ["cwd"],
                    "port": self.serial.port, "baudrate": self.serial.baudrate,
                    "shell": shell, "uart_error": self.fault, "last_rx": self.last_rx,
                    "log_error": self.log.error, "monitor_count": len(self.monitors),
                    "last_execution": self.result(self.last) if self.last else None}

    def execute(self, request):
        command, timeout, maximum = validate_exec(request)
        cwd = request.get("cwd")
        command_id = uuid.uuid4().hex
        payload = shell_payload(command, command_id, self.newline, cwd=cwd)
        with self.condition:
            if self.fault or self.stopping.is_set():
                return None, {"type": "error", "error": "uart_unavailable", "message": self.fault or "Server 關閉中"}
            if self.active or self.interrupting:
                return None, {"type": "error", "error": "busy", "id": self.active["id"] if self.active else None,
                              "message": "前次命令尚未確認完成，不會排隊或重送"}
            execution = {"id": command_id, "parser": ShellMarkers(command_id, maximum),
                         "deadline": time.monotonic() + timeout, "reason": None, "writing": True,
                         "interrupt_requested": False, "command": command, "cwd": cwd}
            self.active = self.last = execution
            # TX 記錄是一次寫入嘗試；accepted_bytes 才代表 Driver 回報的接受量。
            self._event("tx", id=command_id, command=command, cwd=cwd, data_b64=b64(payload))
        # 阻塞的 UART write 不持有狀態鎖，RX 可在 TX 期間持續接收與解析。
        # writing 完成前保留 active，避免完成標記提早到達時接受下一次寫入。
        try:
            count = self.serial.write(payload)
            with self.condition:
                self._event("tx_result", id=command_id, accepted_bytes=count)
                if count != len(payload):
                    raise OSError("UART 部分寫入，命令結果不明")
        except OSError as exc:
            with self.condition:
                self._fail(exc)
        finally:
            with self.condition:
                execution["writing"] = False
                if execution["parser"].done and not self.fault:
                    self.active = None
        return execution, None

    def interrupt(self):
        with self.condition:
            if self.fault or self.stopping.is_set():
                return {"type": "error", "error": "uart_unavailable", "message": self.fault or "Server 關閉中"}
            if self.interrupting or (self.active and self.active["writing"]):
                return {"type": "error", "error": "tx_busy", "message": "UART 正在寫入，不會排隊或重送中斷"}
            self.interrupting = True
            execution = self.active
            command_id = execution["id"] if execution else None
            if execution:
                execution["interrupt_requested"] = True
            self._event("tx", id=command_id, action="interrupt", data_b64=b64(b"\x03"))
        count = None
        try:
            count = self.serial.write(b"\x03")
            with self.condition:
                self._event("tx_result", id=command_id, action="interrupt", accepted_bytes=count)
            if count != 1:
                raise OSError("UART 中斷字元未完整寫入，結果不明")
        except OSError as exc:
            with self.condition:
                self._fail(exc)
            return {"type": "interrupt", "id": command_id, "state": "unknown",
                    "reason": "uart_error", "accepted_bytes": count}
        finally:
            with self.condition:
                self.interrupting = False
                self.condition.notify_all()
        # Driver 接受 Ctrl+C 並非設備停止的證據，原命令鎖由 RX 完成標記解除。
        return {"type": "interrupt", "id": command_id, "state": "written_to_driver",
                "accepted_bytes": count, "message": "已送出一次 Ctrl+C；請用 status 確認原命令是否完成"}

    def detach(self, execution):
        with self.condition:
            if self.active is execution and not execution["reason"]:
                execution["reason"] = "client_disconnected"
                self._event("uncertain", id=execution["id"], reason=execution["reason"])

    def close(self):
        self.stopping.set()
        with self.condition:
            if self.active:
                self.active["reason"] = "server_stopped"
            self.condition.notify_all()
        self.reader.join(timeout=2)
        self.serial.close()
        self.log.close()


def peer_closed_or_sent(sock):
    readable, _, _ = select.select([sock], [], [], 0)
    if readable:
        # 一個連線只接受一筆請求；後續資料也視為協定違反。
        return True
    return False


class Handler(socketserver.StreamRequestHandler):
    def send(self, message):
        self.request.sendall(encode(message))

    def handle(self):
        gateway = self.server.gateway
        self.request.settimeout(5)
        try:
            request = read_message(self.rfile)
            if request.get("op") == "hello":
                # 與 exec 共用同一連線；確認功能後不會因 Server 換版而改送到另一個程序。
                self.send({"type": "hello", "features": ["cwd"]})
                request = read_message(self.rfile)
            operation = request.get("op")
            if operation == "status":
                self.send(gateway.status())
            elif operation == "interrupt":
                self.send(gateway.interrupt())
            elif operation == "monitor":
                self.monitor(gateway)
            elif operation == "exec":
                execution, error = gateway.execute(request)
                if error:
                    self.send(error)
                    return
                try:
                    self.send({"type": "accepted", "id": execution["id"]})
                    while not gateway.stopping.is_set():
                        with gateway.condition:
                            if execution["parser"].done or execution["reason"]:
                                break
                            gateway.condition.wait(0.1)
                        if peer_closed_or_sent(self.request):
                            gateway.detach(execution)
                            return
                    self.send(gateway.result(execution))
                finally:
                    gateway.detach(execution)
            else:
                self.send({"type": "error", "error": "invalid_request", "message": "只接受 exec、monitor、status、interrupt"})
        except (ValueError, UnicodeError, RecursionError) as exc:
            try:
                self.send({"type": "error", "error": "invalid_request", "message": str(exc)})
            except OSError:
                pass
        except OSError:
            pass

    def monitor(self, gateway):
        events = queue.Queue(maxsize=128)
        with gateway.condition:
            gateway.monitors.add(events)
            next_seq = gateway.sequence + 1
            active = gateway.active
            execution = ({"id": active["id"], "started": active["parser"].started,
                          "command": active["command"], "cwd": active["cwd"]}
                         if active and not active["parser"].done else None)
            if execution:
                parser = active["parser"]
                # 提供有界的標記解析前綴，讓中途加入者接得上跨 RX 區塊的框架。
                if parser.started:
                    separator = (b"\r\n" if parser.tail == b"\r\n" else b"\n" if parser.tail.endswith(b"\n")
                                 else b"\r" if parser.tail.endswith(b"\r") else b"")
                    prefix = separator + bytes(parser.pending)
                else:
                    prefix = b"\n" + bytes(parser.pending) if parser.line_start else b""
                execution["prefix_b64"] = b64(prefix)
        # 慢速／停止接收的 Monitor 最多阻塞自己的執行緒。
        self.request.settimeout(1)
        try:
            self.send({"type": "monitor", "next_seq": next_seq, "execution": execution})
            while not gateway.stopping.is_set():
                with gateway.condition:
                    overflow = events not in gateway.monitors
                if overflow:
                    self.send({"type": "error", "error": "monitor_overflow", "message": "監控資料有缺口，請重新連線"})
                    return
                if peer_closed_or_sent(self.request):
                    return
                try:
                    self.send(events.get(timeout=0.1))
                except queue.Empty:
                    pass
        finally:
            with gateway.condition:
                gateway.monitors.discard(events)


class TCPServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, address, gateway):
        self.gateway = gateway
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--serial", default="COM3")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--newline", choices=("cr", "lf", "crlf"), default="cr")
    parser.add_argument("--log", default="logs/uart.ndjson")
    args = parser.parse_args()
    try:
        import serial
    except ImportError:
        print('尚未安裝 pyserial，請執行 python -m pip install -e ".[server]"', file=sys.stderr)
        return 2
    uart = log = gateway = None
    try:
        # 開啟前設定控制線；不主動送出 CR、登入字串或探測命令。
        uart = serial.Serial(port=None, baudrate=args.baud, bytesize=8, parity="N", stopbits=1,
                             timeout=0.1, write_timeout=1, xonxoff=False, rtscts=False, dsrdtr=False)
        uart.dtr = uart.rts = False
        uart.port = args.serial
        uart.open()
        log = TrafficLog(args.log)
        gateway = Gateway(uart, log, args.newline)
        with TCPServer((args.host, args.port), gateway) as server:
            gateway.start()
            print(f"UART {args.serial} {args.baud} 8N1 → {args.host}:{args.port}", flush=True)
            try:
                server.serve_forever(poll_interval=0.1)
            except KeyboardInterrupt:
                pass
    except (OSError, ValueError) as exc:
        print(f"啟動失敗：{exc}", file=sys.stderr)
        return 2
    finally:
        if gateway and gateway.reader.ident is not None:
            gateway.close()
        else:
            if uart:
                uart.close()
            if log:
                log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
