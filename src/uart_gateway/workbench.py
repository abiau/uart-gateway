"""同步文字工作台；以結構化參數呼叫既有命令，不存取 UART。"""

import asyncio
from collections.abc import Callable
from pathlib import Path
import sys
import secrets
from dataclasses import replace

from .local_launcher import LauncherCleanupError
from .protocol.errors import ProtocolError, RemoteError
from .recording_text import visible
from .recording_catalog import preview_capture
from .workbench_state import StartChoice, WorkbenchSession, choice_config, load_choice, save_choice

SCAN_LIMIT = 2000
DISPLAY_LIMIT = 50


def label(value: object) -> str:
    text = str(value)
    return visible(text[:512]).replace("\n", "\\n").replace("\t", "\\t") + ("…" if len(text) > 512 else "")


def capture_paths(root: Path) -> tuple[list[Path], bool]:
    """固定兩層、掃描有上限；只保留最近的少量候選。"""
    if not root.exists():
        return [], False
    if not root.is_dir():
        raise ValueError("錄製根目錄不是資料夾。")
    candidates = []
    scanned = 0
    for capture in root.iterdir():
        scanned += 1
        if scanned > SCAN_LIMIT:
            return [], True
        if not capture.name.startswith("capture-") or not capture.is_dir() or capture.is_symlink():
            continue
        for channel in capture.iterdir():
            scanned += 1
            if scanned > SCAN_LIMIT:
                return [], True
            if channel.is_dir() and not channel.is_symlink() and (channel / "000001.jsonl").is_file():
                candidates.append((channel.stat().st_mtime_ns, str(channel), channel))
                if len(candidates) > DISPLAY_LIMIT:
                    candidates.sort(reverse=True)
                    del candidates[DISPLAY_LIMIT:]
    candidates.sort(reverse=True)
    return [entry[2] for entry in candidates], False


def run_workbench(dispatch: Callable[[list[str]], int], *, recordings: Path,
                  read: Callable[[], str] | None = None,
                  report: Callable[[str], None] | None = None, session: WorkbenchSession | None = None,
                  preferences_file: Path | None = None) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("互動式工作台需要終端機輸入與輸出，請在 PowerShell 或終端機執行。")
    read = read or input
    report = report or (lambda message: print(message, file=sys.stderr, flush=True))
    recordings = recordings.absolute()
    session = session or WorkbenchSession()
    preferences_file = preferences_file or Path(".uart-workbench.local.json")
    preferences_ok = True
    try:
        start_choice = load_choice(preferences_file)
    except ValueError as exc:
        report(str(exc))
        start_choice, preferences_ok = StartChoice(), False

    def ask(prompt: str) -> str:
        report(prompt)
        return read().strip()

    def path(prompt: str) -> Path | None:
        answer = ask(prompt)
        if not answer or answer.casefold() == "q":
            return None
        if len(answer) >= 2 and answer[0] == answer[-1] and answer[0] in "\"'":
            answer = answer[1:-1]
        if not answer or not answer.isprintable():
            raise ValueError("路徑必須是可顯示的非空文字。")
        return Path(answer).expanduser()

    def invoke(argv: list[str]) -> int:
        try:
            code = dispatch(argv)
        except LauncherCleanupError:
            raise
        except (KeyboardInterrupt, asyncio.CancelledError):
            report("本次操作已中止，清理流程已結束，返回工作台。")
            return 130
        except (OSError, ValueError, ProtocolError, RemoteError, TimeoutError) as exc:
            report(f"操作失敗：{getattr(exc, 'code', type(exc).__name__)}：{label(exc)}")
            return 1
        except SystemExit as exc:
            # 僅處理內部參數解析；執行流程不經 CLI 的錯誤吞併邊界。
            code = exc.code if isinstance(exc.code, int) else 1
        if code == 2:
            report("操作結束：請留意上方資料缺口、連續性或訂閱終止訊息。")
        elif code:
            report(f"操作已結束，退出碼：{code}。")
        return code

    def local(record: bool, profile: Path | None = None) -> None:
        argv = ["local", "--read-only", "--watch", "--terminal"]
        if profile is not None:
            argv += ["--profile", str(profile)]
        else:
            while True:
                count = ask("選擇 UART 數量：1 或 2；Enter 使用 1；Q 取消：")
                if count.casefold() == "q":
                    return
                if count in ("", "1", "2"):
                    break
                report("請輸入 1、2 或 Q。")
            argv += ["--select", "--count", count or "1"]
            profile_path = path("保存端點配置至新檔（建議 .local.toml）；Enter 或 Q 略過保存：")
            if profile_path is not None:
                argv += ["--save-profile", str(profile_path)]
        if record:
            argv += ["--record-dir", str(recordings)]
        report("只讀監控中按 Ctrl+C，等待本次 Gateway 清理後返回工作台。")
        invoke(argv)

    def start_menu() -> None:
        nonlocal start_choice
        from .configured_launcher import prepare_config
        from .local_ports import choose_channels
        from .local_profile import save_profile
        templates = [Path("examples/gateway.com3.toml"), Path("examples/gateway.com3.rx-reopen.toml"),
                     Path("examples/gateway.dual.rx-reopen.toml")]
        while True:
            argv = ["start"]
            if start_choice.recording == "on":
                argv += ["--record-dir", str(recordings)]
            elif start_choice.recording == "off":
                argv += ["--no-record"]
            # 摘要與真正啟動都重新驗證；偏好檔不保存憑證或裝置就緒狀態。
            plan = None
            try:
                with choice_config(start_choice) as actual:
                    plan = prepare_config(actual, record_directory=recordings if start_choice.recording == "on" else None,
                                          no_record=start_choice.recording == "off")
                    summary = "／".join(f"{c.adapter.port}（{c.label}）" for c in plan.channels)
                    report("開始監看 | 裝置：" + label(summary))
                    report("錄製：" + ("開啟 → " + label(plan.recording) if plan.recording else "關閉") +
                           " | 手動 RX 恢復：" + ("允許" if all("configure" in g["permissions"] for g in plan.grants) else "未授權"))
                    answer = ask("Enter 開始；D 更換裝置；R 切換錄製；C 沿用設定的錄製選擇；M 進階設定檔；Q 返回：").casefold()
                    if not answer:
                        if preferences_ok:
                            try:
                                save_choice(preferences_file, start_choice)
                            except (OSError, ValueError) as exc:
                                report(label(exc))
                        invoke([*argv, "--config", str(actual)])
                        return
            except LauncherCleanupError:
                raise
            except (OSError, ValueError) as exc:
                report("設定尚無法使用：" + label(exc))
                answer = ask("D 更換裝置；R 切換錄製；M 選擇設定檔；Q 或 Enter 返回：").casefold()
                if not answer:
                    return
            if answer == "q":
                return
            if answer == "r":
                current = bool(plan.recording) if plan is not None else start_choice.recording == "on"
                start_choice = replace(start_choice, recording="off" if current else "on")
            elif answer == "c":
                start_choice = replace(start_choice, recording="inherit")
            elif answer == "m":
                selected = path("輸入 Gateway TOML 路徑；Enter 或 Q 返回：")
                if selected is not None:
                    start_choice = replace(start_choice, config=selected, profile=None)
            elif answer == "d":
                report("1. COM3 單路，僅接收\n2. COM3 單路，可人工恢復 RX\n3. COM3＋COM4 雙路，可人工恢復 RX\n4. 從目前 USB-UART 清單選擇（僅接收）")
                device = ask("輸入裝置方案編號；Enter 或 Q 取消：")
                if device in ("1", "2", "3"):
                    start_choice = replace(start_choice, config=templates[int(device) - 1], profile=None)
                elif device == "4":
                    count = ask("使用幾條 UART？1 或 Enter 單路；2 雙路；Q 取消：")
                    if count not in ("", "1", "2"):
                        continue
                    channels = choose_channels(int(count or "1"), read=read, report=report)
                    if channels:
                        profile = preferences_file.parent / (".uart-workbench-" + secrets.token_hex(8) + ".local.toml")
                        save_profile(profile, channels)
                        start_choice = replace(start_choice, profile=profile)
            else:
                report("選項無效。")

    def configured() -> None:
        options = [Path("examples/gateway.com3.toml"), Path("examples/gateway.com3.rx-reopen.toml"),
                   Path("examples/gateway.dual.rx-reopen.toml")]
        report("選擇 Gateway 設定（與端點配置不同；不改寫原檔）：")
        for number, option in enumerate(options, 1):
            report(f"{number}. {option}")
        answer = ask("輸入設定編號；M 自訂 Gateway TOML；Q 或 Enter 返回：").casefold()
        if answer in ("", "q"):
            return
        if answer == "m":
            selected = path("輸入 Gateway TOML 路徑；Enter 或 Q 返回：")
        elif answer in ("1", "2", "3"):
            selected = options[int(answer) - 1]
        else:
            report("設定編號無效。")
            return
        if selected is None:
            return
        answer = ask("錄製：C 或 Enter 沿用設定；Y 啟用至工作台錄製目錄；N 停用；Q 取消：").casefold()
        if answer == "q":
            return
        if answer not in ("", "c", "y", "n"):
            report("錄製選項無效。")
            return
        argv = ["start", "--config", str(selected)]
        if answer == "y":
            argv += ["--record-dir", str(recordings)]
        elif answer == "n":
            argv += ["--no-record"]
        report("啟動後 Q 或 Ctrl+C 結束並清理本次 Gateway；原已啟動的程序不受影響。")
        invoke(argv)

    def select_capture() -> Path | None:
        category = "a"
        while True:
            entries, limited = capture_paths(recordings)
            report(f"錄製根目錄：{label(recordings)}")
            if limited:
                report("目錄數量超過掃描上限，請以 M 指定 Channel 目錄，或縮小 --recordings-dir 範圍。")
            elif not entries:
                report("目前沒有可列出的錄製，可用 M 手動指定 Channel 目錄。")
            previews = [(entry, preview_capture(entry)) for entry in entries]
            previews = [(entry, preview) for entry, preview in previews
                        if category == "a" or preview.footer == (category == "c")]
            entries = [entry for entry, _ in previews]
            report("預覽尚未驗證 SHA-256；未收尾可能仍在錄製。Epoch 數僅涵蓋有界掃描，R 可更新。")
            groups = {}
            for entry, preview in previews:
                groups.setdefault(entry.parent, []).append((entry, preview))
            grouped = list(groups.values())
            report("目前篩選：" + {"a": "全部", "c": "有收尾標記", "u": "未收尾"}[category])
            if not grouped:
                report("此篩選下沒有可列出的錄製；A 可顯示全部，M 可手動指定。")
            for number, group in enumerate(grouped, 1):
                stamp = next((p.timestamp for _, p in group if p.timestamp != "未提供"), group[0][0].parent.name)
                report(f"{number}. {label(stamp)} | {len(group)} 條通道")
                for entry, preview in group:
                    ending = "已收尾（未驗證）" if preview.footer else "未收尾／可能錄製中"
                    count = str(preview.epochs) if preview.complete_scan else f"至少 {preview.epochs}"
                    report(f"  · {label(preview.source if preview.source != '未提供' else entry.name)} | {ending} | Epoch：{count}")
            choice = ask("輸入錄製編號；R 重新整理；A 全部／C 有收尾標記／U 未收尾；M 手動輸入 Channel 目錄；Q 或 Enter 返回：")
            if not choice or choice.casefold() == "q":
                return None
            if choice.casefold() in ("a", "c", "u"):
                category = choice.casefold()
                continue
            if choice.casefold() == "r":
                continue
            if choice.casefold() == "m":
                selected = path("輸入包含 000001.jsonl 的 Channel 目錄；Enter 或 Q 返回：")
                if selected is None:
                    continue
                if not selected.is_dir():
                    report("找不到該 Channel 目錄。")
                    continue
                return selected
            if choice.isascii() and choice.isdigit() and len(choice) <= 6 and 1 <= int(choice) <= len(grouped):
                return select_channel(tuple(path for path, _ in grouped[int(choice) - 1]))
            report("錄製編號無效，請重新選擇。")

    def select_channel(channels: tuple[Path, ...]) -> Path | None:
        if len(channels) == 1:
            return channels[0]
        for number, channel in enumerate(channels, 1):
            source = preview_capture(channel).source
            report(f"{number}. {label(source if source != '未提供' else channel.name)}")
        while True:
            answer = ask("輸入通道編號；Q 或 Enter 返回：")
            if not answer or answer.casefold() == "q":
                return None
            if answer.isascii() and answer.isdigit() and len(answer) <= 6 and 1 <= int(answer) <= len(channels):
                return channels[int(answer) - 1]
            report("通道編號無效。")

    def recordings_menu(selected: Path | None = None) -> None:
        from .recording_workbench import browse_recording
        selected = selected or select_capture()
        if selected is not None:
            try:
                browse_recording(selected, root=recordings, session=session, ask=ask, path=path, report=report, invoke=invoke)
            except (KeyboardInterrupt, asyncio.CancelledError):
                report("離線操作已中止，掃描工作已結束，返回工作台。")

    try:
        while True:
            report("\nUART 本機只讀工作台\nS. 開始監看（記住上次設定）\nL. 查看本次錄製\n5. 瀏覽錄製\nA. 進階工具\nQ. 離開")
            choice = ask("輸入功能編號或字母；Q 或 Enter 離開：").casefold()
            if choice == "a":
                report("進階工具：1 列出裝置；2 手動選擇監看；3 手動選擇錄製；4 端點配置；6 Gateway 設定；Q 返回")
                choice = ask("輸入進階功能編號；Q 或 Enter 返回：").casefold()
                if choice in ("", "q"):
                    continue
            if choice in ("", "q"):
                report("已離開工作台。")
                return 0
            try:
                if choice == "s":
                    start_menu()
                elif choice == "l":
                    if not session.capture_channels:
                        report("本工作台尚未收到本次錄製位置；可用 5 瀏覽既有錄製，不會猜測最新檔案。")
                    else:
                        report("本工作台最近一次錄製：" + label(session.capture_channels[0].parent))
                        selected = select_channel(session.capture_channels)
                        if selected is not None:
                            recordings_menu(selected)
                elif choice == "1":
                    invoke(["ports"])
                elif choice in ("2", "3"):
                    local(choice == "3")
                elif choice == "4":
                    selected = path("輸入既有 .local.toml 配置路徑；Enter 或 Q 返回：")
                    if selected is not None:
                        answer = ask("同時錄製？Y 是；N 或 Enter 否；Q 取消：").casefold()
                        if answer in ("", "n", "y"):
                            local(answer == "y", selected)
                        elif answer != "q":
                            report("選項無效，已返回工作台。")
                elif choice == "5":
                    recordings_menu()
                elif choice == "6":
                    configured()
                else:
                    report("功能編號無效，請重新選擇。")
            except LauncherCleanupError:
                report("無法確認 Gateway 已清理，工作台停止；請檢查本次程序後再啟動。")
                raise
            except (OSError, ValueError) as exc:
                report(f"操作失敗：{label(exc)}")
    except EOFError:
        report("輸入來源已結束（EOF），已離開工作台。")
        return 0
    except KeyboardInterrupt:
        report("已中止並離開工作台。")
        return 130
