"""FastAPI dependencies: who is logged in, and what they may open."""
import time

from fastapi import Depends, HTTPException, Request

from ..config import settings
from . import roles as R
from . import store
from .sessions import COOKIE, read_token

_CACHE_SECONDS = 60  # how quickly a deactivated user or changed role takes effect
_cache: dict = {}


def _lookup(username):
    hit = _cache.get(username)
    if hit and hit[0] > time.time():
        return hit[1]
    user, _ = store.get_user(username)
    _cache[username] = (time.time() + _CACHE_SECONDS, user)
    return user


def forget(username):
    """Drop a cached user after an admin change so it applies immediately."""
    _cache.pop(username.lower(), None)


def client(request: Request):
    return {"ip": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent")}


def current_user(request: Request) -> store.User:
    username = read_token(request.cookies.get(COOKIE, ""))
    if not username:
        raise HTTPException(401, "Please log in")
    if username == settings.admin_username:
        return store.admin_user()
    try:
        user = _lookup(username)
    except store.StoreUnavailable:
        raise HTTPException(503, "User store (Snowflake) is unavailable - try again shortly")
    if not user or not user.is_active:
        raise HTTPException(401, "Your account is no longer active")
    return user


def ready_user(user: store.User = Depends(current_user)) -> store.User:
    """Logged in and not waiting on a forced password change."""
    if user.must_change_password:
        raise HTTPException(403, "Change your password to continue")
    return user


def require(role):
    def check(user: store.User = Depends(ready_user)) -> store.User:
        if not user.can(role):
            raise HTTPException(403, f"You do not have access to {R.ROLES.get(role, role)}")
        return user
    return check


def admin_only(user: store.User = Depends(ready_user)) -> store.User:
    if not user.is_admin:
        raise HTTPException(403, "Admin only")
    return user
