"""Whether an mPOS terminal may bill, worked out from its switches. No I/O: the phone API, the screens
and the tests all use this one function.

A terminal bills only when every level is on, checked in this order (the first one off is the reason
shown): all mPOS (HO), its store (HO), its store's day (only for stores that use Open day), HO's veto
on the terminal, and the store approver's own switch. Switched off while a bill is open, a terminal
DRAINS: that bill may finish, no new bill starts. An immediate lock (lost or stolen phone), a retired
terminal and one not paired to a phone are OFF straight away, open bill or not.
"""
from dataclasses import dataclass

ON, DRAINING, OFF = "ON", "DRAINING", "OFF"

MAX_MPOS = 5  # active mPOS terminals a store may have, unless HO sets MPOS_STORES.MAX_MPOS otherwise

# Why a terminal is not ON.
RETIRED = "RETIRED"
NOT_PAIRED = "NOT_PAIRED"
LOCKED = "LOCKED"
ALL_MPOS_OFF = "ALL_MPOS_OFF"
STORE_OFF_BY_HO = "STORE_OFF_BY_HO"
DAY_CLOSED = "DAY_CLOSED"
VETOED = "VETOED"
OFF_BY_STORE = "OFF_BY_STORE"

REASON_TEXT = {
    RETIRED: "Terminal retired",
    NOT_PAIRED: "Not paired to a phone",
    LOCKED: "Locked by HO",
    ALL_MPOS_OFF: "All mPOS switched off by HO",
    STORE_OFF_BY_HO: "Store disabled by HO",
    DAY_CLOSED: "Day not opened",
    VETOED: "Blocked by HO",
    OFF_BY_STORE: "Disabled by store",
}


@dataclass
class Global:
    enabled: bool = True
    reason: str = ""
    version: int = 1


@dataclass
class Store:
    store_code: str
    ho_enabled: bool = True
    ho_reason: str = ""
    day_control: bool = False  # False = the store does not use Open day; DAY_OPEN is ignored
    day_open: bool = False
    day_close_time: str | None = None
    approver: str | None = None
    max_mpos: int = MAX_MPOS
    next_seq: int = 1
    version: int = 1


@dataclass
class Terminal:
    terminal_id: str
    store_code: str
    seq: int = 0
    status: str = "ACTIVE"
    store_enabled: bool = True
    ho_veto: bool = False
    veto_reason: str = ""
    immediate_lock: bool = False
    lock_reason: str = ""
    token_hash: str | None = None
    device_serial: str | None = None
    app_version: str | None = None
    paired_at: str | None = None
    last_seen_at: str | None = None
    version: int = 1

    @property
    def paired(self):
        return bool(self.token_hash)


@dataclass(frozen=True)
class State:
    key: str                  # ON, DRAINING or OFF
    reason: str | None = None  # one of the reason codes above, None when ON
    detail: str = ""           # HO's own words, e.g. 'Renovation'

    def text(self):
        """What the phone and the screens show, e.g. 'Store disabled by HO: Renovation'."""
        if self.reason is None:
            return "Enabled"
        words = REASON_TEXT[self.reason]
        return f"{words}: {self.detail}" if self.detail else words


def terminal_id(store_code, seq):
    """HD22 + 1 -> 'HD22_MPOS1'."""
    return f"{store_code.strip().upper()}_MPOS{int(seq)}"


def _blocked_by(terminal, store, glob):
    """(reason, detail) for the first level that is off, or None when every level is on."""
    if not glob.enabled:
        return ALL_MPOS_OFF, glob.reason
    if not store.ho_enabled:
        return STORE_OFF_BY_HO, store.ho_reason
    if store.day_control and not store.day_open:
        return DAY_CLOSED, ""
    if terminal.ho_veto:
        return VETOED, terminal.veto_reason
    if not terminal.store_enabled:
        return OFF_BY_STORE, ""
    return None


def terminal_state(terminal, store, glob, bill_open=False):
    """The terminal's State. bill_open = the phone reported a bill in progress."""
    if terminal.status == "RETIRED":
        return State(OFF, RETIRED)
    if not terminal.paired:
        return State(OFF, NOT_PAIRED)
    if terminal.immediate_lock:
        return State(OFF, LOCKED, terminal.lock_reason or "")
    blocked = _blocked_by(terminal, store, glob)
    if blocked is None:
        return State(ON)
    reason, detail = blocked
    return State(DRAINING if bill_open else OFF, reason, detail or "")
