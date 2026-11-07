"""遠端 RX 工作台；查詢、監看與人工重開均使用獨立 SDK 連線。"""

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
        managers = {grant["channel_id"] for grant in client.auth["grants"] if "configure" in grant["permissions"]}
        channels = await client.list_channels()
        if "channel.reopen_rx" in client.hello.get("capabilities", []):
            channels = [dict(c, reopen_rx_available=True) if c["channel_id"] in managers else c for c in channels]
        return client.instance, sorted((c for c in channels if c["channel_id"] in allowed), key=lambda c: c["channel_id"])


async def monitor_channel(connect: Callable[[], Awaitable[Client]], monitor: Callable[..., Awaitable[int]],
                          channel: dict, instance: str, replay: bool) -> int:
    expected = (instance, channel["binding"]["revision"], channel["stream_epoch"])
    async with await connect() as client:
        return await monitor(client, channel["channel_id"], replay, expected)


async def reopen_channel(connect: Callable[[], Awaitable[Client]], channel: dict, instance: str) -> dict:
    async with await connect() as client:
        if client.instance != instance:
            raise ValueError("Gateway 已改變，未重開裝置；請按 R 更新並重新選擇。")
        current = await client.get_channel(channel["channel_id"])
        if client.expected(current) != client.expected(channel):
            raise ValueError("通道修訂或資料流已改變，未重開裝置；請按 R 更新並重新選擇。")
        return await client.reopen_rx(current)


def run_remote_workbench(connect: Callable[[], Awaitable[Client]], monitor: Callable[..., Awaitable[int]], *,
                         host: str, port: int, read: Callable[[], str] | None = None,
                         report: Callable[[str], None] | None = None,
                         monitor_group: Callable[..., Awaitable[int]] | None = None) -> int:
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
            report("認證失敗（AUTH_FAILED），請確認已初始化相同部署憑證，且 Gateway 已載入目前版本；按 R 才重新讀取憑證並連線。")
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
            report(f"\n遠端 RX 工作台：{label(host)}:{port}；不管理 Gateway 程序。")
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
                    if any(channel.get("reopen_rx_available") for channel in channels):
                        report("核對 USB／COM 後，選擇不可用的通道，可手動重新開啟該路 RX；其他通道繼續執行。")
                    else:
                        report("此連線未提供重開能力或 configure 權限；請由管理者手動重開該路 RX。")
                        report("舊 Gateway 可用原設定與同一 Token 手動重啟；這會中斷全部通道並清除記憶體歷史。")
            if recovering:
                prompt = ("輸入 Channel 編號；R 更新狀態；Enter 保持等待；Q 離開：" if connected else
                          "目前沒有監看連線；R 重新連線／查詢；Enter 保持等待；Q 離開：")
            else:
                prompt = "輸入 Channel 編號；R 更新／重新連線；Q 或 Enter 離開："
            if monitor_group is not None:
                report("雙路同時監看可輸入兩個不同編號，例如 1,2；上限兩路。")
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
            if monitor_group is not None and ("," in choice or " " in choice):
                parts = choice.replace(",", " ").split()
                if not (connected and len(parts) == 2 and all(p.isascii() and p.isdigit() and len(p) <= 6
                        and 1 <= int(p) <= len(channels) for p in parts) and int(parts[0]) != int(parts[1])):
                    report("請輸入兩個不同且有效的 Channel 編號，例如 1,2。")
                    continue
                selected = [channels[int(p) - 1] for p in parts]
                mode = ask("雙路模式：L 或 Enter 即時；R 歷史＋即時；其他輸入取消：")
                if mode not in ("", "l", "r"):
                    continue
                try:
                    asyncio.run(monitor_group(selected, instance, mode == "r"))
                except (KeyboardInterrupt, asyncio.CancelledError):
                    report("雙路監看已中止，SDK 連線已清理；Gateway 繼續執行。")
                except (OSError, ValueError, ProtocolError, RemoteError, TimeoutError) as exc:
                    failed(exc)
                connected, recovering = False, True
                report("已返回清單；按 R 重新查詢後選擇，Q 離開。")
                continue
            if not (connected and choice.isascii() and choice.isdigit() and len(choice) <= 6
                    and 1 <= int(choice) <= len(channels)):
                report("編號無效，請選擇清單編號或按 R／Q。")
                continue
            channel = channels[int(choice) - 1]
            if channel["state"] != "ready":
                if channel["state"] in ("lost", "faulted", "closed") and channel.get("reopen_rx_available"):
                    if ask("O 手動重新開啟此通道 RX；其他輸入返回清單：") != "o":
                        continue
                    report(f"即將重開 {label(channel['channel_id'])}／{label(channel.get('adapter', {}).get('port') or '原設定端點')}；"
                           "請核對原裝置與接線。成功後建立新世代，記憶體歷史清除；不 Confirm、不取得租約、不傳送指令。")
                    if ask("輸入 Y 確認本次單通道 RX 重開；其他輸入取消：") != "y":
                        report("已取消，未重開裝置。")
                        continue
                    try:
                        asyncio.run(reopen_channel(connect, channel, instance))
                        report("單通道 RX 已重新開啟，仍未 Confirm；請重新選擇監看。")
                    except (OSError, ValueError, ProtocolError, RemoteError, TimeoutError) as exc:
                        failed(exc)
                        report("未自動重試；請按 R 查詢結果，核對原因後再操作。占用時請先關閉 MobaXterm 等程式的 COM 連線。")
                        connected, recovering = False, True
                    else:
                        refresh = True
                    continue
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
                    report("若原因是 device_lost／DEVICE_MISSING，核對 USB／COM 後按 R，選擇通道進行有權限的手動 RX 重開。")
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
