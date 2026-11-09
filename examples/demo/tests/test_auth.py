import pytest

from auth import AuthError, Session, hash_password, login, verify_password


def test_hash_roundtrip():
    stored = hash_password("secret")
    assert verify_password("secret", stored)
    assert not verify_password("nope", stored)


class TestLogin:
    def test_success(self, store):
        session = login(store, "alice", "wonderland")
        assert session.user.username == "alice"

    def test_wrong_password(self, store):
        with pytest.raises(AuthError):
            login(store, "alice", "queen")


def test_session_expiry():
    session = Session.__new__(Session)
    session.created = 0
    assert session.is_expired(now=10**9)
