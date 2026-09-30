"""Admin tab: user management and authentication history. Admin account only."""
import re

import snowflake.connector
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ..auth import locations as L
from ..auth import passwords, store
from ..auth import roles as R
from ..auth.deps import admin_only, client, forget
from ..config import settings
from ..stores import store_master

router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin_only)])

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,49}$")


class NewUser(BaseModel):
    username: str
    full_name: str
    roles: list[str] = []
    locations: list[str] = []  # ['HO'] or store codes
    password: str
    must_change_password: bool = False


class UserChange(BaseModel):
    full_name: str | None = None
    roles: list[str] | None = None
    locations: list[str] | None = None
    is_active: bool | None = None


class PasswordReset(BaseModel):
    password: str
    must_change_password: bool = False


def _unavailable(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except store.StoreUnavailable as e:
        raise HTTPException(503, str(e))


def _check_locations(raw):
    """Cleaned location list; 400 unless it is HO or known store codes."""
    locations = L.clean(raw)
    if not locations:
        raise HTTPException(400, "Location is required - choose HO (all stores) or at least one store")
    if locations != [L.HO]:
        known = {s["code"] for s in _unavailable(store_master)}
        unknown = [c for c in locations if c not in known]
        if unknown:
            raise HTTPException(400, f"Not an active store: {', '.join(unknown)}")
    return locations


@router.get("/stores")
def stores():
    """Active stores (Snowflake GOLD.STORE_PLANT_MASTER) for the location picker."""
    return _unavailable(store_master)


@router.get("/defaults")
def defaults():
    return {"default_password": settings.default_user_password}


@router.get("/roles")
def roles():
    return R.ROLES


@router.get("/users")
def users():
    return [u.public() for u in _unavailable(store.list_users)]


@router.post("/users")
def create_user(body: NewUser, request: Request, admin: store.User = Depends(admin_only)):
    username = body.username.strip().lower()
    if not USERNAME_RE.match(username):
        raise HTTPException(400, "Username: 2-50 letters, numbers, dot, dash or underscore")
    if username == settings.admin_username:
        raise HTTPException(400, f"'{username}' is reserved for the built-in admin account")
    full_name = body.full_name.strip()
    if not full_name:
        raise HTTPException(400, "Full name is required")
    problem = passwords.check_strength(body.password)
    if problem:
        raise HTTPException(400, problem)
    roles = R.clean(body.roles)
    locations = _check_locations(body.locations)
    try:
        existing, _ = _unavailable(store.get_user, username)
        if existing:
            raise HTTPException(409, f"User '{username}' already exists")
        store.create_user(username, full_name, roles, locations, passwords.hash_password(body.password),
                          body.must_change_password, admin.username)
    except snowflake.connector.errors.ProgrammingError as e:
        raise HTTPException(400, f"Could not create user: {e.msg}")
    store.record("USER_CREATED", username, actor=admin.username,
                 detail=f"name={full_name}; roles={','.join(roles) or '-'}; location={','.join(locations)}",
                 **client(request))
    user, _ = store.get_user(username)
    return user.public()


@router.patch("/users/{username}")
def change_user(username: str, body: UserChange, request: Request, admin: store.User = Depends(admin_only)):
    user, _ = _unavailable(store.get_user, username)
    if not user:
        raise HTTPException(404, f"No user '{username}'")
    changes = []
    full_name = roles = locations = None
    if body.full_name is not None and body.full_name.strip() != user.full_name:
        full_name = body.full_name.strip()
        if not full_name:
            raise HTTPException(400, "Full name is required")
        changes.append(f"name: {user.full_name} -> {full_name}")
    if body.roles is not None and R.clean(body.roles) != user.roles:
        roles = R.clean(body.roles)
        changes.append(f"roles: {','.join(user.roles) or '-'} -> {','.join(roles) or '-'}")
    if body.locations is not None:
        new = _check_locations(body.locations)
        if new != user.locations:
            locations = new
            changes.append(f"location: {','.join(user.locations) or '-'} -> {','.join(new)}")
    active = body.is_active if body.is_active is not None and body.is_active != user.is_active else None
    store.update_user(user.username, admin.username, full_name=full_name, roles=roles, locations=locations,
                      is_active=active)
    forget(user.username)
    who = client(request)
    if changes:
        store.record("USER_UPDATED", user.username, actor=admin.username, detail="; ".join(changes), **who)
    if active is not None:
        store.record("USER_ACTIVATED" if active else "USER_DEACTIVATED", user.username, actor=admin.username, **who)
    user, _ = store.get_user(user.username)
    return user.public()


@router.post("/users/{username}/reset-password")
def reset_password(username: str, body: PasswordReset, request: Request, admin: store.User = Depends(admin_only)):
    user, _ = _unavailable(store.get_user, username)
    if not user:
        raise HTTPException(404, f"No user '{username}'")
    problem = passwords.check_strength(body.password)
    if problem:
        raise HTTPException(400, problem)
    must_change = body.must_change_password  # optional - the admin decides
    store.set_password(user.username, passwords.hash_password(body.password), must_change, admin.username)
    forget(user.username)
    store.record("PASSWORD_RESET", user.username, actor=admin.username,
                 detail="must change at next login" if must_change else None, **client(request))
    user, _ = store.get_user(user.username)
    return user.public()


@router.get("/activity")
def activity(limit: int = Query(500, ge=1, le=5000), username: str = ""):
    return _unavailable(store.list_events, limit, username.strip() or None)
