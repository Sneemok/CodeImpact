from dataclasses import dataclass, field
from typing import List


@dataclass
class User:
    username: str
    password_hash: str
    roles: List[str] = field(default_factory=list)

    def check_password(self, raw: str) -> bool:
        from auth import verify_password

        return verify_password(raw, self.password_hash)

    def is_admin(self) -> bool:
        return "admin" in self.roles
