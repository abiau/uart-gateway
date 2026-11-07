"""錄製操作選單；編號導覽只組合原 CLI，不操作 UART。"""

import asyncio
from pathlib import Path

from .recording_catalog import preview_capture
from .recording_navigation import match_selection, output_path, selection_arguments, time_input, timeline
from .recording_selection import RXSelection
from .remote_workbench import label


def browse_recording(selected: Path, *, root: Path, session, ask, path, report, invoke) -> None:
    filters = RXSelection()
    information = None

    def confirm() -> bool:
        return preview_capture(selected).footer or ask(
            "此錄製未見最終收尾，可能仍在寫入或異常中止；建議先停止 Gateway。輸入 Y 仍繼續，其他輸入取消：").casefold() == "y"

    def export(selection: RXSelection) -> None:
        suggestion = output_path(root)
        report("建議輸出位置：" + label(suggestion))
        answer = ask("輸入新的輸出路徑；Enter 使用建議檔名；M 自訂路徑；Q 取消：")
        if answer.casefold() == "q":
            return
        if not answer:
            suggestion.parent.mkdir(parents=True, exist_ok=True)
            destination = suggestion
        elif answer.casefold() == "m":
            destination = path("輸入新的 .txt／.log 路徑；Enter 或 Q 取消：")
        else:
            if len(answer) >= 2 and answer[0] == answer[-1] and answer[0] in "\"'":
                answer = answer[1:-1]
            if not answer or not answer.isprintable():
                raise ValueError("輸出路徑須為非空的可顯示文字。")
            destination = Path(answer).expanduser()
        if destination is not None:
            invoke(["recording-text", str(selected), "--output", str(destination), *selection_arguments(selection)])

    def change_filters() -> None:
        nonlocal information, filters
        if information is None:
            report("正在檢查整份錄製並建立資料流清單……")
            information = asyncio.run(timeline(selected))
        if information["integrity"] != "no_known_gap":
            report("注意：整份錄製不完整或連續性未知，選取單一資料流也不消除警告。")
        epochs = information["summary"]["epochs"]
        for number, entry in enumerate(epochs, 1):
            stamp = information["epoch_times"].get(entry["epoch"], "未提供")
            report(f"{number}. 資料流 {number} | 首筆 RX：{label(stamp)} | RX bytes：{entry['rx_bytes']}")
        if information["summary"]["epochs_truncated"]:
            report("資料流清單達摘要上限；完整檢查仍涵蓋整份錄製，可用 I 查看摘要警告。")
        epoch = ask("Epoch（資料流編號；Enter 全部；也可貼上原識別；Q 取消）：")
        if epoch.casefold() == "q":
            return
        if epoch.isascii() and epoch.isdigit():
            if len(epoch) > 6 or not 1 <= int(epoch) <= len(epochs):
                report("資料流編號無效，保留前次篩選。")
                return
            epoch = epochs[int(epoch) - 1]["epoch"]
        report("相對分鐘起點：" + label(information["started_at"] or "未提供，請使用完整時間"))
        start = ask("起始時間（錄製起點後幾分鐘，如 5；或含時區的完整時間；Enter 不限）：")
        end = ask("結束時間（錄製起點後幾分鐘，如 10；或含時區的完整時間；Enter 不限）：")
        try:
            filters = RXSelection(epoch or None, time_input(start, information["started_at"]),
                                  time_input(end, information["started_at"]))
        except ValueError as exc:
            report("篩選無效，保留前次條件：" + label(exc))
            return
        report("已更新篩選：" + filters.description)

    def matches(result: dict) -> bool | None:
        entries = result["search"]["matches"]
        while entries:
            answer = ask("輸入命中編號可回看／匯出附近片段；Q 或 Enter 返回錄製操作：")
            if answer.casefold() == "q" or not answer:
                return
            if not answer.isascii() or not answer.isdigit() or len(answer) > 6 or not 1 <= int(answer) <= len(entries):
                report("命中編號無效。")
                continue
            entry, seconds = entries[int(answer) - 1], 30
            while True:
                try:
                    selection = match_selection(entry, seconds)
                except ValueError as exc:
                    report("命中缺少可用時間或資料流識別，請改用 F 選取：" + label(exc))
                    break
                report(f"選定命中 {answer}，前後各 {seconds} 秒；" + selection.description)
                action = ask("Enter 或 V 回看；T 匯出這一段；W 調整前後秒數；Q 返回命中選擇：").casefold()
                if action == "q":
                    break
                if action == "w":
                    value = ask("前後各幾秒（0～3600）；Enter 保持：")
                    if value:
                        if value.isascii() and value.isdigit() and len(value) <= 4 and int(value) <= 3600:
                            seconds = int(value)
                        else:
                            report("秒數無效，保留前次設定。")
                elif action in ("", "v", "t"):
                    if not confirm():
                        continue
                    if action == "t":
                        export(selection)
                    elif invoke(["recording-play", str(selected), "--terminal", *selection_arguments(selection)]) == 130:
                        return True
                else:
                    report("請輸入 V、T、W 或 Q。")

    while True:
        preview = preview_capture(selected)
        report("目前錄製：" + label(preview.source if preview.source != "未提供" else selected.name) + " | 時間：" + label(preview.timestamp))
        if filters.active:
            report("目前 RX 篩選：" + filters.description + " I 永遠檢查整份錄製。")
        choice = ask("I 檢查資訊；V 終端回看；T 匯出文字；S 關鍵字搜尋；F 設定區段／清除；D 路徑詳情；Q 或 Enter 返回工作台：").casefold()
        if choice in ("", "q"):
            return
        if choice == "i":
            invoke(["recording-info", str(selected)])
        elif choice == "d":
            report(label(selected) + " | " + label(preview.description))
        elif choice == "f":
            change_filters()
        elif choice in ("v", "t"):
            if not confirm():
                continue
            if choice == "t":
                export(filters)
            else:
                report("離線回看中 Ctrl+C 可返回工作台選單；不會連線 UART。")
                if invoke(["recording-play", str(selected), "--terminal", *selection_arguments(filters)]) == 130:
                    return
        elif choice == "s":
            keyword = ask("輸入字面關鍵字（區分大小寫）；Enter 取消：")
            if keyword and confirm():
                session.search_result = None
                code = invoke(["recording-search", str(selected), "--keyword=" + keyword, *selection_arguments(filters)])
                if code in (0, 2) and session.search_result is not None:
                    if matches(session.search_result):
                        return
        else:
            report("請輸入 I、V、T、S、F、D 或 Q。")
