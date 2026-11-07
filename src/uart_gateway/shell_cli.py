"""Container／Codex 的命令入口；人工啟用與非互動執行分開。"""

import asyncio
import sys

from .client.connection import config_directory, connection_name
from .client.shell import ERRORS, ExecutionResult, ShellCancelled, ShellRunner, ShellTargets
from .client.tx_receipts import TxJournal
from .remote_workbench import label


def runner_for(args) -> ShellRunner:
    from .cli import client_connection
    if args.access not in (None, "tx"):
        raise ValueError("Shell 命令須明確使用 TX 憑證，不會提升讀取／管理權限。")
    args.access = "tx"
    settings = client_connection(args)
    name = connection_name(settings.name or "direct")
    root = args.connections_dir or config_directory()
    return ShellRunner(settings.connect, TxJournal(root / "tx-receipts" / name), ShellTargets(root / "shell-targets" / name))


def prepare_shell(args) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("shell-ready 需由人在互動終端核對，不能由管線或非互動 Agent 自動啟用。")
    runner = runner_for(args)
    target = asyncio.run(runner.inspect(args.channel))
    c, a = target.channel, target.channel.get("adapter") or {}
    print(f"Shell 目標：{label(c['channel_id'])}／{label(c['label'])}", flush=True)
    print(f"端點：{label(a.get('port') or '未提供')}；序號：{label(a.get('serial_number') or '未提供')}；換行：{args.newline.upper()}", flush=True)
    print(f"Gateway：{label(target.instance)}；修訂：{c['binding']['revision']}；世代：{label(c['stream_epoch'])}", flush=True)
    print("請核對實體接線、目前已登入 POSIX sh／BusyBox ash，且前次命令已結束。", flush=True)
    print("必要的 Confirm 會切換此通道世代；同通道監看需重訂閱。這一步不送探測／登入命令。", flush=True)
    try:
        accepted = input("輸入 Y 啟用此脈絡的命令操作；其他輸入取消：").strip().casefold() == "y"
    except EOFError:
        accepted = False
    if not accepted:
        print("已取消，未 Confirm 或傳送。", flush=True)
        return 0
    asyncio.run(runner.authorize(target, args.newline))
    print("已啟用。之後可用 uart exec；重啟／插拔／世代改變須重新由人核對。", flush=True)
    return 0


async def report(result: ExecutionResult, *, text: bool) -> None:
    from .cli import output, safe_text
    value = result.as_dict()
    if not text:
        await output(value)
        return
    if value["output_text"] is not None:
        await asyncio.to_thread(sys.stdout.write, safe_text(value["output_text"]))
        await asyncio.to_thread(sys.stdout.flush)
    print(f"\nShell 狀態：{value['state']}；exit code：{value['exit_code']}；操作：{value['execution_id']}；"
          f"原因：{value['reason_code'] or '無'}。", file=sys.stderr, flush=True)
    print("輸出為 UART 標記間資料，可能混有背景訊息；截斷／不明時不重送。", file=sys.stderr, flush=True)


async def run_shell(args) -> int:
    from .cli import output
    runner = None
    try:
        runner = await asyncio.to_thread(runner_for, args)
        if args.command == "exec":
            result = await runner.execute(args.channel, args.shell_command, timeout=args.timeout, maximum=args.max_output)
        else:
            result = await runner.status(args.execution_id, wait=args.wait)
        await report(result, text=args.text)
        return result.process_exit_code
    except ShellCancelled as exc:
        await report(exc.result, text=args.text)
        return 130
    except ERRORS as exc:
        # 已有收據就不能聲稱未傳送；仍以原識別供後續只查詢。
        receipt = runner.last if runner else None
        state = "unknown" if receipt is not None or args.command == "exec-status" else "not_sent"
        await output({"state": state, "execution_id": receipt.tx_id if receipt else getattr(args, "execution_id", None),
                      "exit_code": None, "reason_code": getattr(exc, "code", type(exc).__name__),
                      "message": label(exc), "output_complete": False, "may_still_be_running": state == "unknown"})
        return 2
