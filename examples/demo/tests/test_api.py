from api import handle_login


def test_login_ok(store):
    response = handle_login(store, {"username": "alice", "password": "wonderland"})
    assert response["status"] == 200


def test_login_rejected(store):
    response = handle_login(store, {"username": "alice", "password": "x"})
    assert response["status"] == 401
