"""人類操作的連線提示；不根據錯誤改用憑證或重試。"""

from .protocol.errors import RemoteError


def connection_hint(exc: Exception) -> str | None:
    if isinstance(exc, RemoteError) and exc.code == "AUTH_FAILED":
        return "認證失敗。請在 Windows 使用原固定連線啟動 Gateway，並以 uart setup 檢查此 Client；更換 UART 不需重新產生 Token。"
    if isinstance(exc, ConnectionRefusedError):
        return "無法連到 Gateway。請在 Windows 開啟 run-remote.cmd，選擇啟動遠端使用；確認 Gateway 與 SSH 轉送已就緒後，按 R 重新連線。"
    if isinstance(exc, TimeoutError):
        return "等待連線或回應逾時。請檢查 Windows Gateway 與 SSH 轉送，再按 R 查詢；未自動重試。"
    if isinstance(exc, (ConnectionResetError, BrokenPipeError, EOFError)):
        return "Gateway 連線已中斷。請確認 Windows Gateway 與 SSH 轉送，按 R 查詢；若 Gateway 已重啟，請重新選擇 UART。"
    return None
