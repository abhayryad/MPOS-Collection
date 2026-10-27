"""The real portal with a fake Snowflake behind its mPOS device code - for trying the phone API and
load-testing it with tools/phone_sim.py without touching real tables or using up terminal numbers.

    python tools/fake_device_portal.py --stores 450                    -> http://127.0.0.1:8029, 900 terminals
    python tools/fake_device_portal.py --stores 450 --per-store 1-6    a mix: 1 to 6 terminals per store
    python tools/fake_device_portal.py --real-stores --per-store 1-5   the real active stores (store master)

--real-stores reads the active stores (code, name, zone, region, state) once from Snowflake's store
master, read-only. Everything else - terminals, phones, switches - stays fake and in memory.

Terminals are FK001_MPOS1, FK001_MPOS2, FK002_MPOS1 ... already paired: the token of each is
"sim-<terminal ID>" and its serial "SN-<terminal ID>" (phone_sim.py run --fake reads the list from here).
Pairing works with the code SIM234 for any terminal.

New terminals are added with POST /sim/add {"store": "FK001", "count": 1, "autopair": true} - "store"
null adds to every store. Like `add-mpos` they take the store's next number (never reused), stop at
MAX_MPOS (5) and come with a pairing code; with "autopair" a running phone_sim.py pairs a simulated
phone to each one, otherwise it waits for `phone_sim.py pair` with the code.

The switches are flipped with POST /sim/switch (phone_sim.py switch ... does it for you):
    {"level": "global"}                                 all mPOS
    {"level": "store", "target": "FK001"}               one store (HO switch)
    {"level": "terminal", "target": "FK001_MPOS1"}      one terminal (store's switch)
    {"level": "veto" | "lock", "target": ...}           HO veto / immediate lock on a terminal
each with "on": true/false and an optional "reason". GET /sim/stats shows what reached "Snowflake".

--host 0.0.0.0 shares it on the LAN (it prints the link). The /sim pages have no login - fake data only.

--as USER:LOCATION opens the portal's own screens (mPOS Devices tab) as that user without a login, e.g.
--as v1:HB05 (that store's approver) or --as ho1:HO - this PC only, for trying the screens on fake data.

http://127.0.0.1:8029/sim shows every phone live, coloured by state, with the switches as buttons.
"""
import argparse
import random
from datetime import datetime
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import uvicorn  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from mpos.api import device_api  # noqa: E402
from mpos.devices import cache, health, pairing, store  # noqa: E402
from mpos.devices import state as S  # noqa: E402

PAIR_CODE = "SIM234"
VIEW = Path(__file__).with_name("fake_device_portal.html")


def fake_stores(count):
    return {f"FK{n:03d}": {"name": f"Fake store {n}", "zone": "", "region": "", "state": ""}
            for n in range(1, count + 1)}


def real_stores():
    """The active stores from the store master: {code: {name, zone, region, state}}."""
    from mpos import stores as master
    return {s["code"]: {k: s[k] for k in ("name", "zone", "region", "state")} for s in master.store_master()}


def terminals(codes, per_store="2"):
    """[(store code, seq)] - per_store is a count ("2") or a range ("1-6"); a range gives each store its
    own count, the same one every run."""
    low, _, high = per_store.partition("-")
    low, high = int(low), int(high or low)
    return [(code, seq) for code in codes for seq in range(1, random.Random(code).randint(low, high) + 1)]


class FakeSnowflake:
    """Stands in for every devices/store.py function the phone API and the background threads call."""

    def __init__(self, info, per_store="2"):
        self._lock = threading.Lock()
        self.seq = 1
        self.glob = S.Global()
        self.info = info  # store code -> {name, zone, region, state}
        self.stores = {code: S.Store(code) for code in info}
        self.terminals = {}
        for code, seq in terminals(info, per_store):
            tid = S.terminal_id(code, seq)
            self.terminals[tid] = S.Terminal(tid, code, seq, token_hash=pairing.token_digest(f"sim-{tid}"),
                                             device_serial=f"SN-{tid}")
            self.stores[code].next_seq = seq + 1
        self.premade = set(self.terminals)  # these have "sim-" tokens; added ones pair with a code
        self.pair_codes = {}                # terminal ID -> open pairing code
        self.autopair = set()               # added terminals a simulated phone should pair to
        self.heartbeats = self.inserts = self.last_seen_updates = 0
        self.events = []

    # reads - copies, as Snowflake would hand back fresh objects
    def change_seq(self):
        return self.seq

    def load_global(self):
        return S.Global(**vars(self.glob))

    def load_stores(self):
        return {k: S.Store(**vars(v)) for k, v in self.stores.items()}

    def load_terminals(self):
        return {k: S.Terminal(**vars(v)) for k, v in self.terminals.items()}

    # writes
    def insert_heartbeats(self, rows):
        with self._lock:
            self.heartbeats += len(rows)
            self.inserts += 1

    def update_last_seen(self, since):
        self.last_seen_updates += 1

    def record(self, event, level, **kw):
        self.events.append({"id": len(self.events) + 1, "time": datetime.now().isoformat(timespec="seconds"),
                            "event": event, "level": level, **kw})

    # ---- what the portal screens write (same names and arguments as devices/store.py)
    def _changed(self, obj, version, what):
        if version is not None and obj.version != version:
            raise store.Conflict(f"{what} was changed by someone else since you loaded it - reload and try again")
        obj.version += 1
        self.seq += 1

    def _active(self, tid):
        t = self.terminals.get((tid or "").upper())
        if not t or t.status != "ACTIVE":
            raise ValueError(f"No active terminal {tid}")
        return t

    def set_global(self, on, reason, actor, version=None, ip=None):
        with self._lock:
            self._changed(self.glob, version, "All mPOS")
            self.glob.enabled, self.glob.reason = on, "" if on else reason
        self.record("GLOBAL_ON" if on else "GLOBAL_OFF", "GLOBAL", actor=actor, new="ON" if on else "OFF", reason=reason)

    def set_stores(self, codes, on, reason, actor, version=None, ip=None):
        with self._lock:
            hit = [self.stores[c] for c in (codes if codes is not None else self.stores) if c in self.stores]
            for s in hit:
                self._changed(s, version if len(hit) == 1 else None, f"Store {s.store_code}")
                s.ho_enabled, s.ho_reason = on, "" if on else reason
        self.record("STORE_ON" if on else "STORE_OFF", "STORE", actor=actor,
                    target=hit[0].store_code if len(hit) == 1 else None, reason=reason, affected=len(hit))
        return len(hit)

    def set_terminal(self, terminal_id, on, actor, reason=None, version=None, ip=None):
        with self._lock:
            t = self._active(terminal_id)
            self._changed(t, version, terminal_id)
            t.store_enabled = on
        self.record("TERMINAL_ON" if on else "TERMINAL_OFF", "TERMINAL", actor=actor, target=t.terminal_id,
                    new="ON" if on else "OFF", reason=reason)

    def set_veto(self, terminal_id, blocked, reason, actor, version=None, ip=None):
        with self._lock:
            t = self._active(terminal_id)
            self._changed(t, version, terminal_id)
            t.ho_veto, t.veto_reason = blocked, reason if blocked else ""
        self.record("VETO_ON" if blocked else "VETO_OFF", "TERMINAL", actor=actor, target=t.terminal_id, reason=reason)

    def set_lock(self, terminal_id, locked, reason, actor, version=None, ip=None):
        with self._lock:
            t = self._active(terminal_id)
            self._changed(t, version, terminal_id)
            t.immediate_lock, t.lock_reason = locked, reason if locked else ""
        self.record("LOCK_ON" if locked else "LOCK_OFF", "TERMINAL", actor=actor, target=t.terminal_id, reason=reason)

    def set_day(self, store_code, open_, actor, ip=None):
        with self._lock:
            s = self.stores.get(store_code)
            if not s or not s.day_control:
                raise store.Conflict(f"{store_code} does not use Open day")
            self._changed(s, None, store_code)
            s.day_open = open_
        self.record("DAY_OPEN" if open_ else "DAY_CLOSE", "STORE", actor=actor, target=store_code)

    def set_store_settings(self, store_code, changes, actor, version=None, ip=None):
        with self._lock:
            s = self.stores.setdefault(store_code, S.Store(store_code))
            self._changed(s, version, f"Store {store_code}")
            for k, v in changes.items():
                setattr(s, k, v)
        for k, v in changes.items():
            self.record(store.STORE_SETTINGS[k][1], "STORE", actor=actor, target=store_code, new=v)

    def issue_pair_code(self, terminal_id, actor, ip=None):
        with self._lock:
            t = self._active(terminal_id)
            self.pair_codes[t.terminal_id] = code = pairing.new_code()
            self.autopair.discard(t.terminal_id)  # a person pairs this one, not phone_sim
            self.seq += 1
        self.record("PAIR_CODE_ISSUED", "TERMINAL", actor=actor, target=t.terminal_id, reason="valid 15 minutes")
        return code

    def unpair_terminal(self, terminal_id, actor, reason=None, ip=None):
        with self._lock:
            t = self._active(terminal_id)
            old, t.token_hash, t.device_serial = t.device_serial, None, None
            self.pair_codes.pop(t.terminal_id, None)
            self.seq += 1
        self.record("UNPAIRED", "TERMINAL", actor=actor, target=t.terminal_id, old=old, reason=reason)

    def retire_terminal(self, terminal_id, actor, reason=None, ip=None):
        with self._lock:
            t = self._active(terminal_id)
            t.status, t.token_hash, t.device_serial = "RETIRED", None, None
            self.seq += 1
        self.record("RETIRED", "TERMINAL", actor=actor, target=t.terminal_id, reason=reason)

    def add_terminals(self, store_codes, count, actor, ip=None):
        added, full = [], []
        for code in store_codes:
            if code not in self.stores:
                self.stores[code] = S.Store(code)
            got, at_limit = self.add(code, count, autopair=True)
            added += [tid for tid, _ in got]
            full += at_limit
        return added, full

    def load_history(self, store_code=None, terminal_id=None, limit=200):
        def mine(e):
            target = e.get("target") or ""
            if terminal_id:
                return target == terminal_id
            return not store_code or target == store_code or target.startswith(f"{store_code}_MPOS")
        keys = ("id", "time", "actor", "level", "target", "event", "old", "new", "reason")
        return [{**{k: e.get(k) for k in keys}, "affected": e.get("affected", 1)}
                for e in reversed(self.events) if mine(e)][:limit]

    def lock_terminal(self, terminal_id, reason, actor, ip=None):
        self.switch("lock", terminal_id, False, reason)

    def redeem_pair_code(self, terminal_id, code, *, serial, app_version, ip=None):
        tid = (terminal_id or "").strip().upper()
        good = {pairing.code_digest(PAIR_CODE), pairing.code_digest(self.pair_codes.get(tid, PAIR_CODE))}
        if tid not in self.terminals or pairing.code_digest(code) not in good:
            raise store.PairingError("Wrong code - 4 tries left")
        token = pairing.new_token()
        with self._lock:
            for other in self.terminals.values():  # one phone, one terminal
                if other.terminal_id != tid and other.device_serial == serial and other.paired:
                    other.token_hash = other.device_serial = None
            t = self.terminals[tid]
            t.token_hash, t.device_serial = pairing.token_digest(token), serial  # a lock stays until HO lifts it
            self.pair_codes.pop(tid, None)
            self.seq += 1
        self.record("PAIRED", "TERMINAL", actor=tid, target=tid, new=serial)
        return token

    def add(self, store_code, count, autopair):
        """Like store.add_terminal + issue_pair_code, for one store or (store_code None) every store.
        Returns ([(terminal ID, pairing code)], [stores at their limit])."""
        added, full = [], []
        with self._lock:
            for code in [store_code] if store_code else list(self.stores):
                s = self.stores[code]
                for _ in range(count):
                    active = sum(1 for t in self.terminals.values() if t.store_code == code and t.status == "ACTIVE")
                    if active >= s.max_mpos:
                        full.append(code)
                        break
                    tid = S.terminal_id(code, s.next_seq)
                    self.terminals[tid] = S.Terminal(tid, code, s.next_seq)
                    s.next_seq += 1
                    self.pair_codes[tid] = pairing.new_code()
                    if autopair:
                        self.autopair.add(tid)
                    added.append((tid, self.pair_codes[tid]))
            self.seq += 1
        self.record("TERMINAL_ADDED", "STORE", actor="sim", target=store_code or "ALL", affected=len(added))
        return added, full

    # the HO / store screens, for now
    def switch(self, level, target, on, reason=""):
        with self._lock:
            if level == "global":
                self.glob.enabled, self.glob.reason = on, reason
            elif level == "store":
                s = self.stores[target]
                s.ho_enabled, s.ho_reason = on, reason
            elif level in ("terminal", "veto", "lock"):
                t = self.terminals[target]
                if level == "terminal":
                    t.store_enabled = on
                elif level == "veto":
                    t.ho_veto, t.veto_reason = not on, reason
                else:
                    t.immediate_lock, t.lock_reason = not on, reason
            else:
                raise KeyError(level)
            self.seq += 1


class Add(BaseModel):
    store: str | None = None  # None = every store
    count: int = Field(1, ge=1, le=S.MAX_MPOS)
    autopair: bool = True


class Switch(BaseModel):
    level: str
    target: str | None = None
    on: bool
    reason: str = ""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stores", type=int, default=450, help="how many fake stores (default 450)")
    ap.add_argument("--real-stores", action="store_true", help="use the real active stores from the store master")
    ap.add_argument("--per-store", default="2", help='terminals per store: a count, or a range like "1-6"')
    ap.add_argument("--port", type=int, default=8029)
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to share it on the LAN (default: this PC only)")
    ap.add_argument("--flush-seconds", type=int, default=10, help="check-in writes (real portal: 300)")
    ap.add_argument("--as", dest="as_user", metavar="USER:LOCATION",
                    help="open the portal screens as this user, no login, e.g. v1:HB05 or ho1:HO (this PC only)")
    args = ap.parse_args()
    if args.as_user and args.host != "127.0.0.1":
        ap.error("--as works only with the default --host 127.0.0.1: it skips the login")

    info = real_stores() if args.real_stores else fake_stores(args.stores)
    fake = FakeSnowflake(info, args.per_store)
    for name in ("change_seq", "load_global", "load_stores", "load_terminals", "insert_heartbeats",
                 "update_last_seen", "record", "lock_terminal", "redeem_pair_code", "set_global", "set_stores",
                 "set_terminal", "set_veto", "set_lock", "set_day", "set_store_settings", "issue_pair_code",
                 "unpair_terminal", "retire_terminal", "add_terminals", "load_history"):
        setattr(store, name, getattr(fake, name))
    health.FLUSH_SECONDS = args.flush_seconds
    device_api._pair_limit = pairing.RateLimit(limit=100_000, seconds=60)  # every simulated phone shares one IP

    from mpos.main import app

    if args.as_user:
        from mpos.auth import deps
        from mpos.auth import roles as R
        from mpos.auth.store import User
        username, _, location = args.as_user.partition(":")
        who = User(username.lower(), f"Test user {username}", roles=[R.DEVICES], locations=[location.upper() or "HO"])
        app.dependency_overrides[deps.current_user] = lambda: who

    @app.post("/sim/switch")
    def sim_switch(body: Switch):
        try:
            fake.switch(body.level, body.target, body.on, body.reason)
        except KeyError as e:
            raise HTTPException(400, f"Unknown {e}")
        cache.current.refresh_if_changed()  # the real portal notices within 15 s; here at once
        return {"ok": True, "change_seq": fake.seq}

    @app.post("/sim/add")
    def sim_add(body: Add):
        code = body.store.strip().upper() if body.store else None
        if code and code not in fake.stores:
            raise HTTPException(400, f"No store {code}")
        added, full = fake.add(code, body.count, body.autopair)
        cache.current.refresh_if_changed()
        return {"added": [{"terminal": t, "code": c} for t, c in added], "at_limit": full}

    @app.get("/sim/stats")
    def sim_stats():
        return {"terminals": len(fake.terminals), "heartbeats_written": fake.heartbeats, "inserts": fake.inserts,
                "last_seen_updates": fake.last_seen_updates, "waiting": len(health.buffer),
                "dropped": health.buffer.dropped, "events": fake.events[-20:]}

    @app.get("/sim/live")
    def sim_live():
        c = cache.current
        with c._lock:
            phones = {}
            for tid, t in c.terminals.items():
                st, live = c._state(tid), c._live(tid)
                phones[tid] = {"store": t.store_code, "state": st.key, "message": st.text(), "bill": live.bill_open,
                               "seen": live.last_seen.strftime("%H:%M:%S") if live.last_seen else None,
                               "store_switch": t.store_enabled, "veto": t.ho_veto, "lock": t.immediate_lock,
                               "paired": t.paired, "pair_code": fake.pair_codes.get(tid),
                               "premade": tid in fake.premade, "autopair": tid in fake.autopair}
        return {"global_on": fake.glob.enabled, "stores": fake.info, "stores_off": [k for k, s in fake.stores.items() if not s.ho_enabled],
                "phones": phones, "stats": {**sim_stats(), "events": None}}

    @app.get("/sim", response_class=HTMLResponse)
    def sim_page():
        return VIEW.read_text(encoding="utf-8")

    routes = app.router.routes  # /sim/* ahead of the frontend mounted at /
    ours = [r for r in routes if getattr(r, "path", "").startswith("/sim")]
    routes[:] = ours + [r for r in routes if r not in ours]
    print(f"Fake device portal: {len(fake.stores)} stores, {len(fake.terminals)} terminals, "
          f"check-ins written every {args.flush_seconds} s.")
    for ip in (lan_ips() if args.host == "0.0.0.0" else [args.host]):
        print(f"  Live view: http://{ip}:{args.port}/sim")
    if args.as_user:
        print(f"  Portal screens as {args.as_user}: http://127.0.0.1:{args.port}/#/devices")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


def lan_ips():
    """This PC's addresses, to print the link others on the LAN can open."""
    import socket
    ips = {i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    return ["127.0.0.1", *sorted(ip for ip in ips if not ip.startswith("127."))]


if __name__ == "__main__":
    main()
