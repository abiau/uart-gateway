"""最小 CLI；所有 UART 操作都透過 SDK。"""

import argparse
import asyncio
import codecs
import json
import os
from pathlib import Path
import sys

from .client import Client
from .protocol.codec import binary_decode
from .protocol.errors import ProtocolError, RemoteError
from .text_renderer import RESET, TerminalRenderer


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


async def _display(text: str) -> None:
    """輸出已完成安全轉換或終端過濾的文字。"""
    if not text:
        return
    stream = sys.stdout

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


async def watch_client(client: Client, channel_id: str, *, duration: float | None,
                       text_mode: bool, replay: bool, terminal_mode: bool = False) -> int:
    text_mode = text_mode or terminal_mode
    renderer = TerminalRenderer() if terminal_mode else None
    if replay:
        channel = await client.get_channel(channel_id)
        if channel["stream_epoch"] is None:
            raise ValueError("通道沒有可讀資料流，請先查看 channels 的 last_error")
        sub = await client.subscribe(channel_id, epoch=channel["stream_epoch"],
                                     offset=channel["rx"]["earliest_offset"])
    else:
        sub = await client.subscribe(channel_id)
    decoder = codecs.getincrementaldecoder("utf-8")(errors="backslashreplace")
    last_delivery = None
    text_parts: list[str] = []
    text_size = 0
    output_failed = False
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
                await (_display(text) if terminal_mode else display_text(text))
            except Exception:
                output_failed = True
                raise
        flush_at = loop.time() + 0.02

    async def diagnostic(message: str) -> None:
        await flush_text()
        if terminal_mode:
            await _display(RESET)
        print(message, file=sys.stderr, flush=True)

    failure: BaseException | None = None
    observation = asyncio.timeout(duration)
    try:
        if text_mode:
            await diagnostic(json.dumps({"subscription": sub}, ensure_ascii=True))
        else:
            await output({"subscription": sub})
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
                else:
                    await output(event)
                if event["event"] == "subscription_state" and event["data"]["state"] == "ended":
                    return 2
    except TimeoutError as exc:
        if not observation.expired():
            failure = exc
            raise
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            if text_mode and not output_failed:
                tail = decoder.decode(b"", final=True)
                if renderer is not None:
                    tail = renderer.feed(tail) + renderer.finish()
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
                await client.unsubscribe(sub["subscription_id"])
            except BaseException as exc:
                if failure is None:
                    raise
                failure.add_note(f"取消訂閱另有錯誤：{exc}")
    return 0


async def run(args: argparse.Namespace) -> int:
    if args.command in ("watch", "local") and args.terminal and not sys.stdout.isatty():
        raise ValueError("終端顯示需要互動式終端機；輸出到檔案或管線時請使用一般文字模式。")
    if args.command == "local":
        from .local_launcher import launch_local
        if args.host != "127.0.0.1" or args.token_file or args.token_env != "UART_GATEWAY_TOKEN":
            raise ValueError("本機啟動器只使用 Loopback 與自行產生的臨時認證")
        async def monitor(client: Client, channel_id: str) -> int:
            return await watch_client(client, channel_id, duration=args.duration,
                                      text_mode=True, replay=True, terminal_mode=args.terminal)
        return await launch_local(args.serial_port, monitor,
                                  gateway_port=args.gateway_port if args.gateway_port is not None else args.port,
                                  startup_timeout=args.startup_timeout)
    token = (Path(args.token_file).read_text(encoding="utf-8").strip() if args.token_file
             else os.environ.get(args.token_env, ""))
    if not token:
        raise ValueError("請透過環境變數或受保護的檔案提供 Token")
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
    local = commands.add_parser("local", help="啟動自己的只讀 Gateway 並監控 RX")
    local.add_argument("--port", dest="serial_port", default="COM3", help="本次使用的 Serial 端點")
    local.add_argument("--read-only", action="store_true", help="只讀模式（此版本固定只讀）")
    local.add_argument("--watch", action="store_true", help="持續監控 RX（此版本預設行為）")
    local.add_argument("--terminal", action="store_true", help="以終端顯示換行、退格及顏色；過濾其他控制序列")
    local.add_argument("--gateway-port", type=int, help="本機 Gateway Port，預設 18081")
    local.add_argument("--startup-timeout", type=float, default=20, help="等待就緒的秒數")
    local.add_argument("--duration", type=float, help="監控秒數；省略則持續到 Ctrl+C")
    local.add_argument("--pause-on-error", action="store_true", help="失敗時按 Enter 關閉視窗，供雙擊入口使用")
    for name, help_text in (("confirm", "人工確認目前接線"), ("rebind", "停止通道以準備換板"),
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
    if args.command in ("watch", "local") and args.duration is not None and not args.duration > 0:
        parser.error("觀測時間必須大於零")
    try:
        raise SystemExit(asyncio.run(run(args)))
    except (RemoteError, ProtocolError, OSError, ValueError, TimeoutError) as exc:
        print(f"操作失敗：{getattr(exc, 'code', type(exc).__name__)}：{exc}", file=sys.stderr)
        if args.command == "local" and args.pause_on_error and sys.stdin.isatty():
            try:
                input("按 Enter 關閉此視窗。")
            except (EOFError, KeyboardInterrupt):
                pass
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
