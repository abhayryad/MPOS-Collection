import { useEffect, useRef, useState } from "react";
import { api, ApiError, DEVICE_STORE_KEY, type DeviceEvent, type PairCode, type StoreDevices as Store, type StoreSettings, type Terminal } from "../../lib/api";
import { useAsync } from "../../lib/hooks";
import { StoreSearch } from "../../components/StoreSearch";

const REFRESH_MS = 15_000;

/** mPOS Devices > Store Devices: one store's mPOS terminals - who may bill, which phone is on each,
 *  pairing codes, and the store approver's on/off switch. HO users can pick any store. */
export function StoreDevices() {
  const overview = useAsync((signal) => api.devicesOverview(signal), []);
  const stores = overview.data?.stores ?? [];
  const [picked, setPicked] = useState(() => {
    try {
      const code = sessionStorage.getItem(DEVICE_STORE_KEY) ?? ""; // set by mPOS Control's "Open"
      sessionStorage.removeItem(DEVICE_STORE_KEY);
      return code;
    } catch {
      return "";
    }
  });
  const isHo = !!overview.data?.is_ho;
  const store = picked || stores.find((s) => s.terminals > 0)?.code || stores[0]?.code || "";

  const detail = useAsync((signal) => api.storeDevices(store, signal), [store], !!store);
  const history = useAsync((signal) => api.deviceHistory(store, signal), [store], !!store);
  useEffect(() => {
    if (!store) return;
    const t = setInterval(detail.reload, REFRESH_MS); // phones change state on their own
    return () => clearInterval(t);
  }, [store, detail.reload]);

  const [notice, setNotice] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [pairing, setPairing] = useState<PairCode | null>(null);
  const [confirm, setConfirm] = useState<Confirm | null>(null);

  const act = async (key: string, run: () => Promise<unknown>, done: string) => {
    setBusy(key);
    setFailure(null);
    setNotice(null);
    try {
      await run();
      setNotice(done);
      detail.reload();
      history.reload();
    } catch (e) {
      setFailure((e as ApiError).message);
    } finally {
      setBusy(null);
    }
  };

  const s = detail.data;
  const can = !!s?.can_change;
  const name = (t: Terminal) => t.terminal_id.split("_").pop();

  const askSwitch = (t: Terminal) => {
    if (t.store_enabled) {
      setConfirm({
        title: `Switch ${name(t)} off?`,
        body: t.bill_open
          ? `A bill is open on ${t.terminal_id}. The cashier finishes that bill, then the phone stops. No new bill can start.`
          : `${t.terminal_id} stops billing within 30 seconds, until you switch it on again.`,
        reasonLabel: "Reason (optional, kept in history)",
        action: "Switch off",
        danger: true,
        run: (reason) => act(t.terminal_id, () => api.switchTerminal(t.terminal_id, false, reason, t.version), `${t.terminal_id} switched off`),
      });
    } else {
      act(t.terminal_id, () => api.switchTerminal(t.terminal_id, true, "", t.version), `${t.terminal_id} switched on`);
    }
  };
  const askPair = (t: Terminal) => {
    const go = () =>
      act(t.terminal_id, async () => setPairing(await api.pairCode(t.terminal_id)), `Pairing code issued for ${t.terminal_id}`);
    if (!t.paired) return go();
    setConfirm({
      title: `Pair a new phone to ${name(t)}?`,
      body: `The phone on ${t.terminal_id} now (${t.phone ?? "unknown"}) keeps billing until the new phone scans the code. Then the old phone stops.`,
      action: "Get pairing code",
      run: go,
    });
  };
  // HO only: block (veto), immediate lock, retire
  const askBlock = (t: Terminal, kind: "veto" | "lock") => {
    const on = kind === "veto" ? t.veto : t.lock;
    const call = kind === "veto" ? api.veto : api.lock;
    if (on)
      return act(t.terminal_id, () => call(t.terminal_id, false, "", t.version), `${kind === "veto" ? "Block" : "Lock"} lifted on ${t.terminal_id}`);
    setConfirm(
      kind === "veto"
        ? {
            title: `Block ${name(t)}?`,
            body: `HO's block: ${t.terminal_id} stops after any open bill, and the store cannot switch it back on until HO lifts the block.`,
            reasonLabel: "Reason - shown on the phone",
            reasonRequired: true,
            action: "Block",
            danger: true,
            run: (reason) => act(t.terminal_id, () => api.veto(t.terminal_id, true, reason, t.version), `${t.terminal_id} blocked`),
          }
        : {
            title: `Lock ${name(t)} now?`,
            body: `For a lost or stolen phone. ${t.terminal_id} stops at once${t.bill_open ? " - even the bill open on it now" : ""}. It stays locked until HO unlocks it, even if a new phone is paired.`,
            reasonLabel: "Reason - shown on the phone",
            reasonRequired: true,
            action: "Lock now",
            danger: true,
            run: (reason) => act(t.terminal_id, () => api.lock(t.terminal_id, true, reason, t.version), `${t.terminal_id} locked`),
          },
    );
  };
  const askRetire = (t: Terminal) =>
    setConfirm({
      title: `Retire ${t.terminal_id}?`,
      body: t.bill_open
        ? `A bill is open on ${t.terminal_id} - you can retire it once it is finished.`
        : `For good: its phone stops, the number ${name(t)} is never used again, and the store gets a free slot for a new terminal.`,
      reasonLabel: "Reason (optional, kept in history)",
      action: "Retire",
      danger: true,
      disabled: t.bill_open,
      run: (reason) => act(t.terminal_id, () => api.retire(t.terminal_id, reason), `${t.terminal_id} retired`),
    });

  const askUnpair = (t: Terminal) =>
    setConfirm({
      title: `Unpair the phone from ${name(t)}?`,
      body: t.bill_open
        ? `A bill is open on ${t.terminal_id} - you can unpair once it is finished.`
        : `The phone ${t.phone ?? ""} stops at once and ${t.terminal_id} waits for a new phone.`,
      reasonLabel: "Reason (optional, kept in history)",
      action: "Unpair",
      danger: true,
      disabled: t.bill_open,
      run: (reason) => act(t.terminal_id, () => api.unpair(t.terminal_id, reason), `Phone unpaired from ${t.terminal_id}`),
    });

  const error = overview.error
    ? `Could not load mPOS devices: ${overview.error}`
    : overview.data && !stores.length
      ? "No store is assigned to your account - ask your admin to set your location"
      : detail.error
        ? `Could not load ${store}: ${detail.error}`
        : null;

  return (
    <div className={detail.loading && !s ? "loading" : undefined}>
      <header className="page-head admin-head">
        <div>
          <div className="crumb">mPOS Devices ›</div>
          <h1>Store Devices</h1>
          <div className="sub">
            {s ? `${s.name || s.code} · ${s.code}` : "Loading…"} · refreshes every {REFRESH_MS / 1000} s
          </div>
        </div>
        <div className="admin-actions">
          {stores.length > 1 && (
            <StoreSearch stores={stores.map((x) => x.code)} value={store} onChange={setPicked} allLabel={null} />
          )}
          <button type="button" onClick={() => (detail.reload(), history.reload())} disabled={detail.loading}>
            ↻ Refresh
          </button>
        </div>
      </header>

      {error && <div className="err" role="alert">{error}</div>}
      {failure && <div className="err" role="alert">{failure}</div>}
      {notice && <div className="notice" role="status">{notice}</div>}

      {s && (
        <>
          {!s.global.enabled && (
            <div className="notice dev-notice-off">
              <b>All mPOS are switched off by HO</b>
              {s.global.reason && ` - ${s.global.reason}`}. Open bills finish; no new bill can start anywhere.
            </div>
          )}
          {!s.ho_enabled && (
            <div className="notice dev-notice-off">
              <b>HO has switched this store off</b>
              {s.ho_reason && ` - ${s.ho_reason}`}. Your switches below apply again once HO switches it back on.
            </div>
          )}
          {!can && (
            <div className="notice">
              You can see this store's devices. Only its mPOS approver <b>{s.approver}</b> can change them.
            </div>
          )}

          <div className="tiles">
            <Tile label="Billing allowed" value={s.on} tone="on" />
            <Tile label="Finishing last bill" value={s.draining} tone="drain" />
            <Tile label="Off" value={s.off} tone="off" />
            <Tile label="Waiting for a phone" value={s.not_paired} />
            <Tile label="Bills open now" value={s.bills_open} note={`${s.terminals} of ${s.max_mpos} terminals`} />
          </div>

          {s.day_control && (
            <section className="card dev-day">
              <div>
                <h2>{s.day_open ? "Day is open" : "Day not opened"}</h2>
                <div className="hint">
                  {s.day_open
                    ? `mPOS can bill today${s.day_close_time ? ` - closes by itself at ${s.day_close_time}` : ""}.`
                    : "No mPOS in this store can bill until the day is opened."}
                </div>
              </div>
              <button
                type="button"
                className={s.day_open ? undefined : "primary"}
                disabled={!can || busy === "day"}
                onClick={() =>
                  act("day", () => api.storeDay(s.code, !s.day_open), s.day_open ? "Day closed" : "Day opened")
                }
              >
                {s.day_open ? "Close day" : "Open day"}
              </button>
            </section>
          )}

          <section className="card admin-card">
            <div className="admin-filters">
              <h2>Terminals</h2>
              <span className="hint admin-count">
                Only HO adds or retires terminals · a store can have {s.max_mpos}
              </span>
            </div>
            {s.terminal_list.length === 0 ? (
              <div className="empty">No mPOS terminals in this store yet - ask HO to add one.</div>
            ) : (
              <div className="table-scroll">
                <table className="admin-table dev-table">
                  <thead>
                    <tr>
                      <th>Terminal</th>
                      <th>Status</th>
                      <th>Phone</th>
                      <th>Last check-in</th>
                      <th>Store switch</th>
                      <th aria-label="Actions" />
                    </tr>
                  </thead>
                  <tbody>
                    {s.terminal_list.map((t) => (
                      <tr key={t.terminal_id}>
                        <td>
                          <div className="strong">{name(t)}</div>
                          <div className="hint">{t.terminal_id}</div>
                        </td>
                        <td className="dev-status-cell">
                          <Status t={t} />
                        </td>
                        <td>
                          {t.paired ? (
                            <>
                              <div className="dev-mono">{t.phone ?? "—"}</div>
                              <div className="hint">{t.app_version ? `App ${t.app_version}` : ""}</div>
                            </>
                          ) : (
                            <span className="muted">No phone</span>
                          )}
                        </td>
                        <td>{t.paired ? <Ago iso={t.last_seen} /> : <span className="muted">—</span>}</td>
                        <td>
                          <Switch
                            on={t.store_enabled}
                            disabled={!can || busy === t.terminal_id}
                            label={`Store switch for ${t.terminal_id}`}
                            onClick={() => askSwitch(t)}
                          />
                        </td>
                        <td>
                          <span className="row-actions">
                            <button type="button" disabled={!can || busy === t.terminal_id} onClick={() => askPair(t)}>
                              {t.paired ? "Pair new phone" : "Pair phone"}
                            </button>
                            {t.paired && (
                              <button type="button" disabled={!can || busy === t.terminal_id} onClick={() => askUnpair(t)}>
                                Unpair
                              </button>
                            )}
                          </span>
                          {isHo && (
                            <span className="row-actions dev-ho-actions">
                              <span className="hint">HO:</span>
                              <button type="button" disabled={busy === t.terminal_id} onClick={() => askBlock(t, "veto")}>
                                {t.veto ? "Lift block" : "Block"}
                              </button>
                              <button type="button" className={t.lock ? undefined : "dev-danger-text"} disabled={busy === t.terminal_id} onClick={() => askBlock(t, "lock")}>
                                {t.lock ? "Unlock" : "Lock now"}
                              </button>
                              <button type="button" disabled={busy === t.terminal_id} onClick={() => askRetire(t)}>
                                Retire
                              </button>
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {isHo && <HoSettings store={s} onSave={(change) => act("settings", () => api.storeSettings(s.code, change, s.version), `${s.code} settings saved`)} />}

          <History events={history.data} error={history.error} />
        </>
      )}

      {pairing && <PairDialog code={pairing} onClose={() => setPairing(null)} />}
      {confirm && (
        <ConfirmDialog
          {...confirm}
          onCancel={() => setConfirm(null)}
          onConfirm={(reason) => {
            setConfirm(null);
            confirm.run(reason);
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- pieces

const STATUS: Record<string, { label: string; tone: string }> = {
  ON: { label: "Billing allowed", tone: "on" },
  DRAINING: { label: "Finishing last bill", tone: "drain" },
  OFF: { label: "Off", tone: "off" },
};

function Status({ t }: { t: Terminal }) {
  const look = !t.paired ? { label: "Waiting for a phone", tone: "none" } : STATUS[t.state];
  return (
    <>
      <span className={`dev-pill dev-${look.tone}`}>
        <i aria-hidden="true" />
        {look.label}
        {t.bill_open && <b className="dev-bill">BILL OPEN</b>}
      </span>
      {t.paired && t.state !== "ON" && <div className="hint dev-why">{t.message}</div>}
    </>
  );
}

export function Tile({ label, value, tone, note }: { label: string; value: number; tone?: string; note?: string }) {
  return (
    <div className="tile">
      <div className="tile-label">
        {tone && <i className={`dev-dot dev-${tone}`} aria-hidden="true" />}
        {label}
      </div>
      <div className="tile-value">{value}</div>
      {note && <div className="tile-note">{note}</div>}
    </div>
  );
}

export function Switch({ on, disabled, label, onClick }: { on: boolean; disabled: boolean; label: string; onClick: () => void }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} className="dev-switch" disabled={disabled} onClick={onClick}>
      <span className="dev-knob" aria-hidden="true" />
      <span className="dev-switch-text">{on ? "On" : "Off"}</span>
    </button>
  );
}

export function Ago({ iso }: { iso: string | null }) {
  if (!iso) return <span className="muted">Never</span>;
  const secs = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  const text = secs < 60 ? `${secs} s ago` : secs < 3600 ? `${Math.round(secs / 60)} min ago` : new Date(iso).toLocaleString();
  return <span className={secs > 120 ? "dev-stale" : undefined} title={new Date(iso).toLocaleString()}>{text}</span>;
}

export type Confirm = {
  title: string;
  body: string;
  action: string;
  reasonLabel?: string;
  reasonRequired?: boolean; // the reason is shown on the phones, so it must be given
  danger?: boolean;
  disabled?: boolean;
  run: (reason: string) => void;
};

export function ConfirmDialog({ title, body, action, reasonLabel, reasonRequired, danger, disabled, onCancel, onConfirm }: Confirm & {
  onCancel: () => void;
  onConfirm: (reason: string) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [reason, setReason] = useState("");
  const [missing, setMissing] = useState(false);
  useEffect(() => ref.current?.showModal(), []);
  return (
    <dialog ref={ref} className="form-dialog" onCancel={onCancel} aria-label={title}>
      <form
        className="dialog-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (reasonRequired && !reason.trim()) return setMissing(true);
          onConfirm(reason.trim());
        }}
      >
        <h2>{title}</h2>
        <p className="dev-dialog-body">{body}</p>
        {reasonLabel && !disabled && (
          <label>
            {reasonLabel}
            <input
              value={reason}
              maxLength={500}
              onChange={(e) => (setReason(e.target.value), setMissing(false))}
              autoFocus
              aria-invalid={missing}
            />
            {missing && <span className="field-error">Give a reason - the cashier sees it on the phone</span>}
          </label>
        )}
        <div className="auth-actions">
          <button type="button" onClick={onCancel}>
            Cancel
          </button>
          <button type="submit" className={danger ? "dev-danger" : "primary"} disabled={disabled}>
            {action}
          </button>
        </div>
      </form>
    </dialog>
  );
}

export function PairDialog({ code, onClose }: { code: PairCode; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [until] = useState(() => Date.now() + code.valid_minutes * 60_000);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    ref.current?.showModal();
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const left = Math.max(0, Math.round((until - now) / 1000));
  return (
    <dialog ref={ref} className="form-dialog" onCancel={onClose} aria-label={`Pairing code for ${code.terminal_id}`}>
      <div className="dialog-form dev-pair">
        <h2>Pair a phone to {code.terminal_id}</h2>
        <ol className="dev-steps">
          <li>Open the mPOS app on the phone and choose <b>Set up this mPOS</b>.</li>
          <li>
            Enter terminal <b className="dev-mono">{code.terminal_id}</b> and this code:
          </li>
        </ol>
        <div className={`dev-code${left === 0 ? " dev-expired" : ""}`} aria-live="polite">
          {code.code}
        </div>
        <div className="hint dev-center">
          {left > 0
            ? `Valid for ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")} · works once · 5 wrong tries cancel it`
            : "This code has expired - close and get a new one"}
        </div>
        <div className="hint dev-center">Server address for the app: {code.qr.url}</div>
        <div className="auth-actions">
          <button type="button" className="primary" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </dialog>
  );
}

const EVENT_TEXT: Record<string, string> = {
  TERMINAL_ON: "Switched on", TERMINAL_OFF: "Switched off", PAIR_CODE_ISSUED: "Pairing code issued",
  PAIR_FAILED: "Wrong pairing code tried", PAIRED: "Phone paired", UNPAIRED: "Phone unpaired",
  DRAIN_DONE: "Last bill finished - now off", TERMINAL_ADDED: "Terminal added", RETIRED: "Terminal retired",
  VETO_ON: "Blocked by HO", VETO_OFF: "HO block lifted", LOCK_ON: "Locked by HO", LOCK_OFF: "Lock lifted by HO",
  STORE_ON: "Store switched on by HO", STORE_OFF: "Store switched off by HO", DAY_OPEN: "Day opened",
  DAY_CLOSE: "Day closed", APPROVER_SET: "Approver set", MAX_SET: "Terminal limit changed",
  DAY_CONTROL_SET: "Open day setting changed", DAY_CLOSE_SET: "Day close time changed",
};

/** HO's settings for the store: who approves, how many terminals, and Open day. */
function HoSettings({ store, onSave }: { store: Store; onSave: (change: StoreSettings) => void }) {
  const [approver, setApprover] = useState(store.approver ?? "");
  const [max, setMax] = useState(store.max_mpos);
  const [dayControl, setDayControl] = useState(store.day_control);
  const [close, setClose] = useState(store.day_close_time ?? "");
  useEffect(() => {
    setApprover(store.approver ?? "");
    setMax(store.max_mpos);
    setDayControl(store.day_control);
    setClose(store.day_close_time ?? "");
  }, [store.code, store.version]); // eslint-disable-line react-hooks/exhaustive-deps

  const change: StoreSettings = {};
  if (approver.trim().toLowerCase() !== (store.approver ?? "")) change.approver = approver.trim();
  if (max !== store.max_mpos) change.max_mpos = max;
  if (dayControl !== store.day_control) change.day_control = dayControl;
  if (close !== (store.day_close_time ?? "")) change.day_close_time = close;
  const dirty = Object.keys(change).length > 0;

  return (
    <section className="card dev-settings">
      <div className="card-head">
        <div>
          <h2>Store settings (HO)</h2>
          <div className="hint">Only HO sees and changes these</div>
        </div>
        <button type="button" className="primary" disabled={!dirty} onClick={() => onSave(change)}>
          Save
        </button>
      </div>
      <div className="dev-settings-grid">
        <label>
          mPOS approver
          <input value={approver} placeholder="Anyone at the store" onChange={(e) => setApprover(e.target.value)} />
          <span className="field-hint">Login name. Empty = any store user with the role</span>
        </label>
        <label>
          Terminal limit
          <input type="number" min={0} max={20} value={max} onChange={(e) => setMax(Math.max(0, Math.min(20, +e.target.value || 0)))} />
          <span className="field-hint">Active terminals allowed (default 5)</span>
        </label>
        <label className="check dev-check">
          <input type="checkbox" checked={dayControl} onChange={(e) => setDayControl(e.target.checked)} />
          Uses Open day
        </label>
        <label>
          Day closes at
          <input type="time" value={close} disabled={!dayControl} onChange={(e) => setClose(e.target.value)} />
          <span className="field-hint">Empty = closed by hand only</span>
        </label>
      </div>
    </section>
  );
}

/** Events whose new value says something the event name does not (e.g. the new limit). */
const NEW_SHOWN = new Set(["TERMINAL_ADDED", "APPROVER_SET", "MAX_SET", "DAY_CLOSE_SET", "DAY_CONTROL_SET"]);

const onOff = (v: string | null) => (v?.toLowerCase() === "true" ? "on" : v?.toLowerCase() === "false" ? "off" : v);

function History({ events, error }: { events: DeviceEvent[] | null; error: string | null }) {
  return (
    <section className="card admin-card dev-history">
      <div className="admin-filters">
        <h2>Recent changes</h2>
        <span className="hint admin-count">Last 30 · who did what, and why</span>
      </div>
      {error && <div className="err">{error}</div>}
      {events && events.length === 0 && <div className="empty">Nothing yet</div>}
      {events && events.length > 0 && (
        <div className="table-scroll activity-scroll">
          <table className="admin-table">
            <thead>
              <tr>
                <th>When</th>
                <th>What</th>
                <th>Terminal</th>
                <th>By</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id}>
                  <td>{e.time ? new Date(e.time).toLocaleString() : "—"}</td>
                  <td className="strong">{EVENT_TEXT[e.event] ?? e.event}</td>
                  <td>{e.target ?? "—"}</td>
                  <td>{e.actor ?? "—"}</td>
                  <td className="detail-cell">{[e.reason, NEW_SHOWN.has(e.event) ? onOff(e.new) : null].filter(Boolean).join(" · ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
