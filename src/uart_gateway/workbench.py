"""同步文字工作台；以結構化參數呼叫既有命令，不存取 UART。"""

import asyncio
from collections.abc import Callable
from pathlib import Path
import sys

from .local_launcher import LauncherCleanupError
from .protocol.errors import ProtocolError, RemoteError
from .recording_text import visible

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
                  report: Callable[[str], None] | None = None) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("互動式工作台需要終端機輸入與輸出，請在 PowerShell 或終端機執行。")
    read = read or input
    report = report or (lambda message: print(message, file=sys.stderr, flush=True))
    recordings = recordings.absolute()

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

    def select_capture() -> Path | None:
        while True:
            entries, limited = capture_paths(recordings)
            report(f"錄製根目錄：{label(recordings)}")
            if limited:
                report("目錄數量超過掃描上限，請以 M 指定 Channel 目錄，或縮小 --recordings-dir 範圍。")
            elif not entries:
                report("目前沒有可列出的錄製，可用 M 手動指定 Channel 目錄。")
            for number, entry in enumerate(entries, 1):
                report(f"{number}. {label(entry.relative_to(recordings))}")
            choice = ask("輸入錄製編號；R 重新整理；M 手動輸入 Channel 目錄；Q 或 Enter 返回：")
            if not choice or choice.casefold() == "q":
                return None
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
            if choice.isascii() and choice.isdigit() and len(choice) <= 6 and 1 <= int(choice) <= len(entries):
                return entries[int(choice) - 1]
            report("錄製編號無效，請重新選擇。")

    def recordings_menu() -> None:
        selected = select_capture()
        if selected is None:
            return
        while True:
            report(f"目前錄製：{label(selected)}")
            choice = ask("I 檢查資訊；V 終端回看；T 匯出文字；Q 或 Enter 返回工作台：").casefold()
            if choice in ("", "q"):
                return
            if choice == "i":
                invoke(["recording-info", str(selected)])
            elif choice == "v":
                report("離線回看中 Ctrl+C 可返回工作台選單；不會連線 UART。")
                if invoke(["recording-play", str(selected), "--terminal"]) == 130:
                    return
            elif choice == "t":
                destination = path("輸入新的 .txt／.log 輸出路徑；Enter 或 Q 取消：")
                if destination is not None:
                    invoke(["recording-text", str(selected), "--output", str(destination)])
            else:
                report("請輸入 I、V、T 或 Q。")

    try:
        while True:
            report("\nUART 本機只讀工作台\n1. 列出 Serial 裝置\n2. 選擇 UART 並監看\n"
                   "3. 選擇 UART 並錄製\n4. 使用已存端點配置\n5. 瀏覽錄製：檢查／回看／匯出\nQ. 離開")
            choice = ask("輸入功能編號；Q 或 Enter 離開：").casefold()
            if choice in ("", "q"):
                report("已離開工作台。")
                return 0
            try:
                if choice == "1":
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
