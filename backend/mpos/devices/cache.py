"""Every mPOS terminal's switches in memory, so a phone asking "may I start a bill?" is answered in
microseconds and never waits on Snowflake.

The switches come from Snowflake (devices/store.py): loaded in full at startup, and again whenever
MPOS_GLOBAL.CHANGE_SEQ moves - checked every REFRESH_SECONDS by a background thread. So a change made
by another running copy of the site still arrives here within that time.

What the phones report (bill open, last check-in, serial, app version) lives only here, per terminal,
and survives a reload. It is lost on restart; each phone re-reports it at its next check-in (30 s).
"""
import logging
import threading
from dataclasses import dataclass
from datetime import datetime

from . import state as S
from . import store

REFRESH_SECONDS = 15
GONE_SECONDS = 120  # a phone silent this long is taken as gone: its open bill no longer blocks re-pairing
log = logging.getLogger(__name__)


@dataclass
class Live:
    """What a phone last reported. Memory only."""
    bill_open: bool = False
    last_seen: datetime | None = None
    serial: str | None = None
    app_version: str | None = None
    ip: str | None = None


class Cache:
    def __init__(self):
        self._lock = threading.Lock()
        self.glob = S.Global()
        self.stores = {}     # store code -> Store
        self.terminals = {}  # terminal ID -> Terminal
        self._by_token = {}  # token hash -> terminal ID (active, paired terminals only)
        self.live = {}       # terminal ID -> Live
        self.seq = None      # CHANGE_SEQ of the copy held; None = never loaded
        self.loaded_at = None

    # ------------------------------------------------------------ loading

    def load(self):
        """Read every switch from Snowflake and replace the copy. Raises StoreUnavailable."""
        seq = store.change_seq()  # read first: a change landing during the load is picked up next time
        glob, stores, terminals = store.load_global(), store.load_stores(), store.load_terminals()
        by_token = {t.token_hash: tid for tid, t in terminals.items() if t.paired and t.status == "ACTIVE"}
        with self._lock:
            self.glob, self.stores, self.terminals, self._by_token = glob, stores, terminals, by_token
            self.seq, self.loaded_at = seq, datetime.now()

    def refresh_if_changed(self):
        """Reload when CHANGE_SEQ has moved. True if it reloaded."""
        if self.seq is not None and store.change_seq() == self.seq:
            return False
        self.load()
        return True

    def start_refresher(self):
        """Background thread: load now, then refresh_if_changed every REFRESH_SECONDS. Snowflake trouble
        is logged, and the phones keep being answered from the copy already held."""
        stop = threading.Event()

        def loop():
            self.tick()
            while not stop.wait(REFRESH_SECONDS):
                self.tick()

        threading.Thread(target=loop, name="mpos-device-cache", daemon=True).start()
        return stop

    def tick(self):
        try:
            self.refresh_if_changed()
        except Exception:  # noqa: BLE001 - keep answering phones from the copy held
            log.exception("mPOS device cache refresh failed - keeping the copy loaded at %s", self.loaded_at)

    # ------------------------------------------------------------ phone side

    def terminal_for_token(self, token_hash):
        """The active, paired Terminal holding this token, or None."""
        with self._lock:
            tid = self._by_token.get(token_hash)
            return self.terminals.get(tid) if tid else None

    def state_of(self, terminal_id):
        with self._lock:
            return self._state(terminal_id)

    def _state(self, terminal_id):
        t = self.terminals[terminal_id]
        store_ = self.stores.get(t.store_code) or S.Store(t.store_code)
        return S.terminal_state(t, store_, self.glob, self._live(terminal_id).bill_open)

    def _live(self, terminal_id):
        return self.live.setdefault(terminal_id, Live())

    def check_in(self, terminal_id, *, bill_open, serial=None, app_version=None, ip=None, now=None):
        """A heartbeat: note what the phone reported, return its State."""
        with self._lock:
            live = self._live(terminal_id)
            live.bill_open, live.last_seen = bill_open, now or datetime.now()
            live.serial, live.app_version, live.ip = serial, app_version, ip
            return self._state(terminal_id)

    def start_bill(self, terminal_id, now=None):
        """(allowed, State). Checking the state and marking the bill open happen under one lock, so a
        switch flipped at the same moment cannot let a new bill slip through."""
        with self._lock:
            current = self._state(terminal_id)
            if current.key != S.ON:
                return False, current
            live = self._live(terminal_id)
            live.bill_open, live.last_seen = True, now or datetime.now()
            return True, current

    def billing_phone(self, terminal_id, serial, now=None):
        """The terminal on which a bill is open right now - on `terminal_id` itself or on another terminal
        held by the phone `serial` - or None. Pairing, unpairing and retiring wait for that bill; a
        phone silent for GONE_SECONDS (lost, switched off) does not hold them up."""
        now = now or datetime.now()
        with self._lock:
            mine = [tid for tid, t in self.terminals.items() if t.device_serial == serial and t.paired] if serial else []
            return next((tid for tid in [terminal_id, *mine] if self._billing(tid, now)), None)

    def _billing(self, terminal_id, now):
        t, live = self.terminals.get(terminal_id), self.live.get(terminal_id)
        return bool(t and t.paired and live and live.bill_open and live.last_seen
                    and (now - live.last_seen).total_seconds() < GONE_SECONDS)

    def snapshot(self, now=None):
        """(global, {code: Store}, [(Terminal, State, bill open now, Live)]) in one consistent copy, for
        the portal screens. Retired terminals are left out."""
        now = now or datetime.now()
        with self._lock:
            rows = [(t, self._state(tid), self._billing(tid, now), self._live(tid))
                    for tid, t in self.terminals.items() if t.status == "ACTIVE"]
            return self.glob, dict(self.stores), rows

    def lock_here(self, terminal_id, reason):
        """Lock in this copy straight away, while the lock is being saved to Snowflake."""
        with self._lock:
            t = self.terminals[terminal_id]
            t.immediate_lock, t.lock_reason = True, reason
            return self._state(terminal_id)

    def end_bill(self, terminal_id, now=None):
        """(state before, state after). DRAINING before and OFF after = the drain just finished."""
        with self._lock:
            before = self._state(terminal_id)
            live = self._live(terminal_id)
            live.bill_open, live.last_seen = False, now or datetime.now()
            return before, self._state(terminal_id)


current = Cache()
