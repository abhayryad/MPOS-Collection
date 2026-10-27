"""Endpoints the mPOS phones call - no site login. A phone pairs once with a code from the portal and
from then on sends its own token: `Authorization: Bearer <token>`.

bill/start, bill/end and heartbeat are answered from memory (devices/cache.py) and are `async`, so a
slow report download can never make a cashier wait. Only pairing and the rare lock or history write
reach Snowflake, and those writes run after the answer has been sent.

Every answer carries the terminal's state: ON (bill normally), DRAINING (finish the open bill, start
no new one) or OFF (show the lock screen with `message`).
"""
import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..auth.store import StoreUnavailable
from ..devices import cache, health, pairing, store
from ..devices import state as S

router = APIRouter(prefix="/api/device")
log = logging.getLogger(__name__)

POLL_SECONDS = 30
COPIED_PHONE = "Token used from another phone"
_pair_limit = pairing.RateLimit(limit=10, seconds=60)


class Pair(BaseModel):
    terminal: str = Field(max_length=30)
    code: str = Field(max_length=20)
    serial: str = Field(max_length=100)
    app_version: str = Field(max_length=30)


class BillStart(BaseModel):
    bill_ref: str | None = Field(None, max_length=60)


class BillEnd(BaseModel):
    bill_ref: str | None = Field(None, max_length=60)
    outcome: str = Field("paid", pattern="^(paid|cancelled)$")


class Heartbeat(BaseModel):
    bill_open: bool
    serial: str = Field(max_length=100)
    app_version: str = Field(max_length=30)
    network: str | None = Field(None, max_length=20)        # wifi / mobile, if the app can tell
    battery_pct: int | None = Field(None, ge=0, le=100)


def _ip(request):
    return request.client.host if request.client else None


def _answer(terminal_id, st):
    return {"terminal_id": terminal_id, "state": st.key, "reason": st.reason, "message": st.text(),
            "poll_seconds": POLL_SECONDS, "server_time": datetime.now().isoformat(timespec="seconds")}


async def device(authorization: str = Header("")) -> S.Terminal:
    """The terminal whose token the phone sent."""
    if cache.current.seq is None:  # never loaded - Snowflake was unreachable since the site started
        raise HTTPException(503, "Device control is starting - try again shortly")
    scheme, _, token = authorization.partition(" ")
    t = cache.current.terminal_for_token(pairing.token_digest(token)) if scheme.lower() == "bearer" and token else None
    if not t:
        raise HTTPException(401, "This phone is not paired - ask the store for a pairing code")
    return t


@router.post("/pair")
def pair(body: Pair, request: Request):
    """Plain `def`: it waits on Snowflake, so it runs in a worker thread, not on the event loop."""
    ip = _ip(request)
    if not _pair_limit.allow(ip or "?"):
        raise HTTPException(429, "Too many pairing attempts - wait a minute and try again")
    busy = cache.current.billing_phone(body.terminal.strip().upper(), body.serial)
    if busy:
        raise HTTPException(409, f"A bill is open on {busy} - finish it, then pair this phone")
    try:
        token = store.redeem_pair_code(body.terminal, body.code, serial=body.serial,
                                       app_version=body.app_version, ip=ip)
        cache.current.refresh_if_changed()  # so this phone's very next call is recognised
    except store.PairingError as e:
        raise HTTPException(400, str(e))
    except StoreUnavailable:
        raise HTTPException(503, "Pairing is unavailable right now - try again shortly")
    tid = body.terminal.strip().upper()
    return {**_answer(tid, cache.current.state_of(tid)), "token": token}


@router.post("/bill/start")
async def bill_start(body: BillStart, t: S.Terminal = Depends(device)):
    allowed, st = cache.current.start_bill(t.terminal_id)
    return {**_answer(t.terminal_id, st), "allowed": allowed}


@router.post("/bill/end")
async def bill_end(body: BillEnd, request: Request, tasks: BackgroundTasks, t: S.Terminal = Depends(device)):
    before, after = cache.current.end_bill(t.terminal_id)
    if before.key == S.DRAINING and after.key == S.OFF:
        tasks.add_task(store.record, "DRAIN_DONE", "TERMINAL", actor=t.terminal_id, target=t.terminal_id,
                       old=S.DRAINING, new=S.OFF, reason=f"bill {body.outcome}", ip=_ip(request))
    return _answer(t.terminal_id, after)


@router.post("/heartbeat")
async def heartbeat(body: Heartbeat, request: Request, tasks: BackgroundTasks, t: S.Terminal = Depends(device)):
    ip, now = _ip(request), datetime.now()
    st = cache.current.check_in(t.terminal_id, bill_open=body.bill_open, serial=body.serial,
                                app_version=body.app_version, ip=ip, now=now)
    if t.device_serial and body.serial != t.device_serial and not t.immediate_lock:
        st = cache.current.lock_here(t.terminal_id, COPIED_PHONE)
        tasks.add_task(_save_lock, t.terminal_id, f"{COPIED_PHONE} (serial {body.serial})", ip)
    health.buffer.add((t.terminal_id, t.store_code, now, body.bill_open, st.key, body.app_version,
                       body.serial, ip, body.network, body.battery_pct))
    return _answer(t.terminal_id, st)


async def _save_lock(terminal_id, reason, ip):
    try:
        await run_in_threadpool(store.lock_terminal, terminal_id, reason, terminal_id, ip)
    except Exception:  # noqa: BLE001 - the lock already holds in memory; HO sees the log
        log.exception("Could not save the lock on %s to Snowflake", terminal_id)


def start():
    """At site startup: load every terminal's switches in the background and keep them fresh (phones
    get 503 until the first load succeeds), and write check-ins to Snowflake every 5 minutes.
    Returns the stop events for stop()."""
    return [cache.current.start_refresher(), health.start_flusher(health.buffer)]


def stop(stops):
    """At site shutdown: stop the background threads and write the check-ins still waiting."""
    for event in stops:
        event.set()
    health.flush(health.buffer)
