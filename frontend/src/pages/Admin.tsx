import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import { api, type AuthEvent, type Me, type StoreInfo, type User } from "../lib/api";
import { useAsync } from "../lib/hooks";
import { HO, LocationPicker } from "../components/LocationPicker";
import { fmtQty } from "../lib/format";

type View = "users" | "activity";
type Status = "" | "active" | "inactive" | "must_change";

const EVENT_LABELS: Record<string, string> = {
  LOGIN_SUCCESS: "Logged in",
  LOGIN_FAILED: "Login failed",
  LOGIN_LOCKED: "Login locked",
  LOGOUT: "Logged out",
  PASSWORD_CHANGED: "Changed password",
  PASSWORD_RESET: "Password reset",
  USER_CREATED: "User created",
  USER_UPDATED: "User updated",
  USER_ACTIVATED: "Activated",
  USER_DEACTIVATED: "Deactivated",
};
const WARN_EVENTS = new Set(["LOGIN_FAILED", "LOGIN_LOCKED", "USER_DEACTIVATED"]);

/** Admin tab: User Management (users in Snowflake) and authentication activity. */
export function Admin({ me }: { me: Me }) {
  const [view, setView] = useState<View>("users");
  const users = useAsync((signal) => api.users(signal), []);
  const activity = useAsync((signal) => api.activity(signal), [], view === "activity");
  const [editing, setEditing] = useState<User | "new" | null>(null);
  const [resetting, setResetting] = useState<User | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const storeMaster = useAsync((signal) => api.storeMaster(signal), [], editing !== null);
  const defaults = useAsync((signal) => api.adminDefaults(signal), []);
  const defaultPassword = defaults.data?.default_password ?? "";

  const list = users.data ?? [];
  const active = list.filter((u) => u.is_active).length;
  const roleNames = me.role_names;
  const refresh = () => (view === "users" ? users.reload() : activity.reload());

  const saved = (message: string) => {
    setEditing(null);
    setResetting(null);
    setNotice(message);
    users.reload();
    if (view === "activity") activity.reload();
  };
  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(null), 4000);
    return () => clearTimeout(t);
  }, [notice]);

  const toggleActive = async (u: User) => {
    const verb = u.is_active ? "Deactivate" : "Activate";
    if (!confirm(`${verb} ${u.full_name} (${u.username})?${u.is_active ? " They will be logged out." : ""}`)) return;
    try {
      await api.updateUser(u.username, { is_active: !u.is_active });
      saved(`${u.full_name} ${u.is_active ? "deactivated" : "activated"}`);
    } catch (e) {
      alert((e as Error).message);
    }
  };

  return (
    <div className={users.loading || activity.loading ? "loading" : undefined}>
      <header className="admin-head">
        <div>
          <h1>User Management</h1>
          <div className="sub">
            {fmtQty(list.length)} accounts · {fmtQty(active)} active · {fmtQty(list.length - active)} inactive
            <span className="hint"> · plus the built-in admin · stored in Snowflake</span>
          </div>
        </div>
        <div className="admin-actions">
          <div className="presets" role="group" aria-label="View">
            <button type="button" aria-pressed={view === "users"} onClick={() => setView("users")}>
              Users
            </button>
            <button type="button" aria-pressed={view === "activity"} onClick={() => setView("activity")}>
              Activity
            </button>
          </div>
          <button type="button" onClick={refresh} title="Reload from Snowflake" aria-label="Refresh">
            ↻
          </button>
          <button
            type="button"
            onClick={() => (view === "users" ? exportUsers(list, roleNames) : exportActivity(activity.data ?? []))}
          >
            ↓ CSV
          </button>
          <button type="button" className="primary" onClick={() => setEditing("new")}>
            + New User
          </button>
        </div>
      </header>

      {notice && (
        <div className="notice" role="status">
          {notice}
        </div>
      )}
      {(view === "users" ? users.error : activity.error) && (
        <div className="err" role="alert">
          {view === "users" ? users.error : activity.error}
        </div>
      )}

      {view === "users" ? (
        <UsersTable
          users={list}
          roleNames={roleNames}
          onEdit={setEditing}
          onReset={setResetting}
          onToggleActive={toggleActive}
        />
      ) : (
        <ActivityTable events={activity.data ?? []} />
      )}

      {editing && (
        <UserDialog
          user={editing === "new" ? null : editing}
          roleNames={roleNames}
          stores={storeMaster.data ?? []}
          storesLoading={storeMaster.loading}
          storesError={storeMaster.error}
          defaultPassword={defaultPassword}
          onClose={() => setEditing(null)}
          onSaved={saved}
        />
      )}
      {resetting && (
        <ResetDialog
          user={resetting}
          defaultPassword={defaultPassword}
          onClose={() => setResetting(null)}
          onSaved={saved}
        />
      )}
    </div>
  );
}

// ------------------------------------------------------------------ users

interface UsersTableProps {
  users: User[];
  roleNames: Record<string, string>;
  onEdit: (u: User) => void;
  onReset: (u: User) => void;
  onToggleActive: (u: User) => void;
}

function UsersTable({ users, roleNames, onEdit, onReset, onToggleActive }: UsersTableProps) {
  const [search, setSearch] = useState("");
  const [role, setRole] = useState("");
  const [status, setStatus] = useState<Status>("");
  const [location, setLocation] = useState("");
  const locationOptions = useMemo(
    () => [...new Set(users.flatMap((u) => u.locations))].sort((a, b) => (a === HO ? -1 : b === HO ? 1 : a.localeCompare(b))),
    [users],
  );

  const shown = useMemo(() => {
    const q = search.trim().toLowerCase();
    return users.filter(
      (u) =>
        (!q ||
          u.full_name.toLowerCase().includes(q) ||
          u.username.includes(q) ||
          u.roles.some((r) => r.toLowerCase().includes(q) || (roleNames[r] ?? "").toLowerCase().includes(q)) ||
          u.locations.some((l) => l.toLowerCase().includes(q))) &&
        (!role || u.roles.includes(role)) &&
        (!location || u.locations.includes(location)) &&
        (!status ||
          (status === "active" && u.is_active) ||
          (status === "inactive" && !u.is_active) ||
          (status === "must_change" && u.must_change_password)),
    );
  }, [users, search, role, status, location, roleNames]);

  return (
    <section className="card admin-card">
      <div className="admin-filters">
        <input
          type="search"
          className="admin-search"
          placeholder="Search name, username, role, store…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Search users"
        />
        <select value={role} onChange={(e) => setRole(e.target.value)} aria-label="Filter by role">
          <option value="">All roles</option>
          {Object.entries(roleNames).map(([id, name]) => (
            <option key={id} value={id}>
              {name}
            </option>
          ))}
        </select>
        <select value={location} onChange={(e) => setLocation(e.target.value)} aria-label="Filter by location">
          <option value="">All locations</option>
          {locationOptions.map((l) => (
            <option key={l} value={l}>
              {l === HO ? "HO - all stores" : l}
            </option>
          ))}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value as Status)} aria-label="Filter by status">
          <option value="">Any status</option>
          <option value="active">Active</option>
          <option value="inactive">Inactive</option>
          <option value="must_change">Must change password</option>
        </select>
        <span className="hint admin-count">
          {shown.length} of {users.length} shown
        </span>
      </div>
      <div className="table-scroll">
        <table className="admin-table">
          <thead>
            <tr>
              <th className="num">#</th>
              <th>Full name</th>
              <th>Username</th>
              <th>Roles</th>
              <th>Location</th>
              <th>Status</th>
              <th>Last login</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            <tr className="builtin-row">
              <td className="num muted">–</td>
              <td>
                System Administrator <span className="tag">built-in</span>
              </td>
              <td className="mono">admin</td>
              <td>
                <span className="chip chip-admin">ADMIN</span>
              </td>
              <td>
                <span className="chip chip-admin">HO</span>
              </td>
              <td>
                <StatusDot active />
              </td>
              <td className="muted">—</td>
              <td className="hint">password in server .env</td>
            </tr>
            {shown.map((u, i) => (
              <tr key={u.username} className={u.is_active ? undefined : "inactive-row"}>
                <td className="num muted">{i + 1}</td>
                <td>
                  <span className="strong">{u.full_name}</span>
                  {u.must_change_password && <span className="tag tag-warn">must change pw</span>}
                </td>
                <td className="mono">{u.username}</td>
                <td>
                  <span className="chips">
                    {u.roles.length === 0 && <span className="muted">no access</span>}
                    {u.roles.map((r) => (
                      <span key={r} className="chip" title={roleNames[r]}>
                        {r}
                      </span>
                    ))}
                  </span>
                </td>
                <td>
                  <span className="chips">
                    {u.locations.length === 0 && <span className="tag tag-warn">not set</span>}
                    {u.locations.map((l) => (
                      <span key={l} className={`chip ${l === HO ? "chip-admin" : ""}`}>
                        {l}
                      </span>
                    ))}
                  </span>
                </td>
                <td>
                  <StatusDot active={u.is_active} />
                </td>
                <td className="muted">{u.last_login_at ?? "never"}</td>
                <td>
                  <span className="row-actions">
                    <button type="button" className="ghost" onClick={() => onEdit(u)}>
                      Edit
                    </button>
                    <button type="button" className="ghost" onClick={() => onReset(u)}>
                      Reset password
                    </button>
                    <button type="button" className="ghost" onClick={() => onToggleActive(u)}>
                      {u.is_active ? "Deactivate" : "Activate"}
                    </button>
                  </span>
                </td>
              </tr>
            ))}
            {shown.length === 0 && (
              <tr>
                <td colSpan={8} className="empty">
                  {users.length === 0 ? "No users yet - click + New User to add one." : "No users match these filters."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function StatusDot({ active }: { active: boolean }) {
  return (
    <span className={`status-dot ${active ? "is-active" : "is-inactive"}`}>
      <i aria-hidden="true" />
      {active ? "Active" : "Inactive"}
    </span>
  );
}

// ------------------------------------------------------------------ activity

function ActivityTable({ events }: { events: AuthEvent[] }) {
  const [search, setSearch] = useState("");
  const [event, setEvent] = useState("");
  const shown = useMemo(() => {
    const q = search.trim().toLowerCase();
    return events.filter(
      (e) =>
        (!event || e.event === event) &&
        (!q || [e.username, e.actor, e.detail, e.ip].some((v) => (v ?? "").toLowerCase().includes(q))),
    );
  }, [events, search, event]);

  return (
    <section className="card admin-card">
      <div className="admin-filters">
        <input
          type="search"
          className="admin-search"
          placeholder="Search user, detail, IP…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Search activity"
        />
        <select value={event} onChange={(e) => setEvent(e.target.value)} aria-label="Filter by event">
          <option value="">All events</option>
          {Object.entries(EVENT_LABELS).map(([id, label]) => (
            <option key={id} value={id}>
              {label}
            </option>
          ))}
        </select>
        <span className="hint admin-count">
          {shown.length} of {events.length} events (latest 1,000)
        </span>
      </div>
      <div className="table-scroll activity-scroll">
        <table className="admin-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>User</th>
              <th>Event</th>
              <th>By</th>
              <th>Detail</th>
              <th>IP address</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((e) => (
              <tr key={e.id}>
                <td className="mono">{e.time}</td>
                <td className="mono">{e.username ?? "—"}</td>
                <td>
                  <span className={`chip ${WARN_EVENTS.has(e.event) ? "chip-warn" : ""}`}>
                    {EVENT_LABELS[e.event] ?? e.event}
                  </span>
                </td>
                <td className="mono muted">{e.actor && e.actor !== e.username ? e.actor : ""}</td>
                <td className="detail-cell" title={e.user_agent ?? undefined}>
                  {e.detail ?? ""}
                </td>
                <td className="mono muted">{e.ip ?? ""}</td>
              </tr>
            ))}
            {shown.length === 0 && (
              <tr>
                <td colSpan={6} className="empty">
                  {events.length === 0 ? "No activity recorded yet." : "No events match these filters."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// ------------------------------------------------------------------ dialogs

function Dialog({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  return (
    <dialog ref={ref} className="form-dialog" onClose={onClose} aria-label={title}>
      <div className="dlg-head">
        <strong>{title}</strong>
        <button type="button" className="ghost" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </div>
      {children}
    </dialog>
  );
}

/** Random temporary password: 12 letters/digits, always containing a digit. */
function generatePassword() {
  const chars = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789";
  const bytes = crypto.getRandomValues(new Uint8Array(12));
  const pw = Array.from(bytes, (b) => chars[b % chars.length]).join("");
  return /\d/.test(pw) ? pw : pw.slice(0, 11) + String(2 + (bytes[0] % 8));
}

function PasswordField({ value, onChange, defaultPassword }: { value: string; onChange: (v: string) => void; defaultPassword: string }) {
  return (
    <label>
      Temporary password
      <span className="pw-row">
        <input className="mono" value={value} onChange={(e) => onChange(e.target.value)} autoComplete="off" />
        {defaultPassword && value !== defaultPassword && (
          <button type="button" onClick={() => onChange(defaultPassword)}>
            Default
          </button>
        )}
        <button type="button" onClick={() => onChange(generatePassword())}>
          Generate
        </button>
        <button type="button" disabled={!value} onClick={() => navigator.clipboard?.writeText(value)}>
          Copy
        </button>
      </span>
      <span className="field-hint">
        {defaultPassword && value === defaultPassword
          ? "The default password. Give it to the user; they can change it later from Password in the sidebar."
          : "Give this to the user. At least 8 characters with letters and numbers."}
      </span>
    </label>
  );
}

interface UserDialogProps {
  user: User | null; // null = new user
  roleNames: Record<string, string>;
  stores: StoreInfo[];
  storesLoading: boolean;
  storesError: string | null;
  defaultPassword: string;
  onClose: () => void;
  onSaved: (message: string) => void;
}

function UserDialog({ user, roleNames, stores, storesLoading, storesError, defaultPassword, onClose, onSaved }: UserDialogProps) {
  const [fullName, setFullName] = useState(user?.full_name ?? "");
  const [username, setUsername] = useState(user?.username ?? "");
  const [roles, setRoles] = useState<string[]>(user?.roles ?? Object.keys(roleNames));
  const [locations, setLocations] = useState<string[]>(user?.locations ?? []);
  const [password, setPassword] = useState(user ? "" : defaultPassword || generatePassword());
  const [mustChange, setMustChange] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const toggleRole = (r: string) =>
    setRoles((cur) => (cur.includes(r) ? cur.filter((x) => x !== r) : Object.keys(roleNames).filter((x) => cur.includes(x) || x === r)));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (user) {
        await api.updateUser(user.username, { full_name: fullName, roles, locations });
        onSaved(`${fullName} updated`);
      } else {
        await api.createUser({ username, full_name: fullName, roles, locations, password, must_change_password: mustChange });
        onSaved(`${fullName} (${username.trim().toLowerCase()}) created`);
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog title={user ? `Edit ${user.full_name}` : "New user"} onClose={onClose}>
      <form className="dialog-form" onSubmit={submit}>
        <label>
          Full name
          <input autoFocus value={fullName} onChange={(e) => setFullName(e.target.value)} />
        </label>
        <label>
          Username
          <input
            className="mono"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            disabled={!!user}
            placeholder="e.g. v25151"
            autoComplete="off"
          />
          {!user && <span className="field-hint">Used to log in. Lower-case letters, numbers, dot, dash or underscore.</span>}
        </label>
        <fieldset>
          <legend>Roles - tabs this user can open</legend>
          {Object.entries(roleNames).map(([id, name]) => (
            <label key={id} className="check">
              <input type="checkbox" checked={roles.includes(id)} onChange={() => toggleRole(id)} />
              <span className="chip">{id}</span> {name}
            </label>
          ))}
        </fieldset>
        <LocationPicker
          stores={stores}
          value={locations}
          onChange={setLocations}
          loading={storesLoading}
          error={storesError}
        />
        {!user && (
          <>
            <PasswordField value={password} onChange={setPassword} defaultPassword={defaultPassword} />
            <label className="check">
              <input type="checkbox" checked={mustChange} onChange={(e) => setMustChange(e.target.checked)} />
              Ask the user to change this password at first login (optional)
            </label>
          </>
        )}
        {error && (
          <div className="err" role="alert">
            {error}
          </div>
        )}
        <div className="auth-actions">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button
            type="submit"
            className="primary"
            disabled={busy || !fullName.trim() || locations.length === 0 || (!user && (!username.trim() || !password))}
          >
            {busy ? "Saving…" : user ? "Save changes" : "Create user"}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

interface ResetDialogProps {
  user: User;
  defaultPassword: string;
  onClose: () => void;
  onSaved: (m: string) => void;
}

function ResetDialog({ user, defaultPassword, onClose, onSaved }: ResetDialogProps) {
  const [password, setPassword] = useState(defaultPassword || generatePassword());
  const [mustChange, setMustChange] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.resetPassword(user.username, password, mustChange);
      onSaved(`Password reset for ${user.full_name}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog title={`Reset password - ${user.full_name}`} onClose={onClose}>
      <form className="dialog-form" onSubmit={submit}>
        <PasswordField value={password} onChange={setPassword} defaultPassword={defaultPassword} />
        <label className="check">
          <input type="checkbox" checked={mustChange} onChange={(e) => setMustChange(e.target.checked)} />
          Ask the user to change this password at next login (optional)
        </label>
        {error && (
          <div className="err" role="alert">
            {error}
          </div>
        )}
        <div className="auth-actions">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="primary" disabled={busy || !password}>
            {busy ? "Saving…" : "Reset password"}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

// ------------------------------------------------------------------ CSV

function saveCsv(name: string, header: string[], rows: (string | number | boolean | null)[][]) {
  const cell = (v: unknown) => {
    const s = v == null ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const csv = [header, ...rows].map((r) => r.map(cell).join(",")).join("\r\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

const today = () => new Date().toISOString().slice(0, 10);

function exportUsers(users: User[], roleNames: Record<string, string>) {
  saveCsv(
    `mpos_users_${today()}.csv`,
    ["username", "full_name", "roles", "role_names", "locations", "active", "must_change_password", "last_login_at", "created_at", "created_by", "updated_at", "updated_by"],
    users.map((u) => [
      u.username, u.full_name, u.roles.join(" "), u.roles.map((r) => roleNames[r] ?? r).join("; "), u.locations.join(" "),
      u.is_active, u.must_change_password, u.last_login_at, u.created_at, u.created_by, u.updated_at, u.updated_by,
    ]),
  );
}

function exportActivity(events: AuthEvent[]) {
  saveCsv(
    `mpos_auth_history_${today()}.csv`,
    ["time", "username", "event", "actor", "detail", "ip", "user_agent"],
    events.map((e) => [e.time, e.username, e.event, e.actor, e.detail, e.ip, e.user_agent]),
  );
}
