"""本機端點選單；枚舉不開啟 Serial，選定後交回既有啟動器。"""

from collections.abc import Callable
import re
import sys

from .serial_backend import AdapterDetails, AdapterSelector, list_adapter_details, port_key


def _label(value: object) -> str:
    text = str(value) if value is not None and value != "" else "未提供"
    result = "".join(char if char.isprintable() else char.encode("unicode_escape").decode("ascii")
                     for char in text[:256])
    return result + ("…" if len(text) > 256 else "")


def _inventory() -> list[AdapterDetails]:
    def key(entry):
        name = port_key(entry.adapter.port)
        match = re.fullmatch(r"COM([0-9]+)", name)
        return (0, int(match[1]), name) if match else (1, 0, name.casefold())

    return sorted((entry for entry in list_adapter_details() if entry.adapter.port), key=key)


def port_lines(entries: list[AdapterDetails]) -> list[str]:
    lines = ["本機 Serial 裝置（僅枚舉，不開啟；占用狀態須在啟動時確認）："]
    if not entries:
        lines.append("目前未找到 Serial 裝置，請確認 USB-UART 已連接及 Driver 已安裝。")
    for number, entry in enumerate(entries, 1):
        info = entry.adapter
        identity = f"{info.vid:04X}:{info.pid:04X}" if info.vid is not None and info.pid is not None else "未提供"
        lines.append(f"{number}. {_label(info.port)} | 描述：{_label(entry.description)} | "
                     f"序號：{_label(info.serial_number)} | VID/PID：{identity} | 位置：{_label(info.location)}")
    return lines


def list_ports() -> list[str]:
    return port_lines(_inventory())


def choose_port(*, read: Callable[[], str] | None = None,
                report: Callable[[str], None] | None = None,
                recovering: bool = False) -> AdapterSelector | None:
    """在事件迴圈與 Gateway 啟動之前執行；取消不留下 input 執行緒。"""
    if not sys.stdin.isatty():
        raise ValueError("互動選擇需要終端機輸入；請使用 uart ports 查看，並以 --port 明確指定端點。")
    read = read or input
    report = report or (lambda message: print(message, file=sys.stderr, flush=True))
    entries = _inventory()
    redraw = True
    while True:
        if redraw:
            for line in port_lines(entries):
                report(line)
            redraw = False
        report("輸入裝置編號；R 或 Enter 重新整理；Q 或 Ctrl+C 離開：" if recovering else
               "輸入裝置編號；R 重新整理；Q 或直接 Enter 取消：")
        try:
            answer = read().strip()
        except EOFError:
            report("輸入來源已結束（EOF），無法繼續等待選擇；請在互動式終端重新執行。")
            return None
        if answer.casefold() == "q" or (not answer and not recovering):
            return None
        if answer.casefold() == "r" or (not answer and recovering):
            entries, redraw = _inventory(), True
            continue
        if not (answer.isascii() and answer.isdigit() and len(answer) <= 6
                and 1 <= int(answer) <= len(entries)):
            report("編號無效，請輸入清單中的編號，或按 R／Q。")
            continue
        selected = entries[int(answer) - 1].adapter
        latest = _inventory()
        matches = [entry.adapter for entry in latest if port_key(entry.adapter.port) == port_key(selected.port)]
        if (len(matches) != 1 or any(getattr(matches[0], name) != getattr(selected, name)
                                    for name in ("vid", "pid", "serial_number", "location"))):
            report("所選端點已消失、資訊改變或無法唯一識別，請重新選擇。")
            entries, redraw = latest, True
            continue
        report(f"已選擇 {_label(selected.port)}，即將以只讀模式啟動。")
        return AdapterSelector(port=selected.port,
            vid=selected.vid if selected.pid is not None else None,
            pid=selected.pid if selected.vid is not None else None,
            serial_number=selected.serial_number, location=selected.location)
