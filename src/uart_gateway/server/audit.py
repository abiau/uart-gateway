"""以有界佇列保存操作稽核，寫入與查詢在工作執行緒執行。"""

import asyncio
from contextlib import closing
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import sqlite3
import stat

from uart_gateway.protected_files import WINDOWS, private_directory, windows_acl, write_private
from .recording import disk_io

LOG = logging.getLogger(__name__)
AUDITED = frozenset({"binding.confirm", "binding.rebind", "channel.reopen_rx", "channel.configure_rx",
                     "lease.acquire", "lease.release", "tx.write"})
DATABASE = "operations.sqlite3"


@dataclass(frozen=True)
class AuditConfig:
    directory: Path
    max_entries: int = 10000

    def __post_init__(self) -> None:
        if not self.directory.is_absolute():
            raise ValueError("稽核目錄必須是絕對路徑。")
        if type(self.max_entries) is not int or not 1 <= self.max_entries <= 100000:
            raise ValueError("稽核筆數上限必須為 1 至 100000。")


class AuditLog:
    def __init__(self, config: AuditConfig):
        self.config = config
        self.path = config.directory / "uart-audit" / DATABASE
        self.queue = asyncio.Queue(maxsize=256)
        self.task: asyncio.Task | None = None
        self.dropped = 0

    def _initialize(self) -> None:
        if self.path.parent.is_junction():
            raise ValueError("稽核目錄不得是連結。")
        private_directory(self.path.parent)
        if self.path.is_symlink() or self.path.is_junction():
            raise ValueError("稽核資料庫不得是連結。")
        if not self.path.exists():
            write_private(self.path, b"")
        info = self.path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("稽核資料庫必須是一般檔案。")
        if WINDOWS:
            windows_acl(self.path)
        elif info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("稽核資料庫只能由目前使用者存取。")
        with closing(sqlite3.connect(self.path, timeout=1)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS operations (
                id INTEGER PRIMARY KEY, principal TEXT, channel TEXT, tx_id TEXT, entry TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS audit_principal ON operations (principal)")
            db.execute("CREATE INDEX IF NOT EXISTS audit_channel ON operations (channel)")
            db.execute("CREATE INDEX IF NOT EXISTS audit_tx ON operations (tx_id) WHERE tx_id IS NOT NULL")

    def _write(self, rows: list[dict]) -> None:
        with closing(sqlite3.connect(self.path, timeout=1)) as db, db:
            db.executemany("INSERT INTO operations (principal, channel, tx_id, entry) VALUES (?, ?, ?, ?)",
                [(row["principal"], row["channel"], row.get("tx_id"), json.dumps(row)) for row in rows])
            db.execute("DELETE FROM operations WHERE id <= ((SELECT max(id) FROM operations) - ?)",
                       (self.config.max_entries,))

    async def start(self) -> None:
        try:
            await disk_io(self._initialize)
        except (OSError, sqlite3.Error) as exc:
            raise ValueError("無法初始化稽核資料庫。") from exc
        self.task = asyncio.create_task(self._run())

    def record(self, row: dict) -> None:
        if self.task is None or self.task.done() or self.queue.full():
            self.dropped += 1
            if self.dropped == 1:
                LOG.error("稽核寫入不可用或佇列已滿，部分操作紀錄可能遺失。")
            return
        self.queue.put_nowait(row)

    async def _run(self) -> None:
        try:
            while (row := await self.queue.get()) is not None:
                rows = [row]
                stop = False
                while len(rows) < 64 and not self.queue.empty():
                    item = self.queue.get_nowait()
                    if item is None:
                        stop = True
                        break
                    rows.append(item)
                await disk_io(self._write, rows)
                if stop:
                    return
        except (OSError, sqlite3.Error):
            LOG.error("稽核資料庫無法寫入，稽核停止至 Gateway 重新啟動。")
        finally:
            while not self.queue.empty():
                self.queue.get_nowait()

    async def close(self) -> None:
        task, self.task = self.task, None
        if task is not None:
            if not task.done():
                await self.queue.put(None)
            await task
            if self.dropped:
                LOG.error("本次 Gateway 有 %d 筆稽核事件未入列。", self.dropped)


def query_audit(directory: Path, *, principal: str | None = None, channel: str | None = None,
                tx_id: str | None = None, limit: int = 50) -> list[dict]:
    if not 1 <= limit <= 500:
        raise ValueError("稽核查詢筆數必須為 1 至 500。")
    path = directory.absolute() / "uart-audit" / DATABASE
    if not path.is_file() or path.is_symlink() or path.parent.is_symlink() or path.parent.is_junction():
        raise ValueError("找不到可讀取的稽核資料庫。")
    filters = [(key, value) for key, value in (("principal", principal), ("channel", channel), ("tx_id", tx_id))
               if value is not None]
    where = " WHERE " + " AND ".join(f"{key}=?" for key, _ in filters) if filters else ""
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT entry FROM operations" + where + " ORDER BY id DESC LIMIT ?",
                [value for _, value in filters] + [limit])]
    except sqlite3.Error as exc:
        raise ValueError("無法查詢稽核資料庫。") from exc
