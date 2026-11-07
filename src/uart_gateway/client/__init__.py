"""Client SDK 公開入口，不依賴 Gateway 或 Serial 實作。"""

from .sdk import Client, LeaseHandle, TxUncertain
from .connection import ConnectionSettings, resolve_connection

__all__ = ["Client", "LeaseHandle", "TxUncertain", "ConnectionSettings", "resolve_connection"]
