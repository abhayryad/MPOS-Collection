"""Site users and authentication history in Snowflake (tables from sql/snowflake/auth_tables.sql).

One shared connection, reopened when it drops. History writes never block a login: if
Snowflake cannot be reached the event is appended to output/auth_events_fallback.jsonl.
"""
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import snowflake.connector

from ..config import settings
from . import locations as L
from . import roles as R

snowflake.connector.paramstyle = "qmark"

_lock = threading.Lock()
_conn = None
_tables_ready = False


def _table(name):
    return f"{settings.sf_database}.{settings.sf_schema}.{name}"


USERS = _table("MPOS_USERS")
HISTORY = _table("MPOS_AUTH_HISTORY")


class StoreUnavailable(RuntimeError):
    """Snowflake could not be reached or is not configured."""


def _credentials():
    """Key-pair auth when SNOWFLAKE_PRIVATE_KEY_PATH is set, else password."""
    if settings.sf_private_key_path:
        path = Path(settings.sf_private_key_path).expanduser()
        if not path.is_file():
            raise StoreUnavailable(f"Snowflake private key file not found: {path}")
        creds = {"private_key_file": str(path)}
        # A passphrase is only valid for an encrypted key ("BEGIN ENCRYPTED PRIVATE KEY");
        # the connector rejects one for an unencrypted key, so it is ignored there.
        encrypted = "ENCRYPTED" in path.read_text(errors="replace").splitlines()[0]
        if encrypted:
            if not settings.sf_private_key_passphrase:
                raise StoreUnavailable("The Snowflake private key is encrypted - set SNOWFLAKE_PRIVATE_KEY_PASSPHRASE in .env")
            creds["private_key_file_pwd"] = settings.sf_private_key_passphrase
        return creds
    if settings.sf_password:
        return {"password": settings.sf_password}
    raise StoreUnavailable("Snowflake is not configured - set SNOWFLAKE_PRIVATE_KEY_PATH "
                           "(or SNOWFLAKE_PASSWORD) in .env")


def _connect():
    if not (settings.sf_account and settings.sf_user):
        raise StoreUnavailable("Snowflake is not configured - set SNOWFLAKE_ACCOUNT and SNOWFLAKE_USER in .env")
    creds = _credentials()
    try:
        return snowflake.connector.connect(
            account=settings.sf_account, user=settings.sf_user, **creds,
            role=settings.sf_role or None, warehouse=settings.sf_warehouse or None,
            database=settings.sf_database, schema=settings.sf_schema,
            login_timeout=20, network_timeout=30,
            session_parameters={"TIMEZONE": "Asia/Kolkata", "QUERY_TAG": "mpos-collection-auth"},
        )
    except snowflake.connector.errors.Error as e:
        raise StoreUnavailable(f"Could not connect to Snowflake: {e.msg if hasattr(e, 'msg') else e}")
    except (TypeError, ValueError) as e:  # unreadable / wrong-passphrase private key
        raise StoreUnavailable(f"Could not read the Snowflake private key: {e}")


# Columns added after the tables were first created: {table: {column: definition}}.
# Snowflake errors on ADD COLUMN for an existing column, so they are added only when missing.
ADDED_COLUMNS = {
    "MPOS_USERS": {"LOCATIONS": "VARCHAR(4000) NOT NULL DEFAULT ''",
                   "IS_ADMIN": "BOOLEAN NOT NULL DEFAULT FALSE"},
}


def _ensure_tables(conn):
    """Create the tables (IF NOT EXISTS) and add any missing ADDED_COLUMNS, once per process."""
    global _tables_ready
    if _tables_ready:
        return
    try:
        ddl = (settings.sql_dir / "snowflake" / "auth_tables.sql").read_text(encoding="utf-8")
        body = "\n".join(line for line in ddl.splitlines() if not line.strip().startswith("--"))
        for stmt in (s.strip() for s in body.split(";")):
            if stmt:
                conn.cursor().execute(stmt)
        cur = conn.cursor()
        for table, columns in ADDED_COLUMNS.items():
            cur.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?",
                        [settings.sf_schema.upper(), table])
            have = {r[0] for r in cur.fetchall()}
            for column, definition in columns.items():
                if column not in have:
                    cur.execute(f"ALTER TABLE {_table(table)} ADD COLUMN {column} {definition}")
    except snowflake.connector.errors.Error as e:
        raise StoreUnavailable(f"Could not prepare the Snowflake tables: {getattr(e, 'msg', e)}")
    _tables_ready = True


def _run(sql, params=(), fetch=True):
    """Run one statement; rows as dicts when fetch. Reconnects once if the connection dropped."""
    global _conn
    with _lock:
        for attempt in (1, 2):
            try:
                if _conn is None or _conn.is_closed():
                    _conn = _connect()
                _ensure_tables(_conn)
                cur = _conn.cursor(snowflake.connector.DictCursor)
                cur.execute(sql, list(params))
                return cur.fetchall() if fetch else cur.rowcount
            except snowflake.connector.errors.ProgrammingError:
                raise  # bad SQL / constraint - not a connection problem
            except (snowflake.connector.errors.Error, OSError) as e:
                _conn = None
                if attempt == 2:
                    raise StoreUnavailable(f"Snowflake error: {e}")


# ---------------------------------------------------------------- users

@dataclass
class User:
    username: str
    full_name: str
    roles: list = field(default_factory=list)
    locations: list = field(default_factory=list)  # ['HO'] = all stores, else store codes
    is_admin: bool = False
    is_builtin: bool = False  # the .env admin account, not stored in MPOS_USERS
    is_active: bool = True
    must_change_password: bool = False
    created_at: str | None = None
    created_by: str | None = None
    updated_at: str | None = None
    updated_by: str | None = None
    last_login_at: str | None = None

    def can(self, role):
        return self.is_admin or role in self.roles

    def public(self):
        return {
            "username": self.username, "full_name": self.full_name, "roles": self.roles,
            "locations": self.locations, "is_admin": self.is_admin, "is_builtin": self.is_builtin,
            "is_active": self.is_active,
            "must_change_password": self.must_change_password,
            "created_at": self.created_at, "created_by": self.created_by,
            "updated_at": self.updated_at, "updated_by": self.updated_by,
            "last_login_at": self.last_login_at,
        }


def admin_user():
    return User(username=settings.admin_username, full_name="System Administrator",
                roles=list(R.ROLES) + [R.ADMIN], locations=[L.HO], is_admin=True, is_builtin=True)


def _ts(v):
    return v.strftime("%Y-%m-%d %H:%M:%S") if isinstance(v, datetime) else v


def _user(row):
    return User(
        username=row["USERNAME"], full_name=row["FULL_NAME"],
        roles=R.clean((row["ROLES"] or "").split(",")),
        locations=L.parse(row.get("LOCATIONS")), is_admin=bool(row.get("IS_ADMIN")),
        is_active=bool(row["IS_ACTIVE"]), must_change_password=bool(row["MUST_CHANGE_PASSWORD"]),
        created_at=_ts(row["CREATED_AT"]), created_by=row["CREATED_BY"],
        updated_at=_ts(row["UPDATED_AT"]), updated_by=row["UPDATED_BY"],
        last_login_at=_ts(row["LAST_LOGIN_AT"]),
    )


def get_user(username):
    """(User, password_hash) or (None, None)."""
    rows = _run(f"SELECT * FROM {USERS} WHERE USERNAME = ?", [username.lower()])
    return (_user(rows[0]), rows[0]["PASSWORD_HASH"]) if rows else (None, None)


def list_users():
    return [_user(r) for r in _run(f"SELECT * FROM {USERS} ORDER BY CREATED_AT, USERNAME")]


def create_user(username, full_name, roles, locations, password_hash, must_change, actor, is_admin=False):
    _run(f"INSERT INTO {USERS} (USERNAME, FULL_NAME, ROLES, LOCATIONS, IS_ADMIN, PASSWORD_HASH, "
         f"MUST_CHANGE_PASSWORD, IS_ACTIVE, CREATED_BY) VALUES (?, ?, ?, ?, ?, ?, ?, TRUE, ?)",
         [username.lower(), full_name, ",".join(roles), ",".join(locations), is_admin, password_hash, must_change,
          actor], fetch=False)


def update_user(username, actor, *, full_name=None, roles=None, locations=None, is_active=None, is_admin=None):
    sets, params = [], []
    if full_name is not None:
        sets.append("FULL_NAME = ?"); params.append(full_name)
    if roles is not None:
        sets.append("ROLES = ?"); params.append(",".join(roles))
    if locations is not None:
        sets.append("LOCATIONS = ?"); params.append(",".join(locations))
    if is_active is not None:
        sets.append("IS_ACTIVE = ?"); params.append(is_active)
    if is_admin is not None:
        sets.append("IS_ADMIN = ?"); params.append(is_admin)
    if not sets:
        return 0
    sets += ["UPDATED_AT = CURRENT_TIMESTAMP()", "UPDATED_BY = ?"]
    return _run(f"UPDATE {USERS} SET {', '.join(sets)} WHERE USERNAME = ?",
                params + [actor, username.lower()], fetch=False)


def set_password(username, password_hash, must_change, actor):
    return _run(f"UPDATE {USERS} SET PASSWORD_HASH = ?, MUST_CHANGE_PASSWORD = ?, "
                f"UPDATED_AT = CURRENT_TIMESTAMP(), UPDATED_BY = ? WHERE USERNAME = ?",
                [password_hash, must_change, actor, username.lower()], fetch=False)


def mark_login(username):
    _run(f"UPDATE {USERS} SET LAST_LOGIN_AT = CURRENT_TIMESTAMP() WHERE USERNAME = ?",
         [username.lower()], fetch=False)


# ---------------------------------------------------------------- history

def record(event, username, *, actor=None, detail=None, ip=None, user_agent=None):
    """Write one history row. Never raises: falls back to a local file if Snowflake is down."""
    row = [username, actor or username, event, (detail or "")[:1000] or None, ip, (user_agent or "")[:500] or None]
    try:
        _run(f"INSERT INTO {HISTORY} (USERNAME, ACTOR, EVENT, DETAIL, IP_ADDRESS, USER_AGENT) "
             f"VALUES (?, ?, ?, ?, ?, ?)", row, fetch=False)
    except Exception as e:  # noqa: BLE001 - history must never break a login
        settings.output_dir.mkdir(exist_ok=True)
        with open(settings.output_dir / "auth_events_fallback.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"), "event": event,
                                "username": username, "actor": actor or username, "detail": detail,
                                "ip": ip, "user_agent": user_agent, "error": str(e)}) + "\n")


def recent_failures(username):
    """Failed logins for this username within the lockout window, since its last success."""
    rows = _run(
        f"SELECT COUNT(*) AS N FROM {HISTORY} WHERE USERNAME = ? AND EVENT = 'LOGIN_FAILED' "
        f"AND EVENT_TIME > DATEADD(minute, ?, CURRENT_TIMESTAMP()) "
        f"AND EVENT_TIME > COALESCE((SELECT MAX(EVENT_TIME) FROM {HISTORY} "
        f"WHERE USERNAME = ? AND EVENT = 'LOGIN_SUCCESS'), '1970-01-01'::TIMESTAMP_NTZ)",
        [username.lower(), -settings.lockout_minutes, username.lower()])
    return int(rows[0]["N"])


def list_events(limit=500, username=None):
    where, params = "", []
    if username:
        where, params = "WHERE USERNAME = ? OR ACTOR = ?", [username.lower(), username.lower()]
    rows = _run(f"SELECT EVENT_ID, EVENT_TIME, USERNAME, ACTOR, EVENT, DETAIL, IP_ADDRESS, USER_AGENT "
                f"FROM {HISTORY} {where} ORDER BY EVENT_TIME DESC, EVENT_ID DESC LIMIT {int(limit)}", params)
    return [{"id": r["EVENT_ID"], "time": _ts(r["EVENT_TIME"]), "username": r["USERNAME"], "actor": r["ACTOR"],
             "event": r["EVENT"], "detail": r["DETAIL"], "ip": r["IP_ADDRESS"], "user_agent": r["USER_AGENT"]}
            for r in rows]
