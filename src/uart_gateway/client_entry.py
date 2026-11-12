"""單一 Client 入口：共用 SDK，依已安裝介面開啟工作台。"""

import asyncio
import importlib.util
import sys

from .client.connection import config_directory
from .client_preferences import load_selection, preference_path, save_selection
from .remote_workbench import label, run_remote_workbench


def run_client(args, parse) -> int:
    from .cli import _WatchOutput, client_connection, run, watch_client
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("uart 工作台需要互動終端。腳本／Codex 請使用 uart channels、uart watch 或既有 SDK；查看命令請執行 uart --help。")
    settings = client_connection(args)
    mode = {"read": "讀取", "manage": "管理", "tx": "TX"}.get(settings.access, "明確來源")
    print(f"連線：{label(settings.name or '明確來源')}｜{label(settings.host)}:{settings.port}｜"
          f"模式：{mode}。", file=sys.stderr, flush=True)
    if not args.text and all(importlib.util.find_spec(name) is not None for name in ("textual", "pyte")):
        child = parse(["tx" if settings.access == "tx" else "tui"])
        for name in ("host", "port", "host_explicit", "port_explicit", "token_file", "token_env", "connection", "access", "connections_dir"):
            setattr(child, name, getattr(args, name))
        return asyncio.run(run(child))
    path = preference_path(args.connections_dir or config_directory(), settings)
    try:
        preferred = load_selection(path)
    except (OSError, ValueError) as exc:
        print(label(exc), file=sys.stderr, flush=True)
        preferred = None

    async def monitor(client, cid, replay, expected):
        return await watch_client(client, cid, duration=None, text_mode=True, terminal_mode=True,
                                  replay=replay, expected_context=expected)

    async def group(channels, instance, replay):
        from .remote_group import run_remote_group
        return await run_remote_group(settings.connect, channels, instance, replay, watch=watch_client,
                                      sink_factory=lambda: _WatchOutput(terminal=True))

    return run_remote_workbench(settings.connect, monitor, host=settings.host, port=settings.port,
                                monitor_group=group, preferred_channels=preferred,
                                selection_saved=lambda channels: save_selection(path, channels))
