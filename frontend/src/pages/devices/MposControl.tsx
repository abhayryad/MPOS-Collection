import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, DEVICE_STORE_KEY, type DeviceStore, type PhoneDot } from "../../lib/api";
import { useAsync } from "../../lib/hooks";
import { ConfirmDialog, Switch, Tile, type Confirm } from "./StoreDevices";

const REFRESH_MS = 15_000;
const PAGE = 50;

type Filter = "OFF" | "SILENT" | "BILLS" | "NO_PHONE" | null;
const FILTERS: { key: Filter; label: string; test: (s: DeviceStore) => boolean }[] = [
  { key: "OFF", label: "Off", test: (s) => !s.ho_enabled || s.off > 0 },
  { key: "SILENT", label: "Silent", test: (s) => s.silent > 0 },
  { key: "BILLS", label: "Bills open", test: (s) => s.bills_open > 0 },
  { key: "NO_PHONE", label: "No phone", test: (s) => s.not_paired > 0 },
];

/** mPOS Devices > mPOS Control (HO): every store at once - the master switch, each store's HO switch,
 *  several stores together, and adding terminals. A store's own page has the per-phone controls. */
export function MposControl() {
  const overview = useAsync((signal) => api.devicesOverview(signal), []);
  useEffect(() => {
    const t = setInterval(overview.reload, REFRESH_MS);
    return () => clearInterval(t);
  }, [overview.reload]);

  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>(null);
  const [problemsFirst, setProblemsFirst] = useState(true);
  const [shown, setShown] = useState(PAGE);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [notice, setNotice] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [adding, setAdding] = useState<string[] | null | undefined>(undefined); // undefined = closed, null = all

  const o = overview.data;
  const stores = o?.stores ?? [];
  const rows = useMemo(() => {
    const q = query.trim().toUpperCase();
    const test = FILTERS.find((f) => f.key === filter)?.test;
    const list = stores.filter(
      (s) =>
        (!q || [s.code, s.name, s.region, s.state].some((v) => (v || "").toUpperCase().includes(q))) &&
        (!test || test(s)),
    );
    return problemsFirst ? [...list].sort((a, b) => trouble(b) - trouble(a) || a.code.localeCompare(b.code)) : list;
  }, [stores, query, filter, problemsFirst]);

  const act = async (run: () => Promise<string>) => {
    setBusy(true);
    setFailure(null);
    setNotice(null);
    try {
      setNotice(await run());
      overview.reload();
    } catch (e) {
      setFailure((e as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  /** Ask before switching off: how many phones it reaches and how many are mid-bill. */
  const askOff = async (what: string, level: "global" | "stores", targets: string[] | null, run: (r: string) => Promise<string>) => {
    setFailure(null);
    let reach;
    try {
      reach = await api.preview(level, targets);
    } catch (e) {
      return setFailure((e as ApiError).message);
    }
    setConfirm({
      title: `Switch ${what} off?`,
      body:
        `This reaches ${fmt(reach.phones)} phone${reach.phones === 1 ? "" : "s"} in ${fmt(reach.stores)} store${reach.stores === 1 ? "" : "s"}. ` +
        (reach.billing_now
          ? `${fmt(reach.billing_now)} have a bill open: they finish it, then stop. `
          : "None has a bill open right now. ") +
        "No new bill can start until it is switched back on.",
      reasonLabel: "Reason - shown on every phone it reaches",
      reasonRequired: true,
      action: "Switch off",
      danger: true,
      run: (reason) => act(() => run(reason)),
    });
  };

  const toggleGlobal = () => {
    if (!o) return;
    if (o.global.enabled)
      return askOff("every mPOS", "global", null, async (reason) => {
        await api.switchGlobal(false, reason, o.global.version);
        return "Every mPOS switched off";
      });
    act(async () => {
      await api.switchGlobal(true, "", o.global.version);
      return "Every mPOS switched back on - each store's own switches apply again";
    });
  };
  const toggleStore = (s: DeviceStore) => {
    if (s.ho_enabled)
      return askOff(`store ${s.code}`, "stores", [s.code], async (reason) => {
        await api.switchStores([s.code], false, reason, s.version);
        return `${s.code} switched off`;
      });
    act(async () => {
      await api.switchStores([s.code], true, "", s.version);
      return `${s.code} switched on`;
    });
  };
  const bulk = (on: boolean) => {
    const codes = [...selected];
    if (!on)
      return askOff(`${codes.length} stores`, "stores", codes, async (reason) => {
        const r = await api.switchStores(codes, false, reason);
        setSelected(new Set());
        return `${r.stores_changed} stores switched off`;
      });
    act(async () => {
      const r = await api.switchStores(codes, true, "");
      setSelected(new Set());
      return `${r.stores_changed} stores switched on`;
    });
  };
  const open = (code: string) => {
    try {
      sessionStorage.setItem(DEVICE_STORE_KEY, code);
    } catch {
      /* storage blocked: Store Devices opens on its default store */
    }
    window.location.hash = "#/devices";
  };
  const pick = (code: string, on: boolean) =>
    setSelected((cur) => {
      const next = new Set(cur);
      if (on) next.add(code);
      else next.delete(code);
      return next;
    });

  const page = rows.slice(0, shown);
  const allPicked = page.length > 0 && page.every((s) => selected.has(s.code));

  return (
    <div className={overview.loading && !o ? "loading" : undefined}>
      <header className="page-head admin-head">
        <div>
          <div className="crumb">mPOS Devices ›</div>
          <h1>mPOS Control</h1>
          <div className="sub">All stores · refreshes every {REFRESH_MS / 1000} s</div>
        </div>
        <div className="admin-actions">
          <button type="button" onClick={() => setAdding(null)} disabled={!o}>
            + Add mPOS
          </button>
          {o && (
            <div className={`dev-master${o.global.enabled ? "" : " dev-master-off"}`}>
              <div>
                <div className="strong">All mPOS</div>
                <div className="hint">
                  {o.global.enabled ? "On - each store's own switches apply" : `Off - ${o.global.reason || "no reason given"}`}
                </div>
              </div>
              <Switch on={o.global.enabled} disabled={busy} label="All mPOS" onClick={toggleGlobal} />
            </div>
          )}
        </div>
      </header>

      {overview.error && <div className="err" role="alert">Could not load mPOS devices: {overview.error}</div>}
      {failure && <div className="err" role="alert">{failure}</div>}
      {notice && <div className="notice" role="status">{notice}</div>}
      {o && !o.loaded && <div className="notice">Device control is still loading from Snowflake - try again shortly.</div>}

      {o && (
        <>
          <div className="tiles dev-tiles-6">
            <Tile label="Billing allowed" value={o.totals.on} tone="on" />
            <Tile label="Finishing last bill" value={o.totals.draining} tone="drain" />
            <Tile label="Off" value={o.totals.off} tone="off" />
            <Tile label="Waiting for a phone" value={o.totals.not_paired} />
            <Tile label="Silent over 2 min" value={o.totals.silent} tone={o.totals.silent ? "off" : undefined} />
            <Tile label="Bills open now" value={o.totals.bills_open} note={`${fmt(o.totals.terminals)} terminals in ${stores.filter((s) => s.terminals).length} stores`} />
          </div>

          <section className="card admin-card">
            <div className="admin-filters">
              <input
                type="search"
                className="admin-search"
                placeholder="Search store, name, region or state"
                value={query}
                onChange={(e) => (setQuery(e.target.value), setShown(PAGE))}
              />
              <span className="presets" role="group" aria-label="Show">
                {FILTERS.map((f) => (
                  <button
                    key={f.key}
                    type="button"
                    aria-pressed={filter === f.key}
                    onClick={() => (setFilter(filter === f.key ? null : f.key), setShown(PAGE))}
                  >
                    {f.label} <span className="hint">{stores.filter(f.test).length}</span>
                  </button>
                ))}
              </span>
              <label className="check">
                <input type="checkbox" checked={problemsFirst} onChange={(e) => setProblemsFirst(e.target.checked)} />
                Problems first
              </label>
              <span className="hint admin-count">
                {rows.length} of {stores.length} stores
              </span>
            </div>

            {selected.size > 0 && (
              <div className="dev-bulk" role="region" aria-label="Selected stores">
                <b>
                  {selected.size} store{selected.size === 1 ? "" : "s"} selected
                </b>
                <span className="spacer" />
                <button type="button" disabled={busy} onClick={() => bulk(false)}>
                  Switch off
                </button>
                <button type="button" disabled={busy} onClick={() => bulk(true)}>
                  Switch on
                </button>
                <button type="button" disabled={busy} onClick={() => setAdding([...selected])}>
                  Add mPOS
                </button>
                <button type="button" className="ghost" onClick={() => setSelected(new Set())}>
                  Clear
                </button>
              </div>
            )}

            <div className="table-scroll">
              <table className="admin-table dev-table">
                <thead>
                  <tr>
                    <th className="dev-pick">
                      <input
                        type="checkbox"
                        aria-label="Select the stores shown"
                        checked={allPicked}
                        onChange={(e) => page.forEach((s) => pick(s.code, e.target.checked))}
                      />
                    </th>
                    <th>Store</th>
                    <th>Phones</th>
                    <th>Status</th>
                    <th className="num">Bills open</th>
                    <th>Approver</th>
                    <th>Store switch</th>
                    <th aria-label="Open" />
                  </tr>
                </thead>
                <tbody>
                  {page.map((s) => (
                    <tr key={s.code} className={s.ho_enabled ? undefined : "dev-row-off"}>
                      <td className="dev-pick">
                        <input
                          type="checkbox"
                          aria-label={`Select ${s.code}`}
                          checked={selected.has(s.code)}
                          onChange={(e) => pick(s.code, e.target.checked)}
                        />
                      </td>
                      <td>
                        <div className="strong">{s.code}</div>
                        <div className="hint">{[s.name, s.state].filter(Boolean).join(" · ")}</div>
                      </td>
                      <td>
                        <Dots phones={s.phones ?? []} />
                      </td>
                      <td className="dev-why-cell">
                        <StoreStatus s={s} />
                      </td>
                      <td className="num">{s.bills_open || <span className="muted">0</span>}</td>
                      <td>{s.approver ?? <span className="muted">—</span>}</td>
                      <td>
                        <Switch on={s.ho_enabled} disabled={busy} label={`HO switch for ${s.code}`} onClick={() => toggleStore(s)} />
                      </td>
                      <td>
                        <button type="button" className="ghost" onClick={() => open(s.code)}>
                          Open ›
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {rows.length === 0 && <div className="empty">No store matches</div>}
            {rows.length > shown && (
              <div className="dev-more">
                <button type="button" onClick={() => setShown(shown + PAGE)}>
                  Show {Math.min(PAGE, rows.length - shown)} more
                </button>
              </div>
            )}
            <div className="dev-legend hint">
              <span><i className="dev-sq dev-on" /> billing</span>
              <span><i className="dev-sq dev-drain" /> finishing bill</span>
              <span><i className="dev-sq dev-off" /> off</span>
              <span><i className="dev-sq dev-silent" /> silent</span>
              <span><i className="dev-sq dev-empty" /> no phone</span>
            </div>
          </section>
        </>
      )}

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
      {adding !== undefined && o && (
        <AddDialog
          stores={stores}
          preset={adding}
          onClose={() => setAdding(undefined)}
          onDone={(msg) => {
            setNotice(msg);
            setSelected(new Set());
            overview.reload();
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- pieces

const fmt = (n: number) => n.toLocaleString("en-IN");

/** How much needs attention in a store - for "Problems first". */
function trouble(s: DeviceStore) {
  if (!s.terminals) return -1;
  return (s.ho_enabled ? 0 : 1000) + s.silent * 20 + s.off * 10 + s.draining * 5 + s.not_paired * 3;
}

function Dots({ phones }: { phones: PhoneDot[] }) {
  if (!phones.length) return <span className="muted">No mPOS</span>;
  return (
    <span className="dev-dots">
      {phones.map((p) => {
        const tone = !p.paired ? "empty" : p.silent ? "silent" : p.state === "ON" ? "on" : p.state === "DRAINING" ? "drain" : "off";
        const label = `${p.id}: ${!p.paired ? "no phone" : p.silent ? "silent - no check-in for 2 min" : p.message}${p.bill ? " · bill open" : ""}`;
        return <i key={p.id} className={`dev-sq dev-${tone}${p.bill ? " dev-sq-bill" : ""}`} title={label} aria-label={label} />;
      })}
    </span>
  );
}

function StoreStatus({ s }: { s: DeviceStore }) {
  const phones = s.phones ?? [];
  const locked = phones.filter((p) => p.lock).length;
  const vetoed = phones.filter((p) => p.veto).length;
  const parts: [string, string][] = [];
  if (!s.terminals) return <span className="muted">No mPOS</span>;
  if (!s.ho_enabled) return <span className="dev-text-off">Off by HO{s.ho_reason ? `: ${s.ho_reason}` : ""}</span>;
  if (locked) parts.push([`${locked} locked`, "off"]);
  if (vetoed) parts.push([`${vetoed} blocked by HO`, "off"]);
  if (s.silent) parts.push([`${s.silent} silent`, "off"]);
  if (s.draining) parts.push([`${s.draining} finishing bill`, "drain"]);
  const otherOff = s.off - locked - vetoed;
  if (otherOff > 0) parts.push([`${otherOff} off`, "off"]);
  if (s.not_paired) parts.push([`${s.not_paired} waiting for phone`, "none"]);
  if (s.day_control && !s.day_open) parts.unshift(["Day not opened", "off"]);
  if (!parts.length) return <span>All billing</span>;
  return (
    <span>
      {parts.map(([text, tone], i) => (
        <span key={text} className={tone === "none" ? "muted" : `dev-text-${tone}`}>
          {i > 0 && " · "}
          {text}
        </span>
      ))}
    </span>
  );
}

function AddDialog({ stores, preset, onClose, onDone }: {
  stores: DeviceStore[];
  preset: string[] | null; // stores picked on the page, or null = choose here
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [where, setWhere] = useState<"one" | "picked" | "all">(preset?.length ? "picked" : "one");
  const [code, setCode] = useState("");
  const [count, setCount] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ added: string[]; at_limit: string[] } | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => ref.current?.showModal(), []);

  const one = stores.find((s) => s.code === code.trim().toUpperCase());
  const max = stores[0]?.max_mpos ?? 5;
  const submit = async () => {
    setError(null);
    if (where === "one" && !one) return setError("Pick a store from the list");
    const targets = where === "all" ? null : where === "picked" ? preset : [one!.code];
    if (where === "all" && !confirm(`Add ${count} mPOS to every store (${stores.length} stores)?`)) return;
    setBusy(true);
    try {
      const r = await api.addTerminals(targets, count);
      setResult(r);
      onDone(`${r.added.length} mPOS added${r.at_limit.length ? ` - ${r.at_limit.length} store(s) were at their limit` : ""}`);
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <dialog ref={ref} className="form-dialog" onCancel={onClose} aria-label="Add mPOS">
      <div className="dialog-form">
        <h2>Add mPOS terminals</h2>
        <fieldset>
          <legend>Where</legend>
          {preset && preset.length > 0 && (
            <label className="check">
              <input type="radio" checked={where === "picked"} onChange={() => setWhere("picked")} />
              The {preset.length} selected store{preset.length === 1 ? "" : "s"}
            </label>
          )}
          <label className="check">
            <input type="radio" checked={where === "one"} onChange={() => setWhere("one")} />
            One store
          </label>
          {where === "one" && (
            <>
              <input list="dev-store-list" placeholder="Store code" value={code} onChange={(e) => setCode(e.target.value)} autoFocus />
              <datalist id="dev-store-list">
                {stores.map((s) => (
                  <option key={s.code} value={s.code}>
                    {s.name}
                  </option>
                ))}
              </datalist>
              {one && (
                <span className="field-hint">
                  {one.name} has {one.terminals} of {one.max_mpos}
                </span>
              )}
            </>
          )}
          <label className="check">
            <input type="radio" checked={where === "all"} onChange={() => setWhere("all")} />
            Every store ({stores.length})
          </label>
        </fieldset>
        <label>
          How many in each store
          <input type="number" min={1} max={max} value={count} onChange={(e) => setCount(Math.max(1, Math.min(max, +e.target.value || 1)))} />
          <span className="field-hint">
            Each takes the store's next number (MPOS3, MPOS4…), never reused. A store stops at its limit ({max}). The
            store then pairs a phone to each from Store Devices.
          </span>
        </label>
        {error && <span className="field-error">{error}</span>}
        {result && (
          <div className="notice">
            {result.added.length ? (
              <>
                Added {result.added.length}: {result.added.slice(0, 8).join(", ")}
                {result.added.length > 8 ? "…" : ""}
              </>
            ) : (
              "Nothing added."
            )}
            {result.at_limit.length > 0 && (
              <div className="field-error">At their limit: {result.at_limit.slice(0, 10).join(", ")}{result.at_limit.length > 10 ? "…" : ""}</div>
            )}
          </div>
        )}
        <div className="auth-actions">
          <button type="button" onClick={onClose}>
            {result ? "Done" : "Cancel"}
          </button>
          {!result && (
            <button type="button" className="primary" disabled={busy} onClick={submit}>
              {busy ? "Adding…" : "Add"}
            </button>
          )}
        </div>
      </div>
    </dialog>
  );
}
