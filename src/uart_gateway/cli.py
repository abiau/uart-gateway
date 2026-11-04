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


async def output(value: object) -> None:
    line = json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n"
    await asyncio.to_thread(sys.stdout.write, line)
    await asyncio.to_thread(sys.stdout.flush)


def safe_text(text: str) -> str:
    """文字顯示保留 LF／TAB，其他控制字元跳脫，不執行 ANSI。"""
    return "".join(char if char.isprintable() or char in "\n\t" else
                   char.encode("unicode_escape").decode("ascii") for char in text)


async def display_text(text: str) -> None:
    await asyncio.to_thread(sys.stdout.write, safe_text(text))
    await asyncio.to_thread(sys.stdout.flush)


async def run(args: argparse.Namespace) -> int:
    token = (Path(args.token_file).read_text(encoding="utf-8").strip() if args.token_file
             else os.environ.get(args.token_env, ""))
    if not token:
        raise ValueError("請透過環境變數或受保護的檔案提供 Token")
    async with await Client.connect(args.host, args.port, token) as client:
        if args.command == "channels":
            await output(await client.list_channels())
        elif args.command == "watch":
            if args.replay:
                channel = await client.get_channel(args.channel)
                if channel["stream_epoch"] is None:
                    raise ValueError("通道沒有可讀資料流，請先查看 channels 的 last_error")
                sub = await client.subscribe(args.channel, epoch=channel["stream_epoch"],
                                             offset=channel["rx"]["earliest_offset"])
            else:
                sub = await client.subscribe(args.channel)
            decoder = codecs.getincrementaldecoder("utf-8")(errors="backslashreplace")
            last_delivery = None
            if args.text:
                print(json.dumps({"subscription": sub}, ensure_ascii=True), file=sys.stderr, flush=True)
            else:
                await output({"subscription": sub})
            try:
                async with asyncio.timeout(args.duration):
                    while True:
                        event = await client.next_event()
                        if args.text and event["event"] == "rx":
                            delivery = event["data"]["delivery"]
                            if delivery != last_delivery:
                                mode = "歷史重播" if delivery == "replay" else "即時接收"
                                print(f"接收模式：{mode}", file=sys.stderr, flush=True)
                                last_delivery = delivery
                            await display_text(decoder.decode(binary_decode(event["data"]["data_b64"])))
                        elif args.text:
                            print(json.dumps(event, ensure_ascii=True), file=sys.stderr, flush=True)
                        else:
                            await output(event)
                        if event["event"] == "subscription_state" and event["data"]["state"] == "ended":
                            return 2
            except TimeoutError:
                pass
            finally:
                if args.text:
                    await display_text(decoder.decode(b"", final=True))
                await client.unsubscribe(sub["subscription_id"])
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
    parser = argparse.ArgumentParser(description="UART Gateway 命令列；輸出為 JSON Lines")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18081)
    secrets = parser.add_mutually_exclusive_group()
    secrets.add_argument("--token-file", help="受保護的 Token 檔案")
    secrets.add_argument("--token-env", default="UART_GATEWAY_TOKEN", help="Token 所在的環境變數名稱")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("channels", help="列出通道")
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
            command.add_argument("--text", action="store_true", help="顯示 UTF-8 文字，跳脫控制字元；不改變原始 RX")
            command.add_argument("--replay", action="store_true", help="先讀此 Epoch 最早仍保留的資料，再接即時 RX")
        elif name == "send":
            command.add_argument("text")
            command.add_argument("--newline", choices=("none", "lf", "cr", "crlf"), default="none")
        elif name == "status":
            command.add_argument("tx_id")
    args = parser.parse_args()
    if args.command == "watch" and not args.duration > 0:
        parser.error("觀測時間必須大於零")
    try:
        raise SystemExit(asyncio.run(run(args)))
    except (RemoteError, ProtocolError, OSError, ValueError, TimeoutError) as exc:
        print(f"操作失敗：{getattr(exc, 'code', type(exc).__name__)}：{exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
