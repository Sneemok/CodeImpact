import hashlib
import hmac
import secrets
import time
from typing import Optional

from models import User
from storage import UserStore

TOKEN_TTL = 3600
ITERATIONS = 120_000


class AuthError(Exception):
    pass


def hash_password(raw: str, salt: Optional[str] = None) -> str:
    salt = salt or secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", raw.encode(), salt.encode(), ITERATIONS)
    return f"{salt}${digest.hex()}"


def verify_password(raw: str, stored: str) -> bool:
    salt, _, _ = stored.partition("$")
    return hmac.compare_digest(hash_password(raw, salt), stored)


class Session:
    def __init__(self, user: User) -> None:
        self.user = user
        self.token = secrets.token_urlsafe(16)
        self.created = time.time()

    def is_expired(self, now: Optional[float] = None) -> bool:
        return (now or time.time()) - self.created > TOKEN_TTL

    def refresh(self) -> None:
        self.created = time.time()


def login(store: UserStore, username: str, password: str) -> Session:
    user = store.find_user(username)
    if user is None or not user.check_password(password):
        raise AuthError("invalid credentials")
    return Session(user)


def logout(session: Session) -> None:
    session.created = 0
