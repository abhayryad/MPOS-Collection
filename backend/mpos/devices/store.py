"""mPOS device control tables in Snowflake (sql/snowflake/device_tables.sql). The tables are created
on first use, like the user tables.

Device code has its OWN Snowflake connection (same login as auth/store.py), so a large batch of phone
check-ins never makes a login or an admin screen wait behind it. History writes never raise: if
Snowflake cannot be reached the event goes to output/device_events_fallback.jsonl instead.
"""
import hmac
import json
import threading
from contextlib import contextmanager
from datetime import datetime

import snowflake.connector

from ..auth import store as sf
from ..config import settings
from . import pairing
from . import state as S

GLOBAL = sf._table("MPOS_GLOBAL")
STORES = sf._table("MPOS_STORES")
TERMINALS = sf._table("MPOS_TERMINALS")
HISTORY = sf._table("MPOS_DEVICE_HISTORY")
HEARTBEATS = sf._table("MPOS_DEVICE_HEARTBEATS")

LEVELS = ("GLOBAL", "STORE", "TERMINAL")

_lock = threading.Lock()
_conn = None
_tables_ready = False


def _ensure_tables(conn):
    """Run device_tables.sql once per process."""
    global _tables_ready
    if _tables_ready:
        return
    try:
        ddl = (settings.sql_dir / "snowflake" / "device_tables.sql").read_text(encoding="utf-8")
        body = "\n".join(line for line in ddl.splitlines() if not line.strip().startswith("--"))
        for stmt in (s.strip() for s in body.split(";")):
            if stmt:
                conn.cursor().execute(stmt)
    except snowflake.connector.errors.Error as e:
        raise sf.StoreUnavailable(f"Could not prepare the mPOS device tables: {getattr(e, 'msg', e)}")
    _tables_ready = True


def _run(sql, params=(), fetch=True, many=False):
    """Run one statement on the device connection; rows as dicts when fetch. many = params is a list
    of rows for one INSERT (sent as a single array bind). Reconnects once if the connection dropped."""
    global _conn
    with _lock:
        for attempt in (1, 2):
            try:
                if _conn is None or _conn.is_closed():
                    _conn = sf._connect(query_tag="mpos-collection-devices")
                _ensure_tables(_conn)
                cur = _conn.cursor(snowflake.connector.DictCursor)
                if many:
                    cur.executemany(sql, [list(r) for r in params])
                else:
                    cur.execute(sql, list(params))
                return cur.fetchall() if fetch else cur.rowcount
            except snowflake.connector.errors.ProgrammingError as e:
                if e.errno not in sf.SESSION_EXPIRED or attempt == 2:
                    raise  # bad SQL / constraint - not a connection problem
                _conn = None  # login token expired (e.g. overnight) - log in again
            except (snowflake.connector.errors.Error, OSError) as e:
                _conn = None
                if attempt == 2:
                    raise sf.StoreUnavailable(f"Snowflake error: {e}")


@contextmanager
def _transaction():
    """A cursor inside BEGIN ... COMMIT (ROLLBACK on any error). Holds the device connection for the
    whole transaction so no other device statement lands in the middle of it."""
    global _conn
    with _lock:
        try:
            if _conn is None or _conn.is_closed():
                _conn = sf._connect(query_tag="mpos-collection-devices")
            _ensure_tables(_conn)
            cur = _conn.cursor(snowflake.connector.DictCursor)
            cur.execute("BEGIN")
        except (snowflake.connector.errors.Error, OSError) as e:
            _conn = None
            raise sf.StoreUnavailable(f"Snowflake error: {e}")
        try:
            yield cur
            cur.execute("COMMIT")
        except BaseException:
            try:
                cur.execute("ROLLBACK")
            except Exception:  # noqa: BLE001 - the connection may be gone; Snowflake rolls back on its own then
                _conn = None
            raise


class PairingError(Exception):
    """A pairing attempt was refused; the message is safe to show on the phone."""


class LimitReached(Exception):
    """The store already has its maximum number of active mPOS terminals."""


class Conflict(Exception):
    """The row changed (or went) since the screen loaded it; the message is safe to show."""


# ---------------------------------------------------------------- reading

def _global(row):
    return S.Global(enabled=bool(row["ENABLED"]), reason=row["REASON"] or "", version=int(row["VERSION"]))


def _store(row):
    return S.Store(
        store_code=row["STORE_CODE"], ho_enabled=bool(row["HO_ENABLED"]), ho_reason=row["HO_REASON"] or "",
        day_control=bool(row["DAY_CONTROL"]), day_open=bool(row["DAY_OPEN"]),
        day_close_time=row["DAY_CLOSE_TIME"], approver=row["APPROVER"],
        max_mpos=int(row["MAX_MPOS"]), next_seq=int(row["NEXT_SEQ"]), version=int(row["VERSION"]),
    )


def _terminal(row):
    return S.Terminal(
        terminal_id=row["TERMINAL_ID"], store_code=row["STORE_CODE"], seq=int(row["SEQ"]),
        status=row["STATUS"], store_enabled=bool(row["STORE_ENABLED"]),
        ho_veto=bool(row["HO_VETO"]), veto_reason=row["VETO_REASON"] or "",
        immediate_lock=bool(row["IMMEDIATE_LOCK"]), lock_reason=row["LOCK_REASON"] or "",
        token_hash=row["TOKEN_HASH"], device_serial=row["DEVICE_SERIAL"], app_version=row["APP_VERSION"],
        paired_at=sf._ts(row["PAIRED_AT"]), last_seen_at=sf._ts(row["LAST_SEEN_AT"]),
        version=int(row["VERSION"]),
    )


def load_global():
    rows = _run(f"SELECT * FROM {GLOBAL} WHERE ID = 1")
    return _global(rows[0]) if rows else S.Global()


def load_stores():
    """{store code: Store} for every store with mPOS."""
    return {r["STORE_CODE"]: _store(r) for r in _run(f"SELECT * FROM {STORES} ORDER BY STORE_CODE")}


def load_terminals():
    """{terminal ID: Terminal}, retired ones included."""
    return {r["TERMINAL_ID"]: _terminal(r)
            for r in _run(f"SELECT * FROM {TERMINALS} ORDER BY STORE_CODE, SEQ")}


def change_seq():
    """MPOS_GLOBAL.CHANGE_SEQ: moves whenever any switch changes. The query text never varies, so while
    nothing changes Snowflake answers it from its result cache without starting the warehouse."""
    rows = _run(f"SELECT CHANGE_SEQ FROM {GLOBAL} WHERE ID = 1")
    return int(rows[0]["CHANGE_SEQ"]) if rows else 0


def bump_change():
    """Call after every switch change so every running site reloads its copy."""
    _run(f"UPDATE {GLOBAL} SET CHANGE_SEQ = CHANGE_SEQ + 1 WHERE ID = 1", fetch=False)


# ---------------------------------------------------------------- terminals and pairing

def add_terminal(store_code, actor, ip=None):
    """Create the store's next terminal (HD22 -> HD22_MPOS3) and return its ID. The number comes from
    MPOS_STORES.NEXT_SEQ and is never reused. Raises LimitReached at the store's MAX_MPOS."""
    code = (store_code or "").strip().upper()
    if not code:
        raise ValueError("Store code is required")
    with _transaction() as cur:
        cur.execute(f"MERGE INTO {STORES} s USING (SELECT ? AS STORE_CODE) n ON s.STORE_CODE = n.STORE_CODE "
                    f"WHEN NOT MATCHED THEN INSERT (STORE_CODE, MAX_MPOS, CREATED_BY) VALUES (n.STORE_CODE, ?, ?)",
                    [code, S.MAX_MPOS, actor])  # the limit is set here: Snowflake cannot change a column default
        cur.execute(f"SELECT NEXT_SEQ, MAX_MPOS FROM {STORES} WHERE STORE_CODE = ?", [code])
        st = cur.fetchall()[0]
        cur.execute(f"SELECT COUNT(*) AS N FROM {TERMINALS} WHERE STORE_CODE = ? AND STATUS = 'ACTIVE'", [code])
        limit = min(int(st["MAX_MPOS"]), S.MAX_MPOS)
        if int(cur.fetchall()[0]["N"]) >= limit:
            raise LimitReached(f"{code} already has {limit} active mPOS terminals")
        seq = int(st["NEXT_SEQ"])
        cur.execute(f"UPDATE {STORES} SET NEXT_SEQ = ?, UPDATED_AT = CURRENT_TIMESTAMP(), UPDATED_BY = ? "
                    f"WHERE STORE_CODE = ? AND NEXT_SEQ = ?", [seq + 1, actor, code, seq])
        if cur.rowcount != 1:  # another copy of the site took this number first
            raise sf.StoreUnavailable("The store was changed at the same moment - try again")
        tid = S.terminal_id(code, seq)
        cur.execute(f"INSERT INTO {TERMINALS} (TERMINAL_ID, STORE_CODE, SEQ, CREATED_BY) VALUES (?, ?, ?, ?)",
                    [tid, code, seq, actor])
    bump_change()
    record("TERMINAL_ADDED", "TERMINAL", actor=actor, target=tid, new="ACTIVE", ip=ip)
    return tid


def issue_pair_code(terminal_id, actor, ip=None):
    """A fresh pairing code for the terminal, returned once (only its hash is kept). A phone already
    paired to the terminal keeps working - its bill is never cut - until a new phone pairs with this
    code. A lost or stolen phone is stopped with an immediate lock, or unpaired, not with a code."""
    code = pairing.new_code()
    n = _run(f"UPDATE {TERMINALS} SET PAIR_CODE_HASH = ?, PAIR_EXPIRES_AT = DATEADD(minute, ?, CURRENT_TIMESTAMP()), "
             f"PAIR_ATTEMPTS = 0, VERSION = VERSION + 1, "
             f"UPDATED_AT = CURRENT_TIMESTAMP(), UPDATED_BY = ? WHERE TERMINAL_ID = ? AND STATUS = 'ACTIVE'",
             [pairing.code_digest(code), pairing.CODE_MINUTES, actor, terminal_id], fetch=False)
    if n != 1:
        raise ValueError(f"No active terminal {terminal_id}")
    bump_change()
    record("PAIR_CODE_ISSUED", "TERMINAL", actor=actor, target=terminal_id,
           reason=f"valid {pairing.CODE_MINUTES} minutes", ip=ip)
    return code


def lock_terminal(terminal_id, reason, actor, ip=None):
    """Immediate lock: the terminal stops at once, even mid-bill, until HO unlocks it."""
    n = _run(f"UPDATE {TERMINALS} SET IMMEDIATE_LOCK = TRUE, LOCK_REASON = ?, VERSION = VERSION + 1, "
             f"UPDATED_AT = CURRENT_TIMESTAMP(), UPDATED_BY = ? WHERE TERMINAL_ID = ?",
             [_cut(reason, 500), actor, terminal_id], fetch=False)
    if n == 1:
        bump_change()
        record("LOCK_ON", "TERMINAL", actor=actor, target=terminal_id, new="LOCKED", reason=reason, ip=ip)


def redeem_pair_code(terminal_id, code, *, serial, app_version, ip=None):
    """Pair a phone: check the code, and return the phone's new token (shown once). Raises PairingError.

    The new token replaces the old one, so a phone paired before stops working now. One phone holds
    one terminal: if this phone was paired to another terminal, it is unpaired from it. The caller
    first makes sure no bill is open on either (see cache.Cache.billing_phone)."""
    tid = (terminal_id or "").strip().upper()
    serial = _cut(serial, 100)
    rows = _run(f"SELECT STATUS, PAIR_CODE_HASH, PAIR_ATTEMPTS, DEVICE_SERIAL, TOKEN_HASH, "
                f"PAIR_EXPIRES_AT > CURRENT_TIMESTAMP() AS CODE_LIVE FROM {TERMINALS} WHERE TERMINAL_ID = ?", [tid])
    row = rows[0] if rows else None
    if not row or row["STATUS"] != "ACTIVE" or not row["PAIR_CODE_HASH"] or not row["CODE_LIVE"]:
        raise PairingError("No open pairing code for this terminal - ask the store for a new code")
    if not hmac.compare_digest(row["PAIR_CODE_HASH"], pairing.code_digest(code)):
        tries = int(row["PAIR_ATTEMPTS"]) + 1
        left = pairing.MAX_ATTEMPTS - tries
        cancel = ", PAIR_CODE_HASH = NULL, PAIR_EXPIRES_AT = NULL" if left <= 0 else ""
        _run(f"UPDATE {TERMINALS} SET PAIR_ATTEMPTS = ?{cancel} WHERE TERMINAL_ID = ?", [tries, tid], fetch=False)
        record("PAIR_FAILED", "TERMINAL", actor=tid, target=tid, reason=f"wrong code, try {tries}", ip=ip)
        if left <= 0:
            raise PairingError("Too many wrong codes - this code is cancelled, ask the store for a new one")
        raise PairingError(f"Wrong code - {left} {'try' if left == 1 else 'tries'} left")
    token = pairing.new_token()
    with _transaction() as cur:
        cur.execute(f"UPDATE {TERMINALS} SET TOKEN_HASH = ?, PAIR_CODE_HASH = NULL, PAIR_EXPIRES_AT = NULL, "
                    f"PAIR_ATTEMPTS = 0, DEVICE_SERIAL = ?, APP_VERSION = ?, PAIRED_AT = CURRENT_TIMESTAMP(), "
                    f"PAIRED_BY = ?, VERSION = VERSION + 1, UPDATED_AT = CURRENT_TIMESTAMP(), UPDATED_BY = ? "
                    f"WHERE TERMINAL_ID = ? AND PAIR_CODE_HASH = ?",
                    [pairing.token_digest(token), serial, _cut(app_version, 30), tid, tid, tid,
                     row["PAIR_CODE_HASH"]])
        if cur.rowcount != 1:  # the same code was used by another phone a moment ago
            raise PairingError("This code has just been used - ask the store for a new one")
        cur.execute(f"SELECT TERMINAL_ID FROM {TERMINALS} WHERE DEVICE_SERIAL = ? AND TERMINAL_ID <> ? "
                    f"AND STATUS = 'ACTIVE' AND TOKEN_HASH IS NOT NULL", [serial, tid])
        moved_from = [r["TERMINAL_ID"] for r in cur.fetchall()]
        if moved_from:
            cur.execute(f"UPDATE {TERMINALS} SET {_UNPAIR}, UPDATED_BY = ? WHERE TERMINAL_ID IN "
                        f"({', '.join('?' * len(moved_from))})", [tid, *moved_from])
    bump_change()
    replaced = row["DEVICE_SERIAL"] if row["TOKEN_HASH"] and row["DEVICE_SERIAL"] != serial else None
    record("PAIRED", "TERMINAL", actor=tid, target=tid, old=replaced, new=serial,
           reason="replaced the phone paired before" if replaced else None, ip=ip)
    for other in moved_from:
        record("UNPAIRED", "TERMINAL", actor=tid, target=other, old=serial, reason=f"phone paired to {tid}", ip=ip)
    return token


# Clears the phone and any open code from a terminal: its token stops working at once.
_UNPAIR = ("TOKEN_HASH = NULL, DEVICE_SERIAL = NULL, PAIR_CODE_HASH = NULL, PAIR_EXPIRES_AT = NULL, "
           "PAIR_ATTEMPTS = 0, VERSION = VERSION + 1, UPDATED_AT = CURRENT_TIMESTAMP()")


def unpair_terminal(terminal_id, actor, reason=None, ip=None):
    """Disconnect the phone from the terminal; the terminal stays and waits for a new phone. The phone
    stops at once, so the caller first makes sure it has no bill open. Raises ValueError if there is
    no active terminal by that ID."""
    tid = (terminal_id or "").strip().upper()
    rows = _run(f"SELECT DEVICE_SERIAL FROM {TERMINALS} WHERE TERMINAL_ID = ? AND STATUS = 'ACTIVE'", [tid])
    if not rows:
        raise ValueError(f"No active terminal {tid}")
    _run(f"UPDATE {TERMINALS} SET {_UNPAIR}, UPDATED_BY = ? WHERE TERMINAL_ID = ? AND STATUS = 'ACTIVE'",
         [actor, tid], fetch=False)
    bump_change()
    record("UNPAIRED", "TERMINAL", actor=actor, target=tid, old=rows[0]["DEVICE_SERIAL"], reason=reason, ip=ip)


def retire_terminal(terminal_id, actor, reason=None, ip=None):
    """Retire the terminal for good: its phone stops at once, its number is never used again and it no
    longer counts towards the store's MAX_MPOS. The caller first makes sure no bill is open on it.
    Raises ValueError if there is no active terminal by that ID."""
    tid = (terminal_id or "").strip().upper()
    n = _run(f"UPDATE {TERMINALS} SET STATUS = 'RETIRED', {_UNPAIR}, UPDATED_BY = ? "
             f"WHERE TERMINAL_ID = ? AND STATUS = 'ACTIVE'", [actor, tid], fetch=False)
    if n != 1:
        raise ValueError(f"No active terminal {tid}")
    bump_change()
    record("RETIRED", "TERMINAL", actor=actor, target=tid, old="ACTIVE", new="RETIRED", reason=reason, ip=ip)


# ---------------------------------------------------------------- switches and settings (portal screens)

def _update_one(table, key_col, key, sets, params, *, actor, version, what, active_only=False):
    """UPDATE one row; with `version` only if it still has the VERSION the screen saw. Raises Conflict
    when no row was changed. Bumps CHANGE_SEQ so every running site reloads."""
    sql = (f"UPDATE {table} SET {sets}, VERSION = VERSION + 1, UPDATED_AT = CURRENT_TIMESTAMP(), "
           f"UPDATED_BY = ? WHERE {key_col} = ?")
    params = [*params, actor, key]
    if active_only:
        sql += " AND STATUS = 'ACTIVE'"
    if version is not None:
        sql += " AND VERSION = ?"
        params.append(int(version))
    if _run(sql, params, fetch=False) != 1:
        raise Conflict(f"{what} was changed by someone else since you loaded it - reload and try again")
    bump_change()


def set_global(on, reason, actor, version=None, ip=None):
    """All mPOS on or off (HO). Off: phones with a bill open finish it, then stop."""
    _update_one(GLOBAL, "ID", 1, "ENABLED = ?, REASON = ?", [bool(on), None if on else _cut(reason, 500)],
                actor=actor, version=version, what="All mPOS")
    record("GLOBAL_ON" if on else "GLOBAL_OFF", "GLOBAL", actor=actor, new="ON" if on else "OFF",
           reason=reason, ip=ip)


def set_stores(codes, on, reason, actor, version=None, ip=None):
    """HO's switch for some stores (codes) or every store (codes None), in one statement. Stores that
    never had mPOS have no row and nothing to switch. `version` guards a single store. Returns the
    number of stores changed."""
    sql = (f"UPDATE {STORES} SET HO_ENABLED = ?, HO_REASON = ?, VERSION = VERSION + 1, "
           f"UPDATED_AT = CURRENT_TIMESTAMP(), UPDATED_BY = ?")
    params = [bool(on), None if on else _cut(reason, 500), actor]
    if codes is not None:
        codes = sorted({c.strip().upper() for c in codes})
        if not codes:
            return 0
        sql += f" WHERE STORE_CODE IN ({', '.join('?' * len(codes))})"
        params += codes
        if version is not None and len(codes) == 1:
            sql += " AND VERSION = ?"
            params.append(int(version))
    n = _run(sql, params, fetch=False)
    if version is not None and codes and len(codes) == 1 and n != 1:
        raise Conflict(f"Store {codes[0]} was changed by someone else since you loaded it - reload and try again")
    if n:
        bump_change()
    record("STORE_ON" if on else "STORE_OFF", "STORE", actor=actor,
           target=codes[0] if codes and len(codes) == 1 else None, new="ON" if on else "OFF",
           reason=reason if codes is None or len(codes) == 1 else f"{reason or ''} [{', '.join(codes)[:400]}]".strip(),
           affected=n, ip=ip)
    return n


def set_terminal(terminal_id, on, actor, reason=None, version=None, ip=None):
    """The store approver's switch for one terminal. Off: an open bill finishes first."""
    _update_one(TERMINALS, "TERMINAL_ID", terminal_id, "STORE_ENABLED = ?", [bool(on)],
                actor=actor, version=version, what=terminal_id, active_only=True)
    record("TERMINAL_ON" if on else "TERMINAL_OFF", "TERMINAL", actor=actor, target=terminal_id,
           new="ON" if on else "OFF", reason=reason, ip=ip)


def set_veto(terminal_id, blocked, reason, actor, version=None, ip=None):
    """HO's block on one terminal; the store cannot switch it back on while it holds."""
    _update_one(TERMINALS, "TERMINAL_ID", terminal_id, "HO_VETO = ?, VETO_REASON = ?",
                [bool(blocked), _cut(reason, 500) if blocked else None],
                actor=actor, version=version, what=terminal_id, active_only=True)
    record("VETO_ON" if blocked else "VETO_OFF", "TERMINAL", actor=actor, target=terminal_id,
           new="BLOCKED" if blocked else "LIFTED", reason=reason, ip=ip)


def set_lock(terminal_id, locked, reason, actor, version=None, ip=None):
    """Immediate lock (lost or stolen phone: stops even mid-bill) or its removal, by HO."""
    _update_one(TERMINALS, "TERMINAL_ID", terminal_id, "IMMEDIATE_LOCK = ?, LOCK_REASON = ?",
                [bool(locked), _cut(reason, 500) if locked else None],
                actor=actor, version=version, what=terminal_id, active_only=True)
    record("LOCK_ON" if locked else "LOCK_OFF", "TERMINAL", actor=actor, target=terminal_id,
           new="LOCKED" if locked else "UNLOCKED", reason=reason, ip=ip)


def set_day(store_code, open_, actor, ip=None):
    """Open or close the store's mPOS day - only for stores that use Open day (DAY_CONTROL)."""
    code = store_code.strip().upper()
    opened = ", DAY_OPENED_AT = CURRENT_TIMESTAMP(), DAY_OPENED_BY = ?" if open_ else ""
    n = _run(f"UPDATE {STORES} SET DAY_OPEN = ?{opened}, VERSION = VERSION + 1, UPDATED_AT = CURRENT_TIMESTAMP(), "
             f"UPDATED_BY = ? WHERE STORE_CODE = ? AND DAY_CONTROL",
             [bool(open_), *([actor] if open_ else []), actor, code], fetch=False)
    if n != 1:
        raise Conflict(f"{code} does not use Open day")
    bump_change()
    record("DAY_OPEN" if open_ else "DAY_CLOSE", "STORE", actor=actor, target=code, ip=ip)


STORE_SETTINGS = {  # field -> (column, history event)
    "max_mpos": ("MAX_MPOS", "MAX_SET"),
    "approver": ("APPROVER", "APPROVER_SET"),
    "day_control": ("DAY_CONTROL", "DAY_CONTROL_SET"),
    "day_close_time": ("DAY_CLOSE_TIME", "DAY_CLOSE_SET"),
}


def set_store_settings(store_code, changes, actor, version=None, ip=None):
    """HO's settings for one store: {field: value} with fields from STORE_SETTINGS. Creates the store's
    row first if it never had mPOS."""
    code = store_code.strip().upper()
    changes = {k: v for k, v in changes.items() if k in STORE_SETTINGS}
    if not 0 <= int(changes.get("max_mpos", 0)) <= S.MAX_MPOS:
        raise ValueError(f"A store can have at most {S.MAX_MPOS} mPOS terminals")
    if not changes:
        return
    _run(f"MERGE INTO {STORES} s USING (SELECT ? AS STORE_CODE) n ON s.STORE_CODE = n.STORE_CODE "
         f"WHEN NOT MATCHED THEN INSERT (STORE_CODE, MAX_MPOS, CREATED_BY) VALUES (n.STORE_CODE, ?, ?)",
         [code, S.MAX_MPOS, actor], fetch=False)
    _update_one(STORES, "STORE_CODE", code, ", ".join(f"{STORE_SETTINGS[k][0]} = ?" for k in changes),
                list(changes.values()), actor=actor, version=version, what=f"Store {code}")
    for k, v in changes.items():
        record(STORE_SETTINGS[k][1], "STORE", actor=actor, target=code, new=v, ip=ip)


def add_terminals(store_codes, count, actor, ip=None):
    """`count` new terminals in each store, in one transaction - fast for every store at once. Each
    takes its store's next number; a store stops at its MAX_MPOS. Returns (new terminal IDs, store
    codes that hit their limit)."""
    codes = sorted({c.strip().upper() for c in store_codes if c and c.strip()})
    if not codes or count < 1:
        return [], []
    marks = ", ".join("?" * len(codes))
    added, full, bumps = [], [], []
    with _transaction() as cur:
        cur.execute(f"MERGE INTO {STORES} s USING (SELECT column1 AS STORE_CODE FROM VALUES "
                    f"{', '.join(['(?)'] * len(codes))}) n ON s.STORE_CODE = n.STORE_CODE WHEN NOT MATCHED THEN "
                    f"INSERT (STORE_CODE, MAX_MPOS, CREATED_BY) VALUES (n.STORE_CODE, ?, ?)", [*codes, S.MAX_MPOS, actor])
        cur.execute(f"SELECT s.STORE_CODE, s.NEXT_SEQ, s.MAX_MPOS, COUNT(t.TERMINAL_ID) AS ACTIVE FROM {STORES} s "
                    f"LEFT JOIN {TERMINALS} t ON t.STORE_CODE = s.STORE_CODE AND t.STATUS = 'ACTIVE' "
                    f"WHERE s.STORE_CODE IN ({marks}) GROUP BY s.STORE_CODE, s.NEXT_SEQ, s.MAX_MPOS "
                    f"ORDER BY s.STORE_CODE", codes)
        rows = []
        for r in cur.fetchall():
            seq, room = int(r["NEXT_SEQ"]), min(int(r["MAX_MPOS"]), S.MAX_MPOS) - int(r["ACTIVE"])
            n = max(0, min(count, room))
            if n < count:
                full.append(r["STORE_CODE"])
            for k in range(n):
                tid = S.terminal_id(r["STORE_CODE"], seq + k)
                added.append(tid)
                rows.append((tid, r["STORE_CODE"], seq + k, actor))
            if n:
                bumps.append((r["STORE_CODE"], seq, seq + n))
        if rows:
            cur.executemany(f"INSERT INTO {TERMINALS} (TERMINAL_ID, STORE_CODE, SEQ, CREATED_BY) VALUES (?, ?, ?, ?)",
                            [list(r) for r in rows])
            cur.execute(f"UPDATE {STORES} s SET NEXT_SEQ = n.NEW_SEQ, UPDATED_AT = CURRENT_TIMESTAMP(), UPDATED_BY = ? "
                        f"FROM (SELECT column1 AS STORE_CODE, column2 AS OLD_SEQ, column3 AS NEW_SEQ FROM VALUES "
                        f"{', '.join(['(?, ?, ?)'] * len(bumps))}) n "
                        f"WHERE s.STORE_CODE = n.STORE_CODE AND s.NEXT_SEQ = n.OLD_SEQ",
                        [actor, *(v for b in bumps for v in b)])
            if cur.rowcount != len(bumps):  # another copy of the site added terminals at the same moment
                raise sf.StoreUnavailable("Terminals were added elsewhere at the same moment - try again")
    if added:
        bump_change()
        one = len(codes) == 1
        record("TERMINAL_ADDED", "STORE" if one else "GLOBAL", actor=actor, target=codes[0] if one else None,
               new=", ".join(added)[:200], reason=None if one else f"{count} per store, {len(codes)} stores",
               affected=len(added), ip=ip)
    return added, full


def load_history(store_code=None, terminal_id=None, limit=200):
    """Newest first. A store's history includes its terminals'; no filter = everything (HO)."""
    sql, params = f"SELECT * FROM {HISTORY}", []
    if terminal_id:
        sql += " WHERE TARGET = ?"
        params = [terminal_id.strip().upper()]
    elif store_code:
        code = store_code.strip().upper()
        sql += " WHERE TARGET = ? OR STARTSWITH(TARGET, ?)"
        params = [code, f"{code}_MPOS"]
    sql += " ORDER BY EVENT_ID DESC LIMIT ?"
    params.append(int(limit))
    return [{"id": r["EVENT_ID"], "time": sf._ts(r["EVENT_TIME"]), "actor": r["ACTOR"], "level": r["LEVEL"],
             "target": r["TARGET"], "event": r["EVENT"], "old": r["OLD_VALUE"], "new": r["NEW_VALUE"],
             "reason": r["REASON"], "affected": r["AFFECTED_COUNT"]} for r in _run(sql, params)]


# ---------------------------------------------------------------- phone check-ins

HEARTBEAT_COLUMNS = ("TERMINAL_ID", "STORE_CODE", "SEEN_AT", "BILL_OPEN", "STATE_GIVEN", "APP_VERSION",
                     "SERIAL", "IP_ADDRESS", "NETWORK", "BATTERY_PCT")


def insert_heartbeats(rows):
    """Append check-ins (tuples in HEARTBEAT_COLUMNS order) in ONE statement. Keep a call under about
    6,000 rows: past 65,280 bound values the connector would stage the rows in a temporary file."""
    if rows:
        _run(f"INSERT INTO {HEARTBEATS} ({', '.join(HEARTBEAT_COLUMNS)}) "
             f"VALUES ({', '.join('?' * len(HEARTBEAT_COLUMNS))})", rows, fetch=False, many=True)


def update_last_seen(since):
    """MPOS_TERMINALS.LAST_SEEN_AT = each terminal's latest check-in since `since`. Not a switch change:
    VERSION and CHANGE_SEQ stay as they are, so no running site reloads for it."""
    _run(f"UPDATE {TERMINALS} t SET LAST_SEEN_AT = h.SEEN FROM "
         f"(SELECT TERMINAL_ID, MAX(SEEN_AT) AS SEEN FROM {HEARTBEATS} WHERE SEEN_AT >= ? GROUP BY TERMINAL_ID) h "
         f"WHERE t.TERMINAL_ID = h.TERMINAL_ID AND (t.LAST_SEEN_AT IS NULL OR t.LAST_SEEN_AT < h.SEEN)",
         [since], fetch=False)


# ---------------------------------------------------------------- history

def record(event, level, *, actor, target=None, old=None, new=None, reason=None, affected=1, ip=None):
    """Write one history row. Never raises: falls back to a local file if Snowflake is down."""
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    row = [actor, level, target, event, _cut(old, 200), _cut(new, 200), _cut(reason, 500), int(affected), ip]
    try:
        _run(f"INSERT INTO {HISTORY} (ACTOR, LEVEL, TARGET, EVENT, OLD_VALUE, NEW_VALUE, REASON, "
             f"AFFECTED_COUNT, IP_ADDRESS) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", row, fetch=False)
    except Exception as e:  # noqa: BLE001 - history must never break a switch change or a phone call
        settings.output_dir.mkdir(exist_ok=True)
        with open(settings.output_dir / "device_events_fallback.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"), "event": event,
                                "level": level, "actor": actor, "target": target, "old": old, "new": new,
                                "reason": reason, "affected": affected, "ip": ip, "error": str(e)}) + "\n")


def _cut(value, size):
    return None if value is None else str(value)[:size]
