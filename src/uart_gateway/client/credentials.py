"""SDK 共用憑證來源；只在明確呼叫時讀取，不依賴 Server 或 CLI。"""

from dataclasses import dataclass, field
import os
from pathlib import Path
import re

from uart_gateway.protected_files import read_private


def validate_token(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", value):
        raise ValueError("憑證格式不合法，請重新初始化；不會輸出憑證內容。")
    return value


@dataclass(frozen=True)
class TokenSource:
    reference: str = field(repr=False)
    protected: bool = True

    def __post_init__(self):
        if (not isinstance(self.reference, str) or not 5 <= len(self.reference) <= 4096
                or not self.reference.isprintable() or not self.reference.startswith(("file:", "env:"))
                or self.reference in ("file:", "env:")):
            raise ValueError("憑證來源必須是非空的 file: 或 env: 參照。")

    def read(self) -> str:
        if self.reference.startswith("env:"):
            value = os.environ.get(self.reference[4:], "")
        elif self.reference.startswith("file:"):
            path = Path(self.reference[5:])
            try:
                if self.protected:
                    value = read_private(path, 1024).decode("utf-8").strip()
                else:
                    with path.open("rb") as stream:
                        raw = stream.read(1025)
                    if len(raw) > 1024:
                        raise ValueError("Token 檔案超過大小上限。")
                    value = raw.decode("utf-8").strip()
            except (OSError, UnicodeError):
                raise ValueError("無法讀取 Token 檔案，請確認路徑、權限與 UTF-8 編碼。") from None
        else:
            raise ValueError("秘密參照必須使用 env: 或 file:。")
        if not value:
            raise ValueError("未取得憑證，請先初始化；不會切換其他來源或管理身分。")
        return validate_token(value) if self.protected else value


def explicit_source(*, token_file: str | Path | None = None, token_env: str | None = None) -> TokenSource:
    if token_file is not None and token_env is not None:
        raise ValueError("Token 檔案與環境變數只能明確指定一個。")
    return TokenSource("file:" + str(token_file) if token_file is not None else
                       "env:" + (token_env if token_env is not None else "UART_GATEWAY_TOKEN"), protected=False)
