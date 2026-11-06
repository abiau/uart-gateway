"""遠端只讀工作台；每次查詢／監看均擁有自己的 SDK 連線。"""

import asyncio
from collections.abc import Awaitable, Callable
import sys

from .client import Client
from .protocol.errors import ProtocolError, RemoteError
from .recording_text import visible


def label(value: object) -> str:
    text = str(value)
    return visible(text[:512]).replace("\n", "\\n").replace("\t", "\\t") + ("…" if len(text) > 512 else "")


async def query_channels(connect: Callable[[], Awaitable[Client]]) -> tuple[str, list[dict]]:
    async with await connect() as client:
        allowed = {grant["channel_id"] for grant in client.auth["grants"] if "read" in grant["permissions"]}
        channels = await client.list_channels()
        return client.instance, sorted((c for c in channels if c["channel_id"] in allowed), key=lambda c: c["channel_id"])


async def monitor_channel(connect: Callable[[], Awaitable[Client]], monitor: Callable[..., Awaitable[int]],
                          channel: dict, instance: str, replay: bool) -> int:
    expected = (instance, channel["binding"]["revision"], channel["stream_epoch"])
    async with await connect() as client:
        return await monitor(client, channel["channel_id"], replay, expected)


def run_remote_workbench(connect: Callable[[], Awaitable[Client]], monitor: Callable[..., Awaitable[int]], *,
                         host: str, port: int, read: Callable[[], str] | None = None,
                         report: Callable[[str], None] | None = None) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("遠端工作台需要互動式終端機輸入與輸出。")
    if not host or not host.isprintable() or type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Gateway 位址不可為空或包含控制字元；Port 必須為 1 至 65535。")
    read = read or input
    report = report or (lambda message: print(message, file=sys.stderr, flush=True))

    def ask(prompt: str) -> str:
        report(prompt)
        return read().strip().casefold()

    def failed(exc: Exception) -> None:
        code = getattr(exc, "code", type(exc).__name__)
        if code == "AUTH_FAILED":
            report("認證失敗（AUTH_FAILED），請核對 Token 來源與 Gateway；按 R 才重新讀取憑證並連線。")
        else:
            report(f"遠端操作失敗：{label(code)}：{label(exc)}")

    instance, channels = "", []
    refresh, connected, recovering = True, False, False
    try:
        while True:
            if refresh:
                refresh = False
                try:
                    instance, channels = asyncio.run(query_channels(connect))
                    connected = True
                    recovering = bool(channels) and not any(channel["state"] == "ready" for channel in channels)
                except (OSError, ValueError, ProtocolError, RemoteError, TimeoutError) as exc:
                    instance, channels, connected = "", [], False
                    recovering = True
                    failed(exc)
            report(f"\n遠端只讀工作台：{label(host)}:{port}；不管理 Gateway 程序。")
            if connected:
                report(f"Gateway Instance：{label(instance)}；下列為查詢快照，R 可更新。")
                if not channels:
                    report("目前沒有具 read 權限的 Channel。")
                for number, channel in enumerate(channels, 1):
                    states = {"ready": "就緒", "closed": "已關閉", "opening": "開啟中", "lost": "裝置遺失",
                              "faulted": "故障", "draining": "關閉中"}
                    recording = channel.get("recording", {})
                    error = channel.get("last_error") or {}
                    report(f"{number}. {label(channel['channel_id'])}／{label(channel['label'])} | "
                           f"狀態：{states.get(channel['state'], '未知')} | 端點：{label(channel.get('adapter', {}).get('port') or '未提供')} | "
                           f"錄製：{label(recording.get('state', '未提供'))}／{label(recording.get('integrity', '未提供'))}" +
                           (f" | 錯誤：{label(error.get('code'))}：{label(error.get('message'))}" if error else ""))
                if any(channel["state"] in ("lost", "faulted") for channel in channels):
                    report("Gateway 可查詢；清單中有 UART 通道不可用。R 僅更新狀態，不會重新開啟 Serial。")
                    report("請在 Gateway 電腦核對 USB／COM，使用原設定與同一 Token 手動重啟 Gateway，再按 R 重新選擇。")
                    report("重啟會中斷該 Gateway 的全部通道並清除記憶體歷史；請先確認其他通道可中斷。")
            if recovering:
                prompt = ("輸入 Channel 編號；R 更新狀態；Enter 保持等待；Q 離開：" if connected else
                          "目前沒有監看連線；R 重新連線／查詢；Enter 保持等待；Q 離開：")
            else:
                prompt = "輸入 Channel 編號；R 更新／重新連線；Q 或 Enter 離開："
            choice = ask(prompt)
            if choice == "q" or (not choice and not recovering):
                report("已離開遠端工作台，Gateway 繼續執行。")
                return 0
            if not choice:
                report("保持等待，未重新連線或開啟 Serial；按 R 查詢狀態，Q 離開。")
                continue
            if choice == "r":
                refresh = True
                continue
            if not (connected and choice.isascii() and choice.isdigit() and len(choice) <= 6
                    and 1 <= int(choice) <= len(channels)):
                report("編號無效，請選擇清單編號或按 R／Q。")
                continue
            channel = channels[int(choice) - 1]
            if channel["state"] != "ready":
                report("此 Channel 尚未就緒，請依上方原因處理，按 R 更新後再選擇。")
                continue
            while True:
                mode = ask("L 或 Enter 即時接收；R 最早仍保留的歷史＋即時接收；Q 返回清單：")
                if mode in ("", "l", "r", "q"):
                    break
                report("請輸入 L、R 或 Q。")
            if mode == "q":
                continue
            report(f"正在只讀監看 {label(channel['channel_id'])}；Ctrl+C 停止監看並返回清單，Gateway 繼續執行。")
            try:
                result = asyncio.run(monitor_channel(connect, monitor, channel, instance, mode == "r"))
                if result:
                    report("訂閱已停止，Gateway 程序可能仍在執行。R 僅重新連線／查詢，不會重開 Serial。")
                    report("若原因是 device_lost／DEVICE_MISSING，請在 Gateway 電腦恢復 UART 並手動重啟後再按 R。")
                    connected = False
                    recovering = True
            except (KeyboardInterrupt, asyncio.CancelledError):
                report("監看已中止，SDK 連線已清理；Gateway 繼續執行。")
            except (OSError, ValueError, ProtocolError, RemoteError, TimeoutError) as exc:
                failed(exc)
                connected = False
                recovering = True
    except EOFError:
        report("輸入來源已結束（EOF），已離開遠端工作台。")
        return 0
    except (KeyboardInterrupt, asyncio.CancelledError):
        report("已中止遠端工作台，Gateway 繼續執行。")
        return 130
