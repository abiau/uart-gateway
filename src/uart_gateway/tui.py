"""可選 TUI 入口；驗證終端後才載入畫面或建立自己的 Gateway。"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import secrets
import tomllib
import sys

from .configured_launcher import launch_config, prepare_config
from .remote_workbench import label
from .workbench_state import StartChoice, choice_config, load_choice, save_choice


@asynccontextmanager
async def selected_config(choice):
    manager = choice_config(choice)
    entering = asyncio.create_task(asyncio.to_thread(manager.__enter__))
    try:
        path = await asyncio.shield(entering)
    except asyncio.CancelledError:
        try:
            await entering
        except Exception:
            raise
        else:
            await asyncio.to_thread(manager.__exit__, None, None, None)
        raise
    try:
        yield path
    finally:
        await asyncio.to_thread(manager.__exit__, None, None, None)


async def run_tui(args, *, settings=None) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("多窗格 TUI 需要互動式終端機，請在 Windows Terminal／PowerShell 或終端機執行。")
    try:
        from .tui_app import UARTApp
    except ModuleNotFoundError as exc:
        if exc.name.split(".")[0] in {"textual", "pyte", "rich", "wcwidth"}:
            raise ValueError('尚未安裝多窗格依賴，請執行：python -m pip install -e ".[server,tui]"') from None
        raise
    if args.local:
        choice = StartChoice(config=args.config) if args.config else await asyncio.to_thread(load_choice, args.preferences_file)
        if args.record_dir is not None:
            choice = replace(choice, recording="on")
        elif args.no_record:
            choice = replace(choice, recording="off")
        directory = args.record_dir or args.recordings_dir
        last_plan = None

        def options():
            return {"record_directory": directory if choice.recording == "on" else None,
                    "no_record": choice.recording == "off"}

        async def preview():
            nonlocal last_plan
            async with selected_config(choice) as config:
                plan = await asyncio.to_thread(prepare_config, config, **options())
            last_plan = plan
            return ("本機 UART：" + "／".join(label(c.adapter.port) for c in plan.channels) +
                    "\n錄製：" + label(plan.recording or "關閉") +
                    "\nRX 管理：" + ("允許人工重開" if all("configure" in g["permissions"] for g in plan.grants) else "僅接收") +
                    "\n只管理本次啟動的 Gateway。")

        async def devices():
            from .serial_backend import list_adapter_details
            listed = await asyncio.to_thread(list_adapter_details)
            return listed[:64], {c.adapter.port for c in last_plan.channels} if last_plan else set()

        async def select(devices):
            nonlocal choice
            from .configured_launcher import config_text
            from .local_launcher import _gateway_profile
            from .local_profile import LocalChannel
            from .serial_backend import AdapterSelector
            def build():
                channels = []
                for number, device in enumerate(devices, 1):
                    info = device.adapter
                    selector = AdapterSelector(port=info.port, vid=info.vid, pid=info.pid,
                                               serial_number=info.serial_number, location=info.location)
                    channels.append(LocalChannel(f"ch_uart_{number}", f"UART {number}", selector))
                config = tomllib.loads(_gateway_profile(tuple(channels), last_plan.port if last_plan else 18081))
                if last_plan and all("configure" in g["permissions"] for g in last_plan.grants):
                    config["auth"]["principals"][0]["permissions"] = ["read", "configure"]
                if last_plan and choice.recording == "inherit":
                    existing = tomllib.loads(last_plan.config).get("recording")
                    if existing:
                        config["recording"] = existing
                for entry in config["channels"]:
                    if entry["adapter"].get("serial_number"):
                        del entry["adapter"]["port"]
                path = args.preferences_file.parent / (".uart-tui-" + secrets.token_hex(8) + ".local.toml")
                with path.open("x", encoding="utf-8") as stream:
                    stream.write(config_text(config))
                return path
            selected = await asyncio.to_thread(build)
            choice = replace(choice, config=selected, profile=None)

        async def toggle_record():
            nonlocal choice
            enabled = bool(last_plan.recording) if last_plan else choice.recording == "on"
            choice = replace(choice, recording="off" if enabled else "on")

        async def launch(monitor, reporter):
            try:
                await asyncio.to_thread(save_choice, args.preferences_file, choice)
            except (OSError, ValueError) as exc:
                reporter("本次設定未保存：" + label(exc))
            async with selected_config(choice) as config:
                return await launch_config(config, monitor, **options(), startup_timeout=args.startup_timeout,
                    reporter=reporter, capture_ready=getattr(args, "capture_ready", None))

        app = UARTApp(local_launch=launch, local_preview=preview, local_devices=devices, local_select=select, local_record=toggle_record, description="本機 RX｜Q 清理本次 Gateway")
    else:
        from .client.connection import config_directory, connection_name
        from .client.tx_receipts import TxJournal
        journal = TxJournal((getattr(args, "connections_dir", None) or config_directory()) / "tx-receipts" /
                            connection_name(settings.name or "direct")) if settings.access == "tx" else None
        mode = {"manage": "管理", "tx": "TX 操作"}.get(settings.access, "讀取／明確來源")
        app = UARTApp(settings.connect, description=f"遠端｜{label(settings.host)}:{settings.port}｜{mode}／{label(settings.name or '明確來源')}",
                      channels=args.channels, tx_journal=journal)
    try:
        result = await app.run_async()
    finally:
        await app.cleanup()
        if app.controller.uncertain_reopen:
            print("RX 重開結果未確認；Gateway 可能已接受，請重新進入後先 R 查詢，不直接重送。", file=sys.stderr, flush=True)
        if app.tx is not None and app.tx.notice:
            print(app.tx.notice + " 重新開啟相同連線，R 查通道／F3 查 TX 收據。", file=sys.stderr, flush=True)
        if app.failure is not None:
            raise app.failure
    return result or 0
