import pytest

from admin import create_user
from storage import UserStore


@pytest.fixture
def store():
    users = UserStore()
    create_user(users, "alice", "wonderland")
    create_user(users, "root", "toor", admin=True)
    return users
