"""最小 CLI；所有 UART 操作都透過 SDK。"""

import argparse
import asyncio
import codecs
import json
import math
import os
from pathlib import Path
import sys
from typing import TYPE_CHECKING

from .client import Client
from .client.transport import LocalDataLoss
from .protocol.codec import binary_decode
from .protocol.errors import ProtocolError, RemoteError
from .text_renderer import RESET, TaggedText, TerminalRenderer

if TYPE_CHECKING:
    from .local_profile import LocalChannel


class ChineseHelpFormatter(argparse.HelpFormatter):
    def _format_usage(self, usage, actions, groups, prefix):
        return super()._format_usage(usage, actions, groups, "用法：" if prefix is None else prefix)


class ChineseArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs["add_help"] = False
        kwargs.setdefault("formatter_class", ChineseHelpFormatter)
        super().__init__(*args, **kwargs)
        self._positionals.title, self._optionals.title = "位置參數", "選項"
        self.add_argument("-h", "--help", action="help", help="顯示說明並結束")

    def error(self, message):
        self.print_usage(sys.stderr)
        translated = message if any("\u4e00" <= char <= "\u9fff" for char in message) else "參數不合法，請使用 --help 查看用法。"
        self.exit(2, f"參數錯誤：{translated}\n")


class StreamSelectionChanged(ValueError):
    """選單快照已失效；不可顯示另一個資料流。"""


def read_client_token(args: argparse.Namespace) -> str:
    try:
        token = (Path(args.token_file).read_text(encoding="utf-8").strip() if args.token_file
                 else os.environ.get(args.token_env, ""))
    except (OSError, UnicodeError) as exc:
        raise ValueError("無法讀取 Token 檔案，請確認路徑、權限與 UTF-8 編碼。") from exc
    if not token:
        raise ValueError("請透過環境變數或受保護的檔案提供 Token")
    return token


async def output(value: object) -> None:
    line = json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n"
    await asyncio.to_thread(sys.stdout.write, line)
    await asyncio.to_thread(sys.stdout.flush)


def safe_text(text: str) -> str:
    """文字顯示保留 LF／TAB，其他控制字元跳脫，不執行 ANSI。"""
    return "".join(char if char.isprintable() or char in "\n\t" else
                   char.encode("unicode_escape").decode("ascii") for char in text)


async def display_text(text: str) -> None:
    await _display(safe_text(text))


async def _display(text: str, *, stream=None) -> None:
    """輸出已完成安全轉換或終端過濾的文字。"""
    if not text:
        return
    stream = sys.stdout if stream is None else stream

    def write() -> None:
        stream.write(text)
        stream.flush()

    # 執行緒中的 write 無法取消；等待已開始的輸出收斂，避免清理時交錯寫入。
    pending = asyncio.create_task(asyncio.to_thread(write))
    try:
        await asyncio.shield(pending)
    except asyncio.CancelledError:
        await pending
        raise


class _WatchOutput:
    """監控專用的有界輸出；慢速終端不直接暫停 SDK 事件消費。"""

    def __init__(self, *, terminal: bool, maximum: int = 262144):
        self.terminal, self.maximum, self.used = terminal, maximum, 0
        self.queue = asyncio.Queue(maxsize=256)
        self.task = asyncio.create_task(self._run())

    def put(self, text: str, *, diagnostic: bool = False) -> None:
        if self.task.done():
            self.task.result()
            raise RuntimeError("監控輸出已結束")
        if not text:
            return
        size = len(text.encode("utf-8"))
        if self.queue.full() or self.used + size > self.maximum:
            raise LocalDataLoss("CLI 顯示佇列已滿，終端輸出持續跟不上接收速度；資料連續性中斷。")
        self.used += size
        self.queue.put_nowait((text, diagnostic, size))

    async def _run(self) -> None:
        try:
            while (item := await self.queue.get()) is not None:
                text, diagnostic, size = item
                try:
                    if diagnostic:
                        await _display(text, stream=sys.stderr)
                    elif self.terminal:
                        await _display(text)
                    else:
                        await display_text(text)
                finally:
                    # 使用量包含正在寫入的批次，不能提早釋放其額度。
                    self.used -= size
            if self.terminal:
                # 由 Writer 收尾；即使 Producer 因額度已滿失敗，仍能恢復顏色。
                await _display(RESET)
        finally:
            while not self.queue.empty():
                self.queue.get_nowait()
            self.used = 0

    async def close(self) -> None:
        if not self.task.done():
            await self.queue.put(None)
        await self.task


async def watch_client(client: Client, channel_id: str, *, duration: float | None,
                       text_mode: bool, replay: bool, terminal_mode: bool = False,
                       shared_sink: _WatchOutput | None = None, label: str | None = None,
                       expected_context: tuple[str, str, str | None] | None = None) -> int:
    text_mode = text_mode or terminal_mode
    renderer = TerminalRenderer() if terminal_mode else None
    if expected_context is not None and client.instance != expected_context[0]:
        raise StreamSelectionChanged("Gateway 已重新啟動，請按 R 更新清單後重新選擇。")
    if replay or expected_context is not None:
        channel = await client.get_channel(channel_id)
        if expected_context is not None and (channel["state"] != "ready" or
                (client.instance, channel["binding"]["revision"], channel["stream_epoch"]) != expected_context):
            raise StreamSelectionChanged("所選通道狀態或資料流已改變，請按 R 更新清單後重新選擇。")
    if replay:
        if channel["stream_epoch"] is None:
            raise ValueError("通道沒有可讀資料流，請先查看 channels 的 last_error")
        sub = await client.subscribe(channel_id, epoch=channel["stream_epoch"],
                                     offset=channel["rx"]["earliest_offset"])
    else:
        sub = await client.subscribe(channel_id)
    if expected_context is not None and (client.instance, sub["binding_revision"], sub["stream_epoch"]) != expected_context:
        error = StreamSelectionChanged("訂閱時資料流已改變，未顯示 RX；請按 R 更新清單後重新選擇。")
        try:
            await client.unsubscribe(sub["subscription_id"])
        except Exception as exc:
            error.add_note(f"取消失效訂閱另有錯誤：{type(exc).__name__}")
        raise error
    decoder = codecs.getincrementaldecoder("utf-8")(errors="backslashreplace")
    last_delivery = None
    text_parts: list[str] = []
    text_size = 0
    output_failed = False
    sink = shared_sink if shared_sink is not None else _WatchOutput(terminal=terminal_mode)
    tagged = TaggedText(label, terminal=terminal_mode) if label else None
    loop = asyncio.get_running_loop()
    flush_at = loop.time() + 0.02

    async def flush_text() -> None:
        nonlocal text_size, flush_at, output_failed
        if text_parts:
            text = "".join(text_parts)
            # await 前移出此批；取消或部分寫入失敗時都不能重送同一批文字。
            text_parts.clear()
            text_size = 0
            try:
                rendered = text if terminal_mode else safe_text(text)
                sink.put(tagged.format(rendered) if tagged else rendered)
            except Exception:
                output_failed = True
                raise
        flush_at = loop.time() + 0.02

    async def diagnostic(message: str) -> None:
        await flush_text()
        prefix = RESET if terminal_mode and sys.stderr.isatty() else ""
        source = f"[{label}] " if label else ""
        sink.put(prefix + source + message + "\n", diagnostic=True)

    async def consume() -> int:
        nonlocal last_delivery, text_size
        observation = asyncio.timeout(duration)
        if text_mode:
            await diagnostic(json.dumps({"subscription": sub}, ensure_ascii=True))
        else:
            sink.put(json.dumps({"subscription": sub}, ensure_ascii=True, separators=(",", ":")) + "\n")
        try:
            async with observation:
                while True:
                    if text_parts:
                        refresh = asyncio.timeout_at(flush_at)
                        try:
                            async with refresh:
                                event = await client.next_event()
                        except TimeoutError:
                            if not refresh.expired():
                                raise
                            await flush_text()
                            continue
                    else:
                        event = await client.next_event()
                    if text_mode and event["event"] == "rx":
                        delivery = event["data"]["delivery"]
                        if delivery != last_delivery:
                            mode = "歷史重播" if delivery == "replay" else "即時接收"
                            await diagnostic(f"接收模式：{mode}")
                            last_delivery = delivery
                        text = decoder.decode(binary_decode(event["data"]["data_b64"]))
                        if renderer is not None:
                            text = renderer.feed(text)
                        text_parts.append(text)
                        text_size += len(text)
                        if text_size >= 16384 or loop.time() >= flush_at:
                            await flush_text()
                    elif text_mode:
                        if renderer is not None and event["event"] in ("gap", "subscription_state"):
                            text_parts.append(renderer.feed(decoder.decode(b"", final=True)))
                            text_parts.append(renderer.finish())
                            decoder.reset()
                        await diagnostic(json.dumps(event, ensure_ascii=True))
                        if event["event"] == "recording_state" and event["data"]["state"] in ("failed", "degraded"):
                            await diagnostic("錄製已失敗或連續性不明；RX 監控仍持續，請檢查 Gateway 錄製訊息。")
                    else:
                        sink.put(json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n")
                    if event["event"] == "subscription_state" and event["data"]["state"] == "ended":
                        if text_mode:
                            reason = event["data"].get("reason")
                            message = {
                                "device_lost": "裝置已失去連線，監控已停止。",
                                "epoch_changed": "通道資料流已變更，監控已停止。",
                                "channel_closed": "通道已關閉，監控已停止。",
                                "server_shutdown": "Gateway 已停止服務，監控已停止。",
                                "slow_consumer": "接收速度跟不上資料，訂閱已停止。",
                                "history_gap": "所需歷史資料已有缺口，訂閱已停止。",
                            }.get(reason, "訂閱已結束，監控已停止。")
                            await diagnostic(message)
                        return 2
        except TimeoutError:
            if not observation.expired():
                raise
        return 0

    failure: BaseException | None = None
    consumer = asyncio.create_task(consume())
    try:
        done, _ = await asyncio.wait((consumer, sink.task), return_when=asyncio.FIRST_COMPLETED)
        if consumer in done:
            # 同時失敗時保留接收原因；Writer 錯誤在清理時附註。
            return await consumer
        await sink.task
        raise RuntimeError("監控輸出意外結束")
    except BaseException as exc:
        failure = exc
        raise
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
        try:
            if text_mode and not output_failed and not sink.task.done():
                tail = decoder.decode(b"", final=True)
                if renderer is not None:
                    tail = renderer.feed(tail)
                    renderer.finish()
                if tail:
                    text_parts.append(tail)
                await flush_text()
        except BaseException as exc:
            if failure is None:
                failure = exc
                raise
            failure.add_note(f"文字輸出清理另有錯誤：{exc}")
        finally:
            try:
                try:
                    await client.unsubscribe(sub["subscription_id"])
                except BaseException as exc:
                    if failure is None:
                        failure = exc
                        raise
                    failure.add_note(f"取消訂閱另有錯誤：{exc}")
            finally:
                # 停止訂閱後才等畫面完成，期間仍由 SDK Reader 處理 RPC。
                try:
                    if shared_sink is None:
                        await sink.close()
                except BaseException as exc:
                    if failure is None:
                        raise
                    if exc is not failure:
                        failure.add_note(f"畫面輸出清理另有錯誤：{exc}")


async def watch_channels(clients: tuple[Client, ...], channels: tuple["LocalChannel", ...], *,
                         duration: float | None, terminal_mode: bool) -> int:
    """每通道一條 SDK 接收佇列，共用有界 Writer；失去單條仍監控其餘通道。"""
    sink = _WatchOutput(terminal=terminal_mode)

    async def observe(client, channel) -> int:
        label = f"{channel.id}／{channel.label}"
        try:
            return await watch_client(client, channel.id, duration=duration, text_mode=True,
                replay=True, terminal_mode=terminal_mode, shared_sink=sink, label=label)
        except (OSError, RemoteError, ProtocolError, TimeoutError) as exc:
            sink.put(f"[{label}] 監控失敗：{getattr(exc, 'code', type(exc).__name__)}：{exc}\n",
                     diagnostic=True)
            return 2
        finally:
            await client.close()

    tasks = [asyncio.create_task(observe(client, channel)) for client, channel in zip(clients, channels, strict=True)]
    failure = None
    try:
        results = await asyncio.gather(*tasks)
        return 2 if all(result == 2 for result in results) else 0
    except BaseException as exc:
        failure = exc
        raise
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await sink.close()
        except BaseException as exc:
            if failure is None:
                raise
            if exc is not failure:
                failure.add_note(f"畫面輸出清理另有錯誤：{exc}")


def _check_local_args(args: argparse.Namespace) -> None:
    if args.host != "127.0.0.1" or args.token_file or args.token_env != "UART_GATEWAY_TOKEN":
        raise ValueError("本機列出／啟動只使用此電腦，不接受遠端位址或外部認證選項。")


def _report_failure(exc: Exception) -> None:
    print(f"操作失敗：{getattr(exc, 'code', type(exc).__name__)}：{exc}", file=sys.stderr, flush=True)


def _run_selected(args: argparse.Namespace) -> int:
    """選單與事件迴圈分開；每次啟動必須來自新的明確選擇。"""
    from .local_ports import choose_channels, choose_port
    from .local_profile import save_profile
    from .local_launcher import LauncherCleanupError, LauncherError, validate_local_settings

    _check_local_args(args)
    if args.terminal and not sys.stdout.isatty():
        raise ValueError("終端顯示需要互動式終端機；請移除 --terminal 再選擇裝置。")
    validate_local_settings(args.gateway_port if args.gateway_port is not None else args.port,
                            args.startup_timeout)
    save_path = args.save_profile
    if save_path is not None and save_path.exists():
        raise ValueError("配置檔已存在，請指定新的檔名；未覆蓋原配置。")
    group = args.count > 1 or save_path is not None
    recovering = False
    while True:
        if group:
            args.selected_channels = choose_channels(args.count, recovering=recovering)
            selected = args.selected_channels
            if selected and save_path is not None:
                save_profile(save_path, selected)
                print(f"已保存本次選擇：{save_path}；後續重新選擇不會覆蓋此檔。", file=sys.stderr, flush=True)
                save_path = None
        else:
            args.selected_adapter = choose_port(recovering=recovering)
            selected = args.selected_adapter
            if selected is not None:
                args.serial_port = selected.port
        if selected is None:
            print("已離開選單，未啟動新的 Gateway。", file=sys.stderr, flush=True)
            return 0
        try:
            result = asyncio.run(run(args))
        except LauncherCleanupError:
            raise
        except (LauncherError, RemoteError, ProtocolError, OSError, TimeoutError) as exc:
            _report_failure(exc)
        else:
            if result != 2:
                return result
        recovering = True
        print("本次流程已結束，已返回裝置選單；請重新選擇端點或取消離開。\n"
              "重新啟動會建立新的資料流，前次記憶體歷史不保留。", file=sys.stderr, flush=True)


async def run(args: argparse.Namespace) -> int:
    if args.command == "recording-play":
        from .recording_play import playback
        result = await playback(args.directory, terminal=args.terminal)
        return 0 if result["integrity"] == "no_known_gap" else 2
    if args.command == "recording-text":
        from .recording_text import export_text
        result = await asyncio.to_thread(export_text, args.directory, args.output)
        print(f"已匯出文字：{args.output}；RX bytes：{result['rx_bytes']}。", flush=True)
        if result["integrity"] != "no_known_gap":
            print("原錄製不完整或連續性未知，請查看輸出末尾的檢查摘要。", file=sys.stderr, flush=True)
            return 2
        return 0
    if args.command == "recording-info":
        from .recording_info import inspect_capture_summary
        result = await asyncio.to_thread(inspect_capture_summary, args.directory)
        states = {"no_known_gap": "對已觀測資料無已知缺口", "unknown": "連續性未知", "incomplete": "資料不完整"}
        print(f"錄製檢查：{states[result['integrity']]}；正常收尾：{'是' if result['closed_cleanly'] else '否'}；"
              f"分段：{result['segments']}；RX 區塊：{result['rx_chunks']}；RX bytes：{result['rx_bytes']}", flush=True)
        summary = result["summary"]

        def field(value: object) -> str:
            return safe_text(str(value)).replace("\n", "\\n").replace("\t", "\\t")

        if summary["identity"]:
            identity = summary["identity"]
            print(f"通道：{field(identity['channel_id'])}；Capture：{field(identity['capture_id'])}；"
                  f"Gateway Instance：{field(identity['gateway_instance_id'])}", flush=True)
        print(f"Epoch 統計：已列出 {len(summary['epochs'])} 個世代；末筆結束位移不包含右端點。", flush=True)
        for number, epoch in enumerate(summary["epochs"], 1):
            print(f"{number}. Epoch={field(epoch['epoch'])}；首筆位移：{epoch['first_offset'] if epoch['first_offset'] is not None else '無 RX'}；"
                  f"末筆結束位移：{epoch['last_offset'] if epoch['last_offset'] is not None else '無 RX'}；"
                  f"RX 區塊：{epoch['rx_chunks']}；RX bytes：{epoch['rx_bytes']}", flush=True)
        print(f"已列世代加總：RX 區塊：{summary['listed_rx_chunks']}；RX bytes：{summary['listed_rx_bytes']}。", flush=True)
        if summary["epochs_truncated"]:
            print(f"注意：Epoch 摘要達到數量或識別長度上限，未完整列出；未列 RX 區塊：{summary['omitted_rx_chunks']}；"
                  f"未列 RX bytes：{summary['omitted_rx_bytes']}；原完整性掃描仍繼續。", flush=True)
        print(f"含未列資料後與整份 RX 統計核對：{'一致' if summary['totals_match'] else '不一致'}。", flush=True)
        names = {"epoch_started": "資料流建立", "epoch_changed": "世代切換", "lost": "裝置遺失",
                 "faulted": "通道故障", "opening": "通道開啟中", "draining": "通道關閉中",
                 "closed": "通道已關閉", "gap": "已知資料缺口"}
        print("狀態／世代紀錄（依檔案順序，世代切換不單獨證明重開原因）：", flush=True)
        for event in summary["boundaries"]:
            context = (f"{field(event['previous_epoch'])} → {field(event['epoch'])}" if event["previous_epoch"]
                       else field(event["epoch"] or "無"))
            print(f"- {field(event['timestamp_utc'])}：{names[event['kind']]}；關聯 Epoch：{context}", flush=True)
        if summary["boundaries_truncated"]:
            print("注意：狀態／世代紀錄已達 256 筆上限，後續僅省略顯示，完整性檢查仍涵蓋全檔。", flush=True)
        if result["integrity"] != "no_known_gap":
            print("注意：以上摘要僅涵蓋可解析紀錄，不消除整份錄製的完整性警告。", flush=True)
        for issue in result["issues"]:
            print(f"注意：{issue}", flush=True)
        print("此結果不代表 UART／USB／Driver 前段零遺失。", flush=True)
        return 0 if result["integrity"] == "no_known_gap" else 2
    if args.command in ("watch", "local") and args.terminal and not sys.stdout.isatty():
        raise ValueError("終端顯示需要互動式終端機；輸出到檔案或管線時請使用一般文字模式。")
    if args.command == "ports":
        from .local_ports import list_ports
        _check_local_args(args)
        for line in await asyncio.to_thread(list_ports):
            print(line, flush=True)
        return 0
    if args.command == "local":
        from .local_launcher import launch_channels, launch_local
        from .local_profile import load_profile, resolve_profile
        _check_local_args(args)
        channels = getattr(args, "selected_channels", None)
        if args.profile is not None:
            saved = await asyncio.to_thread(load_profile, args.profile)
            channels = await asyncio.to_thread(resolve_profile, saved)
        if channels is not None:
            for channel in channels:
                print(f"[{channel.id}／{channel.label}] 本次端點：{channel.adapter.port}；115200／8N1／只讀。",
                      file=sys.stderr, flush=True)

            async def monitor_group(clients, selected) -> int:
                if len(selected) == 1:
                    return await watch_client(clients[0], selected[0].id, duration=args.duration,
                        text_mode=True, replay=True, terminal_mode=args.terminal)
                return await watch_channels(clients, selected, duration=args.duration, terminal_mode=args.terminal)

            return await launch_channels(channels, monitor_group,
                gateway_port=args.gateway_port if args.gateway_port is not None else args.port,
                startup_timeout=args.startup_timeout, record_directory=args.record_dir)
        selector = getattr(args, "selected_adapter", None)
        if args.select and selector is None:
            raise ValueError("尚未完成端點選擇，請使用 uart local --select 進入選單。")
        async def monitor(client: Client, channel_id: str) -> int:
            return await watch_client(client, channel_id, duration=args.duration,
                                      text_mode=True, replay=True, terminal_mode=args.terminal)
        return await launch_local(args.serial_port, monitor,
                                  gateway_port=args.gateway_port if args.gateway_port is not None else args.port,
                                  startup_timeout=args.startup_timeout, selector=selector,
                                  record_directory=args.record_dir)
    token = read_client_token(args)
    async with await Client.connect(args.host, args.port, token) as client:
        if args.command == "channels":
            await output(await client.list_channels())
        elif args.command == "watch":
            return await watch_client(client, args.channel, duration=args.duration,
                                      text_mode=args.text, replay=args.replay, terminal_mode=args.terminal)
        elif args.command == "status":
            await output(await client.tx_status(args.channel, args.tx_id))
        else:
            channel = await client.get_channel(args.channel)
            if args.command == "confirm":
                await output(await client.confirm(channel, args.label, args.note))
            elif args.command == "rebind":
                await output(await client.rebind(channel))
            elif args.command == "reopen":
                await output(await client.reopen_rx(channel))
                print("單通道 RX 已重新開啟；尚未 Confirm，不允許 TX，請重新選擇監看。",
                      file=sys.stderr, flush=True)
            elif args.command == "send":
                suffix = {"none": "", "lf": "\n", "cr": "\r", "crlf": "\r\n"}[args.newline]
                async with await client.acquire(channel) as lease:
                    pending = await lease.write_bytes((args.text + suffix).encode("utf-8"))
                    await output(pending)
                    result = await client.wait_tx(args.channel, pending["tx_id"])
                    await output(result)
                    return 0 if result["state"] == "written_to_driver" else 2
    return 0


def main() -> None:
    parser = ChineseArgumentParser(description="UART Gateway 命令列；支援 SDK 操作與本機只讀啟動")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18081)
    secrets = parser.add_mutually_exclusive_group()
    secrets.add_argument("--token-file", help="受保護的 Token 檔案")
    secrets.add_argument("--token-env", default="UART_GATEWAY_TOKEN", help="Token 所在的環境變數名稱")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("channels", help="列出通道")
    commands.add_parser("ports", help="只列出本機 Serial 裝置，不開啟端點")
    menu = commands.add_parser("menu", help="開啟只讀工作台：本機操作或連線既有 Gateway")
    menu_mode = menu.add_mutually_exclusive_group()
    menu_mode.add_argument("--remote", action="store_true", help="連線既有 Gateway，選擇一條 Channel 監看")
    menu_mode.add_argument("--recordings-dir", type=Path, default=Path("recordings"), help="本機錄製與瀏覽的根目錄，預設 recordings")
    menu.add_argument("--pause-on-error", action="store_true", help="失敗時按 Enter 關閉，供雙擊入口使用")
    play = commands.add_parser("recording-play", help="離線快速回看錄製，不連線 UART")
    play.add_argument("directory", type=Path, help="錄製的 Channel 目錄")
    play.add_argument("--terminal", action="store_true", help="在互動終端呈現換行、退格與允許的顏色")
    inspect = commands.add_parser("recording-info", help="離線檢查一個 Channel 的錄製目錄，不連線 UART")
    inspect.add_argument("directory", type=Path, help="包含 000001.jsonl 等分段的 Channel 目錄")
    export = commands.add_parser("recording-text", help="將錄製所有分段匯出成可讀 UTF-8 文字")
    export.add_argument("directory", type=Path, help="錄製的 Channel 目錄")
    export.add_argument("--output", type=Path, required=True, help="新文字檔路徑；不覆寫既有檔案")
    local = commands.add_parser("local", help="啟動自己的只讀 Gateway 並監控 RX")
    source = local.add_mutually_exclusive_group()
    source.add_argument("--port", dest="serial_port", default="COM3", help="本次使用的 Serial 端點，預設 COM3")
    source.add_argument("--select", action="store_true", help="列出本機裝置並互動選擇，不自動開啟第一個端點")
    source.add_argument("--profile", type=Path, help="重用本機配置；啟動前重新核對裝置條件")
    local.add_argument("--count", type=int, choices=(1, 2), default=1, help="互動選擇 UART 數量，預設 1；目前最多 2")
    local.add_argument("--save-profile", type=Path, help="將互動選擇保存至新配置檔，建議使用 .local.toml 副檔名")
    local.add_argument("--record-dir", type=Path, help="啟用 Gateway RX 錄製，保存至指定目錄下的新 Capture")
    local.add_argument("--read-only", action="store_true", help="只讀模式（此版本固定只讀）")
    local.add_argument("--watch", action="store_true", help="持續監控 RX（此版本預設行為）")
    local.add_argument("--terminal", action="store_true", help="顯示允許的顏色；單通道保留退格，多通道跳脫 CR／退格")
    local.add_argument("--gateway-port", type=int, help="本機 Gateway Port，預設 18081")
    local.add_argument("--startup-timeout", type=float, default=20, help="等待就緒的秒數")
    local.add_argument("--duration", type=float, help="監控秒數；省略則持續到 Ctrl+C")
    local.add_argument("--pause-on-error", action="store_true", help="失敗時按 Enter 關閉視窗，供雙擊入口使用")
    for name, help_text in (("confirm", "人工確認目前接線"), ("rebind", "停止通道以準備換板"),
                            ("reopen", "手動重開遺失／故障／已關閉通道的 RX，需要 configure 權限"),
                            ("watch", "訂閱 UART 事件"), ("send", "以租約保護的 TX 傳送文字"),
                            ("status", "查詢既有 TX 狀態")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("channel")
        if name == "confirm":
            command.add_argument("--label", required=True)
            command.add_argument("--note")
        elif name == "watch":
            command.add_argument("--duration", type=float, default=10)
            display = command.add_mutually_exclusive_group()
            display.add_argument("--text", action="store_true", help="顯示 UTF-8 文字，跳脫控制字元；不改變原始 RX")
            display.add_argument("--terminal", action="store_true", help="顯示換行、退格與 ANSI 顏色；過濾其他控制序列")
            command.add_argument("--replay", action="store_true", help="先讀此 Epoch 最早仍保留的資料，再接即時 RX")
        elif name == "send":
            command.add_argument("text")
            command.add_argument("--newline", choices=("none", "lf", "cr", "crlf"), default="none")
        elif name == "status":
            command.add_argument("tx_id")
    args = parser.parse_args()
    if args.command in ("watch", "local") and args.duration is not None and (
            not math.isfinite(args.duration) or not args.duration > 0):
        parser.error("觀測時間必須是大於零的有限秒數")
    if args.command == "local" and not args.select and (args.count != 1 or args.save_profile is not None):
        parser.error("--count 與 --save-profile 僅能搭配 --select 使用")
    try:
        if args.command == "menu":
            if args.remote:
                from .remote_workbench import run_remote_workbench

                async def connect() -> Client:
                    token = await asyncio.to_thread(read_client_token, args)
                    return await Client.connect(args.host, args.port, token)

                async def monitor(client: Client, channel_id: str, replay: bool, expected: tuple) -> int:
                    return await watch_client(client, channel_id, duration=None, text_mode=True,
                        terminal_mode=True, replay=replay, expected_context=expected)

                async def monitor_group(channels: list[dict], instance: str, replay: bool) -> int:
                    from .remote_group import run_remote_group
                    return await run_remote_group(connect, channels, instance, replay, watch=watch_client,
                        sink_factory=lambda: _WatchOutput(terminal=True))

                raise SystemExit(run_remote_workbench(connect, monitor, host=args.host, port=args.port,
                                                     monitor_group=monitor_group))
            from .workbench import run_workbench
            _check_local_args(args)

            def dispatch(argv: list[str]) -> int:
                child = parser.parse_args(["--port", str(args.port), *argv])
                if child.command == "local" and child.select:
                    return _run_selected(child)
                return asyncio.run(run(child))

            raise SystemExit(run_workbench(dispatch, recordings=args.recordings_dir))
        if args.command == "local" and args.select:
            raise SystemExit(_run_selected(args))
        raise SystemExit(asyncio.run(run(args)))
    except (RemoteError, ProtocolError, OSError, ValueError, TimeoutError) as exc:
        _report_failure(exc)
        if args.command in ("local", "menu") and args.pause_on_error and sys.stdin.isatty():
            try:
                input("按 Enter 關閉此視窗。")
            except (EOFError, KeyboardInterrupt):
                pass
        raise SystemExit(1) from None
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
