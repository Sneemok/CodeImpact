from typing import Dict

import auth
from auth import AuthError, login, logout
from storage import UserStore


def handle_login(store: UserStore, payload: Dict[str, str]) -> Dict[str, object]:
    try:
        session = login(store, payload["username"], payload["password"])
    except AuthError:
        return {"status": 401}
    return {"status": 200, "token": session.token, "expires_in": auth.TOKEN_TTL}


def handle_logout(session: auth.Session) -> Dict[str, object]:
    logout(session)
    return {"status": 204}


def session_info(session: auth.Session) -> Dict[str, object]:
    return {"user": session.user.username, "expired": session.is_expired()}
