"""Token 映射到固定身分與明列的通道權限。"""

from dataclasses import dataclass, field
import hmac
import re

from uart_gateway.core.models import DomainError
from uart_gateway.protocol.messages import identifier


@dataclass(frozen=True)
class Principal:
    id: str
    token: str = field(repr=False)
    grants: dict[str, frozenset[str]]

    def __post_init__(self) -> None:
        identifier(self.id)
        if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", self.token):
            raise ValueError("Token 格式不合法")
        for channel, permissions in self.grants.items():
            identifier(channel)
            if not permissions or not permissions <= {"read", "tx", "configure"} or "read" not in permissions:
                raise ValueError("通道權限不合法")

    def require(self, channel_id: str, permission: str) -> None:
        if permission not in self.grants.get(channel_id, frozenset()):
            raise DomainError("PERMISSION_DENIED", "沒有此通道的操作權限。")


def authenticate(token: str, principals: tuple[Principal, ...]) -> Principal:
    match = None
    for principal in principals:
        if hmac.compare_digest(token, principal.token):
            match = principal
    if match is None:
        raise DomainError("AUTH_FAILED", "認證失敗。")
    return match
