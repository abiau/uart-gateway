"""Windows 遠端設定與日常啟動；固定部署與 UART 選擇分開保存。"""

import asyncio
from dataclasses import asdict
import logging
from pathlib import Path
import sys
import tempfile
import tomllib

from .auth_setup import initialize, root_directory
from .client.connection import config_directory, default_name, profile_path, read_profile
from .console_input import ConsoleInput
from .protected_files import read_private, write_private
from .remote_install import RemoteTarget, install_payload, install_remote, load_target, open_remote, save_target, stop_ssh, trust_target
from .remote_workbench import label


class GatewayPortUnavailable(ValueError):
    """啟動前已確認未建立任何 Gateway 資源，可安全返回選單。"""


def ask(prompt: str, default: str = "") -> str:
    print(prompt + (f" [{label(default)}]" if default else "") + "：", flush=True)
    answer = input().strip()
    if answer.casefold() == "q":
        raise EOFError
    return answer or default


def read_gateway(root: Path, name: str) -> tuple[Path, dict]:
    profile = read_profile(root, name)
    if not profile.get("gateway_config"):
        raise ValueError("此連線只有 Client 憑證；請在 Windows 的原 Gateway 連線執行設定。")
    path = Path(profile["gateway_config"])
    config = tomllib.loads(read_private(path, 65536).decode("utf-8-sig"))
    return path, config


def select_devices(count: int):
    from .local_ports import choose_channels
    channels = choose_channels(count)
    if channels is None:
        raise EOFError
    return channels


def update_devices(root: Path, name: str, selected) -> None:
    """僅在未啟動 Gateway 時人工換設備；保留秘密參照及既有 TX 範圍。"""
    from .configured_launcher import config_text
    from .server.app import assemble, secret
    path, config = read_gateway(root, name)
    old = config["channels"]
    ids = [c["id"] for c in old]
    while len(ids) < len(selected):
        cid = "ch_uart_" + str(len(ids) + 1)
        if cid in ids:
            raise ValueError("新增通道 ID 衝突；請使用原 Gateway 設定工具明確調整。")
        ids.append(cid)
    result = []
    for i, chosen in enumerate(selected):
        base = dict(old[i]) if i < len(old) else dict(old[0], id=ids[i])
        base.update(label=chosen.label, adapter={key: value for key, value in asdict(chosen.adapter).items() if value is not None})
        base.pop("target_note", None)
        result.append(base)
    wanted = {c["id"] for c in result}
    for principal in config["auth"]["principals"]:
        if principal["id"] in ("remote_reader", "remote_manager"):
            principal["channels"] = [c["id"] for c in result]
        elif not set(principal["channels"]) <= wanted:
            raise ValueError("移除通道會影響既有 TX 授權；請先明確調整 TX 範圍，未改寫設定。")
    config["channels"] = result
    assemble(config, fake=False, resolve_secret=secret)
    write_private(path.with_name("gateway.previous.toml"), read_private(path, 65536), replace=True)
    write_private(path, config_text(config).encode("utf-8"), replace=True)


def check_idle(config: dict) -> None:
    import socket
    port = config.get("network", {}).get("port", 18081)
    try:
        # 只暫時綁定位址，不連線、不接聽、不觸發既有 Gateway 的 Session。
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("127.0.0.1", port))
    except OSError as exc:
        import errno
        code = getattr(exc, "winerror", None) or exc.errno
        if code in (errno.EADDRINUSE, errno.EACCES, 10013, 10048):
            raise GatewayPortUnavailable(f"本機 Gateway TCP Port {port} 已被使用、由系統保留或綁定權限不足。設定 Linux 目的地請選 2；"
                                         "啟動另一個 Gateway 前，請先在原視窗停止既有 Gateway；不會接管其他程序。") from None
        raise GatewayPortUnavailable(f"無法檢查本機 Gateway TCP Port {port}（錯誤 {code or '未提供'}），"
                                     "請確認本機網路與綁定權限；尚未啟動 Gateway 或連線 SSH。") from None


def setup_remote(root: Path, name: str) -> int:
    target_path = profile_path(root, name) / "remote.json"
    previous = load_target(target_path)
    configured = (profile_path(root, name) / "client.json").exists()
    selected = None
    if configured:
        read_gateway(root, name)
        print("沿用既有固定部署與憑證，不重新產生 Token。", flush=True)
    else:
        count = int(ask("第一次設定：UART 數量（1 或 2；Q 取消）", "1"))
        selected = select_devices(count)
    ssh = ask("Linux SSH 主機（帳號@主機或既有 SSH 別名；Q 取消）", previous.ssh if previous else "")
    container = ask("Container 名稱（- 表示直接使用 Linux 主機）", previous.container if previous and previous.container else "-")
    roles_choice = ask("Linux 權限：R 讀取、M 讀取與管理、T 另含 TX（明確授權才選 T）",
                       "T" if previous and "tx" in previous.roles else "M" if previous and "manage" in previous.roles else "R").casefold()
    roles = {"r": ("read",), "m": ("read", "manage"), "t": ("read", "manage", "tx")}.get(roles_choice)
    if roles is None:
        raise ValueError("請使用 R、M 或 T 選擇權限。")
    target = RemoteTarget(ssh, container="" if container == "-" else container, roles=roles,
                          ssh_port=int(ask("SSH Port", str(previous.ssh_port if previous else 22))),
                          python=ask("Linux Python 執行檔（需 3.12 以上）", previous.python if previous else "python3"),
                          name=ask("Linux 連線名稱", previous.name if previous else "company"),
                          port=int(ask("Linux 轉送 Port", str(previous.port if previous else 18081))))
    print(f"即將設定：{label(target.ssh)}／{label(target.container or 'Linux 主機')}；角色：{'／'.join(target.roles)}。", flush=True)
    print("安裝使用者私有 Client 與固定憑證，並選為該環境的預設連線；不修改 Shell 啟動檔、容器掛載或網路。", flush=True)
    if "tx" in roles:
        print("首次建立的 TX 範圍為本次所選通道；既有部署保留原 TX 範圍。傳送仍須核對目標、預覽、Confirm 與 Lease。", flush=True)
    if ask("Enter 安裝；Q 取消"):
        raise EOFError
    trust_target(target)
    if not configured:
        from .local_launcher import _gateway_profile
        with tempfile.TemporaryDirectory(prefix="uart-remote-setup-") as temporary:
            source = Path(temporary) / "gateway.toml"
            source.write_text(_gateway_profile(selected, 18081), encoding="utf-8")
            initialize(source, name=name, directory=root, enable_tx="tx" in roles)
    profile = read_profile(root, name)
    if set(roles) - profile["credentials"].keys():
        raise ValueError("既有部署尚未建立所選角色；未產生新 Token 或提升權限。請先使用原授權流程建立該角色，或改選已有角色。")
    # 先保存無秘密目的地，SSH 中斷後可直接重試，固定憑證不變。
    save_target(target_path, target)
    print("正在透過 SSH 安裝 Client 與所選角色；不顯示憑證內容……", flush=True)
    in_path = asyncio.run(install_remote(target, name=name, root=root))
    print("Linux Client 已安裝。日常使用 uart；Windows 使用此選單啟動遠端服務。", flush=True)
    if not in_path:
        print("目前遠端 PATH 尚未包含 ~/.local/bin；可直接執行 ~/.local/bin/uart，或重新登入讓既有使用者 PATH 生效。", flush=True)
    return 0


async def serve_remote(root: Path, name: str, target: RemoteTarget, *, poll=None, report=print) -> int:
    from .server.app import load
    path, _ = await asyncio.to_thread(read_gateway, root, name)
    gateway, host, port = await asyncio.to_thread(load, path, fake=False)
    process = None
    output_task = None
    payload = await asyncio.to_thread(install_payload, target, name=name, root=root, hold=True)
    poll = poll or ConsoleInput(echo=report).poll

    async def drain_output(stream):
        # 就緒之後也持續有界消費，避免 SSH 標準輸出阻塞而影響清理。
        total = 0
        while data := await stream.read(4096):
            total += len(data)
            if total > 65536:
                raise ValueError("SSH 控制輸出超過上限，請檢查遠端 Shell 設定。")

    async def connect_remote():
        nonlocal process, output_task
        process, in_path = await open_remote(target, payload, forward=port)
        output_task = asyncio.create_task(drain_output(process.stdout))
        report("遠端已就緒：Linux 可執行 uart；Gateway／憑證／通道查詢已經由 SSH 驗證。")
        if not in_path:
            report("Linux 本次請使用 ~/.local/bin/uart；目前 PATH 尚未包含 ~/.local/bin。")

    try:
        await gateway.start(host, port)
        for channel in gateway.channels.values():
            report(f"{channel.config.id}／{label(channel.config.label)}：{channel.state}；未 Confirm。")
        try:
            await connect_remote()
        except (OSError, ValueError, TimeoutError) as exc:
            report("遠端尚未就緒：" + label(exc) + "；Gateway RX 保持執行。")
        report("R 手動建立／驗證 SSH 連線；Q／Ctrl+C 停止本次 Gateway 與 SSH。")
        while True:
            if process is not None and (process.returncode is not None or output_task.done()):
                failure = output_task.exception() if output_task.done() and not output_task.cancelled() else None
                await stop_ssh(process)
                await asyncio.gather(output_task, return_exceptions=True)
                process, output_task = None, None
                report("SSH 連線已中斷，Gateway RX／錄製繼續；R 手動恢復。" + (label(failure) if failure else ""))
            try:
                answer = poll()
            except EOFError:
                return 0
            if answer is not None:
                if answer.strip().casefold() == "q":
                    return 0
                if answer.strip().casefold() == "r":
                    if process is not None:
                        report("SSH 控制連線仍存在；Linux 可按 R 更新通道，未重建任何 UART。")
                    else:
                        try:
                            await connect_remote()
                        except (OSError, ValueError, TimeoutError) as exc:
                            report("遠端尚未就緒：" + label(exc) + "；未自動重試，RX 繼續。")
            await asyncio.sleep(0.05)
    finally:
        try:
            if process is not None:
                await stop_ssh(process)
            if output_task is not None:
                await asyncio.gather(output_task, return_exceptions=True)
        finally:
            await gateway.close()


def start_remote(root: Path, name: str) -> int:
    target = load_target(profile_path(root, name) / "remote.json")
    if target is None:
        raise ValueError("尚未設定 Linux 目的地，請先執行 uart setup。")
    _, config = read_gateway(root, name)
    check_idle(config)
    for channel in config["channels"]:
        print(f"{label(channel['id'])}／{label(channel['label'])}：{label(channel['adapter'].get('port') or '依已存裝置條件')}。", flush=True)
    choice = ask("Enter 沿用並啟動；D 重新選 UART；Q 取消").casefold()
    if choice == "d":
        count = int(ask("UART 數量（1 或 2）", str(len(config["channels"]))))
        selected = select_devices(count)
        print("將沿用固定憑證；新通道加入讀取／管理範圍，TX 維持原授權。", flush=True)
        if ask("Enter 保存並啟動；Q 取消"):
            raise EOFError
        update_devices(root, name, selected)
    elif choice:
        raise ValueError("請直接 Enter、D 或 Q。")
    print(f"啟動固定 Gateway → {label(target.ssh)}／{label(target.container or '主機')}；錄製沿用 Gateway 設定。", flush=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return asyncio.run(serve_remote(root, name, target))


def run_remote_entry(args) -> int:
    if sys.platform != "win32":
        raise ValueError("設定與 Gateway 啟動請在 Windows 執行 uart setup／run-remote.cmd；此 Linux／Container 日常只需執行 uart。")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("遠端設定／啟動需要互動終端；Linux 日常請執行 uart，腳本使用既有 CLI／SDK。")
    if args.access or args.token_file or args.token_env is not None or args.host_explicit or args.port_explicit:
        raise ValueError("遠端設定／啟動使用固定連線；不混用 Token、角色或端點覆寫。")
    root = root_directory(args.connections_dir or config_directory())
    name = args.connection or default_name(root)
    try:
        if args.command == "setup":
            return setup_remote(root, name)
        if args.command == "gateway":
            return start_remote(root, name)
        while True:
            print("\nUART 遠端使用\n1 開始遠端使用（固定憑證／選 UART）\n2 第一次設定或修改 Linux 目的地\nQ 離開", flush=True)
            try:
                configured = load_target(profile_path(root, name) / "remote.json") is not None
                if not configured:
                    print("此連線尚未設定 Linux 目的地，第一次請選 2。", flush=True)
                choice = ask("選擇", "1" if configured else "2")
                if choice == "1":
                    if configured:
                        start_remote(root, name)
                    else:
                        print("請先選 2 完成第一次設定。", flush=True)
                elif choice == "2":
                    setup_remote(root, name)
                else:
                    print("請輸入 1、2 或 Q。", flush=True)
            except EOFError:
                return 0
            except GatewayPortUnavailable as exc:
                print(label(exc), flush=True)
    except EOFError:
        print("已取消；未啟動新的 Gateway。", flush=True)
        return 0
