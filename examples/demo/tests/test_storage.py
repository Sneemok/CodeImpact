from models import User
from storage import UserStore


def test_count():
    store = UserStore()
    store.add_user(User("bob", "x"))
    assert store.count() == 1
