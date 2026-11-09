from auth import hash_password
from models import User
from storage import UserStore


def create_user(store: UserStore, username: str, password: str, admin: bool = False) -> User:
    user = User(username, hash_password(password), ["admin"] if admin else [])
    store.add_user(user)
    return user


def reset_password(store: UserStore, username: str, new_password: str) -> bool:
    user = store.find_user(username)
    if user is None:
        return False
    user.password_hash = hash_password(new_password)
    return True
