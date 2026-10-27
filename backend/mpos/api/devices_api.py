"""mPOS device control for the portal screens (HO mPOS Control, Store Devices). Needs the DEVICES role.

Who may do what comes from the user's locations, as everywhere on the site:
- HO (location HO, or an admin): every store, every action.
- A store user (location HD22): that store only, and only the store approver's actions - its terminals'
  on/off switch, pairing codes, unpairing, and opening the day. If HO has named an approver for the
  store (MPOS_STORES.APPROVER), only that user may change it.

Screens read from the in-memory copy (devices/cache.py), so a busy screen never wakes Snowflake. Every
change is one statement in Snowflake, then the copy reloads at once and phones hear of it at their
next check-in (30 s). No bill is cut by these actions except HO's Immediate lock: switching off lets
an open bill finish, and unpair / retire wait until no bill is open.

Plain `def` endpoints: writes wait on Snowflake, so they run in worker threads, never blocking phones.
"""
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import locations as L
from ..auth import roles as R
from ..auth.deps import require
from ..auth.store import StoreUnavailable
from ..devices import cache, pairing, store
from ..devices import state as S
from ..stores import store_master

router = APIRouter(prefix="/api/devices")
devices_user = require(R.DEVICES)
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


# ---------------------------------------------------------------- request bodies

class OnOff(BaseModel):
    on: bool
    reason: str = Field("", max_length=500)
    version: int | None = None  # the VERSION the screen saw; refuses the change if it moved since


class Stores(OnOff):
    stores: list[str] | None = None  # None = every store


class Block(BaseModel):
    active: bool  # veto / lock in place (True) or lifted (False)
    reason: str = Field("", max_length=500)
    version: int | None = None


class Reason(BaseModel):
    reason: str = Field("", max_length=500)


class Day(BaseModel):
    open: bool


class Add(BaseModel):
    stores: list[str] | None = None  # None = every active store
    count: int = Field(1, ge=1, le=S.MAX_MPOS)


class Settings(BaseModel):
    max_mpos: int | None = Field(None, ge=0, le=20)
    approver: str | None = Field(None, max_length=50)  # "" clears it
    day_control: bool | None = None
    day_close_time: str | None = None  # "HH:MM", "" clears it
    version: int | None = None


class Preview(BaseModel):
    level: str = Field(pattern="^(global|stores|terminal)$")
    targets: list[str] | None = None  # store codes or terminal IDs; None with stores = every store


# ---------------------------------------------------------------- who may do what

def _is_ho(user):
    return L.allowed_stores(user) is None


def _ho_only(user, what):
    if not _is_ho(user):
        raise HTTPException(403, f"Only HO can {what}")


def _readable(user, code):
    allowed = L.allowed_stores(user)
    if allowed is not None and code not in allowed:
        raise HTTPException(403, f"You do not have access to store {code}")


def _may_change(user, code):
    """HO, or this store's own user - and the named approver, if HO has named one."""
    if _is_ho(user):
        return
    _readable(user, code)
    approver = getattr(cache.current.stores.get(code), "approver", None)
    if approver and approver.lower() != user.username.lower():
        raise HTTPException(403, f"Only {code}'s mPOS approver ({approver}) can change its devices")


def _terminal(tid):
    t = cache.current.terminals.get((tid or "").strip().upper())
    if not t or t.status != "ACTIVE":
        raise HTTPException(404, f"No active terminal {tid}")
    return t


def _no_open_bill(tid, what):
    if cache.current.billing_phone(tid, None):
        raise HTTPException(409, f"A bill is open on {tid} - {what} once it is finished")


def _need_reason(on, reason):
    if not on and not reason.strip():
        raise HTTPException(400, "Give a reason - the cashier sees it on the phone")


def _ip(request):
    return request.client.host if request.client else None


def _write(fn, *args, **kwargs):
    """Run a store.* change, turn its errors into answers, and reload the copy straight away."""
    if cache.current.seq is None:
        raise HTTPException(503, "Device control is starting - try again shortly")
    try:
        result = fn(*args, **kwargs)
    except store.Conflict as e:
        raise HTTPException(409, str(e))
    except store.LimitReached as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(404, str(e))
    except StoreUnavailable:
        raise HTTPException(503, "Snowflake is unavailable - nothing was changed, try again shortly")
    try:
        cache.current.refresh_if_changed()
    except StoreUnavailable:
        pass  # saved; the background refresher picks it up within 15 s
    return result


# ---------------------------------------------------------------- views

def _names():
    try:
        return {s["code"]: s for s in store_master()}
    except StoreUnavailable:
        return {}


def _terminal_view(t, st, billing, live):
    return {"terminal_id": t.terminal_id, "store": t.store_code, "seq": t.seq, "state": st.key,
            "reason": st.reason, "message": st.text(), "paired": t.paired, "bill_open": billing,
            "store_enabled": t.store_enabled, "veto": t.ho_veto, "veto_reason": t.veto_reason,
            "lock": t.immediate_lock, "lock_reason": t.lock_reason, "phone": t.device_serial,
            "app_version": live.app_version or t.app_version, "paired_at": t.paired_at,
            "last_seen": live.last_seen.isoformat(timespec="seconds") if live.last_seen else t.last_seen_at,
            "version": t.version}


def _silent(t, live, now):
    """Paired, but no check-in for cache.GONE_SECONDS (lost, off, out of network) - or never since the
    site started."""
    return t.paired and (not live.last_seen or (now - live.last_seen).total_seconds() >= cache.GONE_SECONDS)


def _store_view(code, s, terms, info, now=None):
    now = now or datetime.now()
    counts = {"on": 0, "draining": 0, "off": 0, "not_paired": 0, "bills_open": 0, "silent": 0}
    for t, st, billing, live in terms:
        if not t.paired:
            counts["not_paired"] += 1
        else:
            counts[st.key.lower()] += 1
        counts["bills_open"] += billing
        counts["silent"] += _silent(t, live, now)
    s = s or S.Store(code)
    return {"code": code, "name": info.get("name", ""), "region": info.get("region", ""),
            "state": info.get("state", ""), "ho_enabled": s.ho_enabled, "ho_reason": s.ho_reason,
            "day_control": s.day_control, "day_open": s.day_open, "day_close_time": s.day_close_time,
            "approver": s.approver, "max_mpos": s.max_mpos, "version": s.version,
            "terminals": len(terms), **counts}


# ---------------------------------------------------------------- reading

@router.get("/overview")
def overview(user=Depends(devices_user)):
    """Every store this user may see, with its terminals counted by state."""
    glob, stores, rows = cache.current.snapshot()
    names = _names()
    allowed = L.allowed_stores(user)
    codes = set(names) | set(stores) if allowed is None else set(allowed)
    by_store = {}
    for row in rows:
        by_store.setdefault(row[0].store_code, []).append(row)
    now = datetime.now()
    out = []
    for c in sorted(codes):
        terms = sorted(by_store.get(c, []), key=lambda r: r[0].seq)
        view = _store_view(c, stores.get(c), terms, names.get(c, {}), now)
        # one entry per phone, for the coloured squares on the HO screen
        view["phones"] = [{"id": t.terminal_id, "state": st.key, "paired": t.paired, "bill": billing,
                           "lock": t.immediate_lock, "veto": t.ho_veto, "silent": _silent(t, live, now),
                           "message": st.text()} for t, st, billing, live in terms]
        out.append(view)
    return {"is_ho": allowed is None, "loaded": cache.current.seq is not None,
            "global": {"enabled": glob.enabled, "reason": glob.reason, "version": glob.version},
            "totals": {k: sum(s[k] for s in out) for k in ("terminals", "on", "draining", "off", "not_paired",
                                                           "bills_open", "silent")},
            "stores": out}


@router.get("/stores/{code}")
def store_detail(code: str, user=Depends(devices_user)):
    code = code.strip().upper()
    _readable(user, code)
    glob, stores, rows = cache.current.snapshot()
    terms = [r for r in rows if r[0].store_code == code]
    view = _store_view(code, stores.get(code), terms, _names().get(code, {}))
    view["terminal_list"] = [_terminal_view(*r) for r in sorted(terms, key=lambda r: r[0].seq)]
    view["global"] = {"enabled": glob.enabled, "reason": glob.reason}
    view["can_change"] = _is_ho(user) or not (stores.get(code) and stores[code].approver) or \
        stores[code].approver.lower() == user.username.lower()
    return view


@router.get("/history")
def history(store_code: str = "", terminal: str = "", limit: int = 200, user=Depends(devices_user)):
    code = (store_code or terminal.split("_MPOS")[0]).strip().upper()
    if code:
        _readable(user, code)
    elif not _is_ho(user):
        raise HTTPException(400, "Choose a store")
    try:
        return store.load_history(code or None, terminal or None, min(max(limit, 1), 1000))
    except StoreUnavailable:
        raise HTTPException(503, "Snowflake is unavailable - try again shortly")


@router.post("/preview")
def preview(body: Preview, user=Depends(devices_user)):
    """What a switch-off would reach before it is done: phones affected and bills open on them now."""
    _, _, rows = cache.current.snapshot()
    allowed = L.allowed_stores(user)
    targets = {x.strip().upper() for x in body.targets or []}
    if body.level == "global":
        hit = rows
    elif body.level == "stores":
        hit = [r for r in rows if body.targets is None or r[0].store_code in targets]
    else:
        hit = [r for r in rows if r[0].terminal_id in targets]
    hit = [r for r in hit if r[0].paired and (allowed is None or r[0].store_code in allowed)]
    return {"phones": len(hit), "billing_now": sum(1 for r in hit if r[2]),
            "stores": len({r[0].store_code for r in hit})}


# ---------------------------------------------------------------- store approver (and HO)

@router.post("/terminals/{tid}/switch")
def switch_terminal(tid: str, body: OnOff, request: Request, user=Depends(devices_user)):
    t = _terminal(tid)
    _may_change(user, t.store_code)
    _write(store.set_terminal, t.terminal_id, body.on, user.username, reason=body.reason or None,
           version=body.version, ip=_ip(request))
    return _one(t.terminal_id)


@router.post("/terminals/{tid}/pair-code")
def pair_code(tid: str, request: Request, user=Depends(devices_user)):
    """A one-time code (15 minutes) for a phone to pair to this terminal. The phone on it now keeps
    billing until a new phone pairs."""
    t = _terminal(tid)
    _may_change(user, t.store_code)
    code = _write(store.issue_pair_code, t.terminal_id, user.username, ip=_ip(request))
    url = str(request.base_url).rstrip("/")
    return {"terminal_id": t.terminal_id, "code": code, "valid_minutes": pairing.CODE_MINUTES,
            "qr": pairing.qr_payload(url, t.terminal_id, code)}


@router.post("/terminals/{tid}/unpair")
def unpair(tid: str, body: Reason, request: Request, user=Depends(devices_user)):
    t = _terminal(tid)
    _may_change(user, t.store_code)
    _no_open_bill(t.terminal_id, "unpair it")
    _write(store.unpair_terminal, t.terminal_id, user.username, reason=body.reason or None, ip=_ip(request))
    return _one(t.terminal_id)


@router.post("/stores/{code}/day")
def day(code: str, body: Day, request: Request, user=Depends(devices_user)):
    code = code.strip().upper()
    _may_change(user, code)
    _write(store.set_day, code, body.open, user.username, ip=_ip(request))
    return store_detail(code, user)


# ---------------------------------------------------------------- HO only

@router.post("/global")
def switch_global(body: OnOff, request: Request, user=Depends(devices_user)):
    _ho_only(user, "switch every mPOS on or off")
    _need_reason(body.on, body.reason)
    _write(store.set_global, body.on, body.reason, user.username, version=body.version, ip=_ip(request))
    return {"enabled": cache.current.glob.enabled, "reason": cache.current.glob.reason}


@router.post("/stores/switch")
def switch_stores(body: Stores, request: Request, user=Depends(devices_user)):
    _ho_only(user, "switch stores on or off")
    _need_reason(body.on, body.reason)
    n = _write(store.set_stores, body.stores, body.on, body.reason, user.username, version=body.version,
               ip=_ip(request))
    return {"stores_changed": n}


@router.post("/terminals/{tid}/veto")
def veto(tid: str, body: Block, request: Request, user=Depends(devices_user)):
    _ho_only(user, "block a terminal")
    t = _terminal(tid)
    _need_reason(not body.active, body.reason)
    _write(store.set_veto, t.terminal_id, body.active, body.reason, user.username, version=body.version,
           ip=_ip(request))
    return _one(t.terminal_id)


@router.post("/terminals/{tid}/lock")
def lock(tid: str, body: Block, request: Request, user=Depends(devices_user)):
    """Immediate lock: stops the phone at once, even mid-bill (lost or stolen phone)."""
    _ho_only(user, "lock a terminal")
    t = _terminal(tid)
    _need_reason(not body.active, body.reason)
    _write(store.set_lock, t.terminal_id, body.active, body.reason, user.username, version=body.version,
           ip=_ip(request))
    return _one(t.terminal_id)


@router.post("/terminals/{tid}/retire")
def retire(tid: str, body: Reason, request: Request, user=Depends(devices_user)):
    _ho_only(user, "retire a terminal")
    t = _terminal(tid)
    _no_open_bill(t.terminal_id, "retire it")
    _write(store.retire_terminal, t.terminal_id, user.username, reason=body.reason or None, ip=_ip(request))
    return {"retired": t.terminal_id}


@router.post("/add")
def add(body: Add, request: Request, user=Depends(devices_user)):
    """New terminals: `count` in each of the listed stores, or in every active store."""
    _ho_only(user, "add terminals")
    active = set(_names())
    if not active:
        raise HTTPException(503, "The store list is unavailable - try again shortly")
    codes = sorted(active) if body.stores is None else sorted({c.strip().upper() for c in body.stores})
    unknown = [c for c in codes if c not in active]
    if unknown:
        raise HTTPException(400, f"Not active stores: {', '.join(unknown[:10])}")
    added, full = _write(store.add_terminals, codes, body.count, user.username, ip=_ip(request))
    return {"added": added, "at_limit": full}


@router.post("/stores/{code}/settings")
def settings(code: str, body: Settings, request: Request, user=Depends(devices_user)):
    _ho_only(user, "change store settings")
    code = code.strip().upper()
    changes = body.model_dump(exclude_unset=True, exclude={"version"})
    if "day_close_time" in changes and changes["day_close_time"] and not HHMM.match(changes["day_close_time"]):
        raise HTTPException(400, "Day close time must be HH:MM, e.g. 22:30")
    for key in ("approver", "day_close_time"):
        if key in changes:
            changes[key] = (changes[key] or "").strip().lower() if key == "approver" else changes[key]
            changes[key] = changes[key] or None
    if "max_mpos" in changes and changes["max_mpos"] is None:
        del changes["max_mpos"]
    _write(store.set_store_settings, code, changes, user.username, version=body.version, ip=_ip(request))
    return store_detail(code, user)


def _one(tid):
    _, _, rows = cache.current.snapshot()
    return next((_terminal_view(*r) for r in rows if r[0].terminal_id == tid), {"terminal_id": tid})
