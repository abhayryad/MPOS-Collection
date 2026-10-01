"""Admin tab: user management and authentication history.

Two levels of admin:
- superadmin - the built-in .env account only. Manages everyone and is the only one who can make
  or remove admins.
- admin - a user marked IS_ADMIN. Sees every tab and every store's data, but manages only normal
  (non-admin) users inside their own locations, and can only assign those locations. An HO admin
  manages every normal user.
"""
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
    is_admin: bool = False


class UserChange(BaseModel):
    full_name: str | None = None
    roles: list[str] | None = None
    locations: list[str] | None = None
    is_active: bool | None = None
    is_admin: bool | None = None


class PasswordReset(BaseModel):
    password: str
    must_change_password: bool = False


def _unavailable(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except store.StoreUnavailable as e:
        raise HTTPException(503, str(e))


def scope(admin):
    """Stores whose users this admin manages: None = all (superadmin or HO admin), else a set."""
    if admin.is_builtin or L.HO in admin.locations:
        return None
    return set(admin.locations)


def can_manage(admin, user):
    """Superadmin manages everyone; an admin manages non-admin users wholly inside their scope."""
    if admin.is_builtin:
        return True
    if user.is_admin:
        return False
    allowed = scope(admin)
    if allowed is None:
        return True
    return bool(user.locations) and L.HO not in user.locations and set(user.locations) <= allowed


def _managed_user(username, admin):
    """The user, or 404 - also for users this admin may not manage, so they stay invisible."""
    user, _ = _unavailable(store.get_user, username)
    if not user or not can_manage(admin, user):
        raise HTTPException(404, f"No user '{username}'")
    return user


def _check_admin_flag(admin, wanted):
    if wanted and not admin.is_builtin:
        raise HTTPException(403, "Only the superadmin can make or remove admins")


def _check_locations(raw, admin):
    """Cleaned location list; 400 unless it is HO or known store codes inside the admin's scope."""
    locations = L.clean(raw)
    if not locations:
        raise HTTPException(400, "Location is required - choose HO (all stores) or at least one store")
    allowed = scope(admin)
    if allowed is not None:
        outside = [c for c in locations if c not in allowed]
        if outside:
            raise HTTPException(403, f"You can only assign your own stores - not {', '.join(outside)}")
    if locations != [L.HO]:
        known = {s["code"] for s in _unavailable(store_master)}
        unknown = [c for c in locations if c not in known]
        if unknown:
            raise HTTPException(400, f"Not an active store: {', '.join(unknown)}")
    return locations


@router.get("/stores")
def stores(admin: store.User = Depends(admin_only)):
    """Active stores (Snowflake GOLD.STORE_PLANT_MASTER) this admin may assign, for the location picker."""
    allowed = scope(admin)
    rows = _unavailable(store_master)
    return rows if allowed is None else [s for s in rows if s["code"] in allowed]


@router.get("/defaults")
def defaults():
    return {"default_password": settings.default_user_password}


@router.get("/roles")
def roles():
    return R.ROLES


@router.get("/users")
def users(admin: store.User = Depends(admin_only)):
    return [u.public() for u in _unavailable(store.list_users) if can_manage(admin, u)]


@router.post("/users")
def create_user(body: NewUser, request: Request, admin: store.User = Depends(admin_only)):
    _check_admin_flag(admin, body.is_admin)
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
    # admins have every role; their location is the set of stores whose users they manage
    roles = list(R.ROLES) if body.is_admin else R.clean(body.roles)
    locations = _check_locations(body.locations, admin)
    try:
        existing, _ = _unavailable(store.get_user, username)
        if existing:
            raise HTTPException(409, f"User '{username}' already exists")
        store.create_user(username, full_name, roles, locations, passwords.hash_password(body.password),
                          body.must_change_password, admin.username, is_admin=body.is_admin)
    except snowflake.connector.errors.ProgrammingError as e:
        raise HTTPException(400, f"Could not create user: {e.msg}")
    store.record("USER_CREATED", username, actor=admin.username,
                 detail=f"name={full_name}; {'admin' if body.is_admin else 'roles=' + (','.join(roles) or '-')}; "
                        f"location={','.join(locations)}",
                 **client(request))
    user, _ = store.get_user(username)
    return user.public()


@router.patch("/users/{username}")
def change_user(username: str, body: UserChange, request: Request, admin: store.User = Depends(admin_only)):
    user = _managed_user(username, admin)
    make_admin = body.is_admin if body.is_admin is not None and body.is_admin != user.is_admin else None
    _check_admin_flag(admin, make_admin is not None)
    if user.is_admin if make_admin is None else make_admin:
        body.roles = list(R.ROLES)  # admins always have every role
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
        new = _check_locations(body.locations, admin)
        if new != user.locations:
            locations = new
            changes.append(f"location: {','.join(user.locations) or '-'} -> {','.join(new)}")
    active = body.is_active if body.is_active is not None and body.is_active != user.is_active else None
    store.update_user(user.username, admin.username, full_name=full_name, roles=roles, locations=locations,
                      is_active=active, is_admin=make_admin)
    forget(user.username)
    who = client(request)
    if changes:
        store.record("USER_UPDATED", user.username, actor=admin.username, detail="; ".join(changes), **who)
    if active is not None:
        store.record("USER_ACTIVATED" if active else "USER_DEACTIVATED", user.username, actor=admin.username, **who)
    if make_admin is not None:
        store.record("ADMIN_GRANTED" if make_admin else "ADMIN_REVOKED", user.username, actor=admin.username, **who)
    user, _ = store.get_user(user.username)
    return user.public()


@router.post("/users/{username}/reset-password")
def reset_password(username: str, body: PasswordReset, request: Request, admin: store.User = Depends(admin_only)):
    user = _managed_user(username, admin)
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
