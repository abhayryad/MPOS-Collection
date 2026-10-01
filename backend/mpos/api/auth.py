"""Login, logout, current user and password change."""
import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from ..auth import passwords, store
from ..auth.deps import client, current_user, forget
from ..auth.roles import ROLES
from ..auth.sessions import COOKIE, make_token
from ..config import settings

router = APIRouter(prefix="/api/auth")

BAD_LOGIN = "Wrong username or password"


class Login(BaseModel):
    username: str
    password: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


def _me(user):
    return {**user.public(), "role_names": ROLES}


def _set_cookie(response, username):
    response.set_cookie(COOKIE, make_token(username), max_age=settings.session_hours * 3600,
                        httponly=True, samesite="lax", path="/")


def _locked(username):
    try:
        return store.recent_failures(username) >= settings.max_failed_logins
    except Exception:  # noqa: BLE001 - Snowflake trouble must never block a login (admin must stay able to get in)
        return False  # cannot check; the password check below still applies


@router.post("/login")
def login(body: Login, request: Request, response: Response):
    username = body.username.strip().lower()
    who = client(request)
    if not username or not body.password:
        raise HTTPException(400, "Enter your username and password")
    if _locked(username):
        store.record("LOGIN_LOCKED", username, detail="too many failed attempts", **who)
        raise HTTPException(429, f"Too many failed attempts - try again in {settings.lockout_minutes} minutes")

    if username == settings.admin_username:
        ok = bool(settings.admin_password) and hmac.compare_digest(body.password, settings.admin_password)
        user = store.admin_user() if ok else None
    else:
        try:
            user, pw_hash = store.get_user(username)
        except store.StoreUnavailable:
            raise HTTPException(503, "User store (Snowflake) is unavailable - try again shortly")
        ok = bool(user) and passwords.verify_password(body.password, pw_hash)
        if ok and not user.is_active:
            store.record("LOGIN_FAILED", username, detail="account inactive", **who)
            raise HTTPException(401, "This account has been deactivated")

    if not ok:
        store.record("LOGIN_FAILED", username, detail="wrong username or password", **who)
        raise HTTPException(401, BAD_LOGIN)

    if not user.is_builtin:
        try:
            store.mark_login(username)
        except Exception:  # noqa: BLE001 - last-login time is informational only
            pass
        forget(username)
    store.record("LOGIN_SUCCESS", username, **who)
    _set_cookie(response, username)
    return _me(user)


@router.post("/logout")
def logout(request: Request, response: Response):
    try:
        user = current_user(request)
        store.record("LOGOUT", user.username, **client(request))
    except HTTPException:
        pass
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: store.User = Depends(current_user)):
    return _me(user)


@router.post("/change-password")
def change_password(body: PasswordChange, request: Request, user: store.User = Depends(current_user)):
    if user.is_builtin:
        raise HTTPException(400, "The admin password is set in the server's .env file")
    _, pw_hash = store.get_user(user.username)
    if not passwords.verify_password(body.current_password, pw_hash):
        raise HTTPException(400, "Current password is wrong")
    if body.new_password == body.current_password:
        raise HTTPException(400, "New password must be different")
    if settings.default_user_password and body.new_password == settings.default_user_password:
        raise HTTPException(400, "Choose your own password, not the default one")
    problem = passwords.check_strength(body.new_password)
    if problem:
        raise HTTPException(400, problem)
    store.set_password(user.username, passwords.hash_password(body.new_password), False, user.username)
    forget(user.username)
    store.record("PASSWORD_CHANGED", user.username, **client(request))
    user.must_change_password = False
    return _me(user)
