from typing import Dict, Optional

from models import User


class UserStore:
    def __init__(self) -> None:
        self._users: Dict[str, User] = {}

    def add_user(self, user: User) -> None:
        self._users[user.username] = user

    def find_user(self, username: str) -> Optional[User]:
        return self._users.get(username)

    def count(self) -> int:
        return len(self._users)
