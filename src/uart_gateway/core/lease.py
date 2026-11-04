"""租約規則；由 Channel 的同一同步邊界保護。"""

from .models import DomainError

U64_MAX = 2**64 - 1


class Lease:
    def __init__(self, ttl: float):
        self.ttl = ttl
        self.owner: str | None = None
        self.token = 0
        self.deadline = 0.0

    def acquire(self, session: str, now: float) -> None:
        if self.owner is not None and self.owner != session:
            raise DomainError("LEASE_BUSY", "此通道已有租約持有者。", owner_session_id=self.owner)
        if self.owner is None:
            if self.token == U64_MAX:
                raise DomainError("CHANNEL_BUSY", "隔離序號已耗盡，需重新啟動。")
            self.token += 1
            self.owner = session
            self.deadline = now + self.ttl

    def check(self, session: str, token: int, now: float) -> None:
        if self.owner is None or now >= self.deadline:
            raise DomainError("LEASE_EXPIRED", "傳送租約已失效。")
        if session != self.owner or token != self.token:
            raise DomainError("LEASE_NOT_OWNER", "此工作階段沒有有效的傳送操作權。")

    def renew(self, session: str, token: int, now: float) -> None:
        self.check(session, token, now)
        self.deadline = now + self.ttl
