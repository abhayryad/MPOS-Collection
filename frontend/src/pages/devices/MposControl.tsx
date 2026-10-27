import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  api,
  ApiError,
  DEVICE_STORE_KEY,
  type DeviceStore,
  type DevicesOverview,
  type PairCode,
  type PhoneDot,
  type Terminal,
} from "../../lib/api";
import { useAsync } from "../../lib/hooks";
import { Ago, ConfirmDialog, PairDialog, Switch, type Confirm } from "./StoreDevices";

const REFRESH_MS = 5_000; // answered from the portal's memory, so polling often costs nothing
const PAGE = 60;

/** What a phone looks like on this page: its state, or why it has none. */
type Kind = "ON" | "DRAINING" | "OFF" | "NO_PHONE" | "SILENT";
const LOOK: Record<Kind, { label: string; tone: string; screen: string; big: string }> = {
  ON: { label: "Billing allowed", tone: "on", screen: "#16a34a", big: "Ready to bill" },
  DRAINING: { label: "Finishing last bill", tone: "drain", screen: "#d97706", big: "Finish this bill" },
  OFF: { label: "Off", tone: "off", screen: "#b91c1c", big: "Billing stopped" },
  NO_PHONE: { label: "Waiting for a phone", tone: "none", screen: "#4338ca", big: "New terminal" },
  SILENT: { label: "Silent over 2 min", tone: "silent", screen: "#4b5563", big: "Not connected" },
};
const kind = (p: PhoneDot): Kind => (!p.paired ? "NO_PHONE" : p.silent ? "SILENT" : p.state);

type Card = Kind | "BILLS";

/** mPOS Devices > mPOS Control (HO): every store and phone live - the master switch, each store's HO
 *  switch, what any phone's cashier sees right now with that phone's switches, and a running log. */
export function MposControl() {
  const overview = useAsync((signal) => api.devicesOverview(signal), []);
  useEffect(() => {
    const t = setInterval(overview.reload, REFRESH_MS);
    return () => clearInterval(t);
  }, [overview.reload]);
  const o = overview.data;

  const [card, setCard] = useState<Card | null>(null);
  const [query, setQuery] = useState("");
  const [problemsFirst, setProblemsFirst] = useState(true);
  const [shown, setShown] = useState(PAGE);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [phone, setPhone] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [adding, setAdding] = useState<string[] | null | undefined>(undefined); // undefined = closed, null = choose
  const [pairing, setPairing] = useState<PairCode | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const feed = useFeed(o);

  const act = async (what: string, run: () => Promise<unknown>) => {
    setBusy(true);
    setFailure(null);
    feed.add(`You: ${what}`, true);
    try {
      await run();
      overview.reload();
    } catch (e) {
      setFailure((e as ApiError).message);
      feed.add(`Failed: ${what} - ${(e as ApiError).message}`, true);
    } finally {
      setBusy(false);
    }
  };

  /** Switching off asks first: how many phones it reaches and how many are mid-bill, and a reason. */
  const askOff = async (title: string, level: "global" | "stores", targets: string[] | null, run: (reason: string) => void) => {
    setFailure(null);
    try {
      const r = await api.preview(level, targets);
      setConfirm({
        title,
        body:
          `This reaches ${fmt(r.phones)} phone${r.phones === 1 ? "" : "s"} in ${fmt(r.stores)} store${r.stores === 1 ? "" : "s"}. ` +
          (r.billing_now ? `${fmt(r.billing_now)} have a bill open: they finish it, then stop. ` : "None has a bill open right now. ") +
          "No new bill can start until it is switched back on.",
        reasonLabel: "Reason - shown on every phone it reaches",
        reasonRequired: true,
        action: "Switch off",
        danger: true,
        run,
      });
    } catch (e) {
      setFailure((e as ApiError).message);
    }
  };

  const toggleGlobal = () => {
    if (!o) return;
    const v = o.global.version;
    if (o.global.enabled)
      return askOff("Switch every mPOS off?", "global", null, (reason) =>
        act(`all mPOS off (${reason})`, () => api.switchGlobal(false, reason, v)),
      );
    act("all mPOS on", () => api.switchGlobal(true, "", v));
  };
  const toggleStore = (s: DeviceStore) => {
    if (s.ho_enabled)
      return askOff(`Switch store ${s.code} off?`, "stores", [s.code], (reason) =>
        act(`store ${s.code} off (${reason})`, () => api.switchStores([s.code], false, reason, s.version)),
      );
    act(`store ${s.code} on`, () => api.switchStores([s.code], true, "", s.version));
  };
  const bulk = (on: boolean) => {
    const codes = [...selected];
    const done = () => setSelected(new Set());
    if (!on)
      return askOff(`Switch ${codes.length} stores off?`, "stores", codes, (reason) =>
        act(`${codes.length} stores off (${reason})`, () => api.switchStores(codes, false, reason).then(done)),
      );
    act(`${codes.length} stores on`, () => api.switchStores(codes, true, "").then(done));
  };
  const pick = (code: string, on: boolean) =>
    setSelected((cur) => {
      const next = new Set(cur);
      if (on) next.add(code);
      else next.delete(code);
      return next;
    });

  const stores = o?.stores ?? [];
  const rows = useMemo(() => {
    const q = query.trim().toUpperCase();
    const list = stores.filter(
      (s) =>
        (!q || [s.code, s.name, s.region, s.state].some((v) => (v || "").toUpperCase().includes(q))) &&
        (!card || (s.phones ?? []).some((p) => (card === "BILLS" ? p.bill : kind(p) === card))),
    );
    return problemsFirst ? [...list].sort((a, b) => trouble(b) - trouble(a) || a.code.localeCompare(b.code)) : list;
  }, [stores, query, card, problemsFirst]);
  const page = rows.slice(0, shown);
  const allPicked = page.length > 0 && page.every((s) => selected.has(s.code));
  const t = o?.totals;
  const cards: [Card, string, number, string][] = t
    ? [
        ["ON", "Billing allowed", t.on - t.silent, "on"],
        ["DRAINING", "Finishing last bill", t.draining, "drain"],
        ["OFF", "Off", t.off, "off"],
        ["BILLS", "Bills open now", t.bills_open, "billtone"],
        ["NO_PHONE", "Waiting for a phone", t.not_paired, "none"],
        ["SILENT", "Silent over 2 min", t.silent, "silent"],
      ]
    : [];
  const total = t?.terminals || 1;

  return (
    <div>
      <header className="page-head admin-head">
        <div>
          <div className="crumb">mPOS Devices ›</div>
          <h1>mPOS Control</h1>
          <div className="sub dev-live">
            <i className={overview.error ? "dev-pulse dev-pulse-down" : "dev-pulse"} aria-hidden="true" />
            {overview.error ? "Not reachable - retrying" : `Live · every store and phone · updates every ${REFRESH_MS / 1000} s`}
          </div>
        </div>
        <div className="admin-actions">
          <button type="button" className="primary" onClick={() => setAdding(null)} disabled={!o}>
            + Add mPOS
          </button>
          {o && (
            <div className={`dev-master${o.global.enabled ? "" : " dev-master-off"}`}>
              <div>
                <div className="strong">All mPOS devices</div>
                <div className="hint">
                  {o.global.enabled ? "On - stores control their own phones" : `Off by HO - ${o.global.reason || "no new bills anywhere"}`}
                </div>
              </div>
              <Switch on={o.global.enabled} disabled={busy} label="All mPOS devices" onClick={toggleGlobal} />
            </div>
          )}
        </div>
      </header>

      {overview.error && !o && <div className="err" role="alert">Could not load mPOS devices: {overview.error}</div>}
      {failure && <div className="err" role="alert">{failure}</div>}
      {o && !o.loaded && <div className="notice">Device control is still loading from Snowflake - try again shortly.</div>}

      {o && (
        <>
          <div className="dev-cards">
            {cards.map(([key, label, value, tone]) => (
              <button
                key={key}
                type="button"
                className={`dev-card${card === key ? " dev-card-on" : ""}`}
                aria-pressed={card === key}
                onClick={() => (setCard(card === key ? null : key), setShown(PAGE))}
              >
                <span className="dev-card-label">
                  <i className={`dev-dot dev-${tone}`} aria-hidden="true" />
                  {label}
                </span>
                <span className="dev-card-num">{fmt(value)}</span>
                <span className="dev-bar">
                  <i className={`dev-${tone}`} style={{ width: `${Math.min(100, (100 * value) / total)}%` }} />
                </span>
              </button>
            ))}
          </div>

          <div className="dev-main">
            <section className="card admin-card dev-stores">
              <div className="admin-filters">
                <h2>Stores</h2>
                <input
                  type="search"
                  className="admin-search"
                  placeholder="Search store code, name, region or state"
                  value={query}
                  onChange={(e) => (setQuery(e.target.value), setShown(PAGE))}
                />
                <label className="check">
                  <input type="checkbox" checked={problemsFirst} onChange={(e) => setProblemsFirst(e.target.checked)} />
                  Problems first
                </label>
                <span className="hint admin-count">
                  {rows.length} of {stores.length} stores · {fmt(t?.terminals ?? 0)} phones
                </span>
              </div>

              {selected.size > 0 ? (
                <div className="dev-bulk" role="region" aria-label="Selected stores">
                  <b>
                    {selected.size} store{selected.size === 1 ? "" : "s"} selected
                  </b>
                  <span className="spacer" />
                  <button type="button" disabled={busy} onClick={() => bulk(false)}>Switch off</button>
                  <button type="button" disabled={busy} onClick={() => bulk(true)}>Switch on</button>
                  <button type="button" disabled={busy} onClick={() => setAdding([...selected])}>Add mPOS</button>
                  <button type="button" className="ghost" onClick={() => setSelected(new Set())}>Clear</button>
                </div>
              ) : (
                <div className="dev-pickall">
                  <label className="check">
                    <input type="checkbox" checked={allPicked} onChange={(e) => page.forEach((s) => pick(s.code, e.target.checked))} />
                    Select the stores shown
                  </label>
                </div>
              )}

              <div className="dev-rows">
                {page.map((s) => (
                  <StoreRow
                    key={s.code}
                    s={s}
                    picked={selected.has(s.code)}
                    onPick={(on) => pick(s.code, on)}
                    phone={phone}
                    onPhone={setPhone}
                    onSwitch={() => toggleStore(s)}
                    onAdd={() => setAdding([s.code])}
                    busy={busy}
                  />
                ))}
                {rows.length === 0 && <div className="empty">No store matches</div>}
              </div>
              {rows.length > shown && (
                <div className="dev-more">
                  <button type="button" onClick={() => setShown(shown + PAGE)}>
                    Show {Math.min(PAGE, rows.length - shown)} more stores
                  </button>
                </div>
              )}
            </section>

            <aside className="dev-side">
              <section className="card admin-card">
                <div className="admin-filters">
                  <h2>What the cashier sees</h2>
                </div>
                <PhonePanel
                  id={phone}
                  overview={o}
                  tick={overview.data}
                  busy={busy}
                  act={act}
                  setConfirm={setConfirm}
                  onPairCode={setPairing}
                />
              </section>
              <section className="card admin-card">
                <div className="admin-filters">
                  <h2>Live activity</h2>
                  <span className="hint admin-count">Since you opened this page</span>
                </div>
                <div className="dev-feed" aria-live="polite">
                  {feed.items.map((e) => (
                    <div key={e.id} className={`dev-ev${e.you ? " dev-ev-you" : ""}`}>
                      <time>{e.time}</time>
                      <span>{e.text}</span>
                    </div>
                  ))}
                </div>
              </section>
            </aside>
          </div>
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
      {pairing && <PairDialog code={pairing} onClose={() => setPairing(null)} />}
      {adding !== undefined && o && (
        <AddDialog
          stores={stores}
          preset={adding}
          onClose={() => setAdding(undefined)}
          onDone={(added, where) => {
            feed.add(`You: added ${added.length} mPOS ${where}`, true);
            setSelected(new Set());
            if (added.length === 1) setPhone(added[0]);
            overview.reload();
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- a store row

function StoreRow({ s, picked, onPick, phone, onPhone, onSwitch, onAdd, busy }: {
  s: DeviceStore;
  picked: boolean;
  onPick: (on: boolean) => void;
  phone: string | null;
  onPhone: (id: string) => void;
  onSwitch: () => void;
  onAdd: () => void;
  busy: boolean;
}) {
  const phones = s.phones ?? [];
  const billing = phones.filter((p) => kind(p) === "ON").length;
  return (
    <div className={`dev-srow${s.ho_enabled ? "" : " dev-srow-off"}`}>
      <input type="checkbox" aria-label={`Select ${s.code}`} checked={picked} onChange={(e) => onPick(e.target.checked)} />
      <div className="dev-sname">
        <div className="strong">{s.code}</div>
        <div className="hint dev-ellipsis" title={[s.name, s.region, s.state].filter(Boolean).join(" · ")}>
          {s.name || "—"}
        </div>
        <div className="hint">
          {!s.ho_enabled
            ? `Off by HO${s.ho_reason ? `: ${s.ho_reason}` : ""}`
            : s.day_control && !s.day_open
              ? "Day not opened"
              : phones.length
                ? `${billing} of ${phones.length} billing`
                : "No mPOS yet"}
        </div>
      </div>
      <div className="dev-chips">
        {phones.map((p) => {
          const k = kind(p);
          return (
            <button
              key={p.id}
              type="button"
              className={`dev-chip${phone === p.id ? " dev-chip-sel" : ""}${k === "NO_PHONE" ? " dev-chip-empty" : ""}`}
              title={`${p.id}: ${LOOK[k].label}${p.paired && k !== "ON" && k !== "SILENT" ? ` - ${p.message}` : ""}`}
              onClick={() => onPhone(p.id)}
            >
              <i className={`dev-dot dev-${LOOK[k].tone}`} aria-hidden="true" />
              {p.id.split("_").pop()}
              {p.bill && <b className="dev-bill">BILL</b>}
              {p.lock && <b className="dev-lockt">LOCK</b>}
            </button>
          );
        })}
      </div>
      <div className="dev-sctl">
        <button type="button" className="dev-small" disabled={busy} onClick={onAdd} title={`Add mPOS to ${s.code}`}>
          + mPOS
        </button>
        <span className="hint">Store</span>
        <Switch on={s.ho_enabled} disabled={busy} label={`HO switch for ${s.code}`} onClick={onSwitch} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- the phone preview

function PhonePanel({ id, overview, tick, busy, act, setConfirm, onPairCode }: {
  id: string | null;
  overview: DevicesOverview;
  tick: unknown; // a new overview = time to reload this phone too
  busy: boolean;
  act: (what: string, run: () => Promise<unknown>) => void;
  setConfirm: (c: Confirm) => void;
  onPairCode: (c: PairCode) => void;
}) {
  const storeCode = id ? id.split("_MPOS")[0] : "";
  const store = useAsync((signal) => api.storeDevices(storeCode, signal), [storeCode, tick], !!storeCode);
  if (!id) return <div className="empty dev-empty-side">Click any phone in the list to see its screen and its switches.</div>;
  const t: Terminal | undefined = store.data?.terminal_list.find((x) => x.terminal_id === id);
  const dot = overview.stores.find((s) => s.code === storeCode)?.phones?.find((p) => p.id === id);
  if (!t || !dot) return <div className="empty dev-empty-side">{store.error ?? (store.loading ? "Loading…" : `${id} is no longer active`)}</div>;
  const k = kind(dot);
  const look = LOOK[k];
  const short = id.split("_").pop();

  const message =
    k === "NO_PHONE"
      ? "Get a pairing code below and scan it on the new phone"
      : k === "SILENT"
        ? "No check-in for 2 minutes - phone off, out of network or app closed"
        : k === "ON"
          ? "New bills can be started"
          : k === "DRAINING"
            ? `${t.message}. Complete the open bill - no new bill after it.`
            : t.message;

  const block = (what: "veto" | "lock") => {
    const active = what === "veto" ? t.veto : t.lock;
    const call = what === "veto" ? api.veto : api.lock;
    if (active) return act(`${what === "veto" ? "lift block on" : "unlock"} ${id}`, () => call(id, false, "", t.version));
    setConfirm({
      title: what === "veto" ? `Block ${short}?` : `Lock ${short} now?`,
      body:
        what === "veto"
          ? `${id} stops after any open bill, and the store cannot switch it back on until HO lifts the block.`
          : `For a lost or stolen phone. ${id} stops at once${t.bill_open ? " - even the bill open on it now" : ""}, and stays locked until HO unlocks it.`,
      reasonLabel: "Reason - shown on the phone",
      reasonRequired: true,
      action: what === "veto" ? "Block" : "Lock now",
      danger: true,
      run: (reason) => act(`${what === "veto" ? "block" : "lock"} ${id} (${reason})`, () => call(id, true, reason, t.version)),
    });
  };
  const storeSwitch = () => {
    if (!t.store_enabled) return act(`store switch on for ${id}`, () => api.switchTerminal(id, true, "", t.version));
    setConfirm({
      title: `Switch ${short} off?`,
      body: t.bill_open
        ? `A bill is open on ${id}. The cashier finishes that bill, then the phone stops.`
        : `${id} stops billing within 30 seconds, until it is switched on again.`,
      reasonLabel: "Reason (optional, kept in history)",
      action: "Switch off",
      danger: true,
      run: (reason) => act(`store switch off for ${id}`, () => api.switchTerminal(id, false, reason, t.version)),
    });
  };

  return (
    <div className="dev-phonebox">
      <div className="dev-phone">
        <div className="dev-screen" style={{ background: look.screen }}>
          <div className="dev-tid">
            {id}
            <br />
            {store.data?.name}
          </div>
          <div className="dev-icon" aria-hidden="true">{ICON[k]}</div>
          <div className="dev-big">{look.big}</div>
          <div className="dev-msg">{message}</div>
          <div className="dev-foot">
            {k === "NO_PHONE" ? "Not paired" : t.bill_open ? "Bill open" : "No bill open"}
            {t.paired && t.last_seen ? <> · <Ago iso={t.last_seen} /></> : null}
          </div>
        </div>
      </div>
      <div className="dev-facts hint">
        {t.paired ? <>Phone <span className="dev-mono">{t.phone}</span>{t.app_version ? ` · app ${t.app_version}` : ""}</> : "No phone paired"}
      </div>
      <div className="dev-ctl">
        <CtlRow title="Store switch" sub="The store approver's on/off" on={t.store_enabled} onClick={storeSwitch} busy={busy} />
        <CtlRow title="HO block" sub="HO blocks it - the store can't override" on={t.veto} danger onClick={() => block("veto")} busy={busy} />
        <CtlRow title="Immediate lock" sub="Lost or stolen - stops even mid-bill" on={t.lock} danger onClick={() => block("lock")} busy={busy} />
      </div>
      <div className="row-actions dev-phone-actions">
        <button type="button" disabled={busy} onClick={() => act(`pairing code for ${id}`, async () => onPairCode(await api.pairCode(id)))}>
          {t.paired ? "Pair new phone" : "Get pairing code"}
        </button>
        {t.paired && (
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              setConfirm({
                title: `Unpair the phone from ${short}?`,
                body: t.bill_open ? `A bill is open on ${id} - unpair once it is finished.` : `The phone stops at once and ${id} waits for a new phone.`,
                reasonLabel: "Reason (optional, kept in history)",
                action: "Unpair",
                danger: true,
                disabled: t.bill_open,
                run: (reason) => act(`unpair ${id}`, () => api.unpair(id, reason)),
              })
            }
          >
            Unpair
          </button>
        )}
        <button
          type="button"
          disabled={busy}
          onClick={() =>
            setConfirm({
              title: `Retire ${id}?`,
              body: t.bill_open
                ? `A bill is open on ${id} - retire it once it is finished.`
                : `For good: its phone stops, ${short} is never used again, and the store gets a free slot.`,
              reasonLabel: "Reason (optional, kept in history)",
              action: "Retire",
              danger: true,
              disabled: t.bill_open,
              run: (reason) => act(`retire ${id}`, () => api.retire(id, reason)),
            })
          }
        >
          Retire
        </button>
        <button type="button" className="ghost" onClick={() => openStore(storeCode)}>
          Store page ›
        </button>
      </div>
    </div>
  );
}

function CtlRow({ title, sub, on, danger, onClick, busy }: { title: string; sub: string; on: boolean; danger?: boolean; onClick: () => void; busy: boolean }) {
  return (
    <div className="dev-ctlrow">
      <div>
        <b>{title}</b>
        <span className="hint">{sub}</span>
      </div>
      <span className={danger ? "dev-switch-danger" : undefined}>
        <Switch on={on} disabled={busy} label={title} onClick={onClick} />
      </span>
    </div>
  );
}

const ICON: Record<Kind, ReactNode> = {
  ON: <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6 9 17l-5-5" /></svg>,
  DRAINING: <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.5" strokeLinecap="round"><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>,
  OFF: <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.5" strokeLinecap="round"><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></svg>,
  NO_PHONE: <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.5" strokeLinecap="round"><rect x="6" y="2" width="12" height="20" rx="2" /><path d="M12 8v6M9 11h6" /></svg>,
  SILENT: <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.5" strokeLinecap="round"><path d="M2 2l20 20M8.5 16.5a5 5 0 0 1 7 0M5 13a10 10 0 0 1 5.2-2.8M19 13a10 10 0 0 0-2.3-1.7M12 20h.01" /></svg>,
};

// ---------------------------------------------------------------- live activity

type FeedItem = { id: number; time: string; text: string; you: boolean };

/** Compares each refresh with the last one and writes what changed: one line per kind of change, or
 *  per phone when only one changed. Your own actions are added as they happen. */
function useFeed(o: DevicesOverview | null) {
  const [items, setItems] = useState<FeedItem[]>([]);
  const prev = useRef<Map<string, PhoneDot> | null>(null);
  const seq = useRef(0);
  const add = (text: string, you = false) =>
    setItems((cur) => [{ id: ++seq.current, time: new Date().toLocaleTimeString([], { hour12: false }), text, you }, ...cur].slice(0, 150));

  useEffect(() => {
    if (!o) return;
    const now = new Map(o.stores.flatMap((s) => (s.phones ?? []).map((p) => [p.id, p] as const)));
    const before = prev.current;
    prev.current = now;
    if (!before) return add("Watching every phone. Flip a switch to see what happens.");
    const groups = new Map<string, string[]>();
    for (const [id, p] of now) {
      const was = before.get(id);
      if (!was) {
        groups.set("new", [...(groups.get("new") ?? []), id]);
        continue;
      }
      const a = kind(was), b = kind(p);
      if (a !== b) groups.set(`${a}>${b}`, [...(groups.get(`${a}>${b}`) ?? []), id]);
    }
    for (const [key, ids] of groups) {
      const who = ids.length === 1 ? ids[0] : `${fmt(ids.length)} phones`;
      if (key === "new") {
        add(`${who} added`);
        continue;
      }
      const [a, b] = key.split(">") as [Kind, Kind];
      const why =
        a === "NO_PHONE" ? " · phone paired" : a === "DRAINING" && b === "OFF" ? " · last bill done" : b === "SILENT" ? " · stopped checking in" : "";
      add(`${who}: ${LOOK[a].label} → ${LOOK[b].label}${why}`);
    }
  }, [o]); // eslint-disable-line react-hooks/exhaustive-deps

  return { items, add };
}

// ---------------------------------------------------------------- helpers

const fmt = (n: number) => n.toLocaleString("en-IN");

function openStore(code: string) {
  try {
    sessionStorage.setItem(DEVICE_STORE_KEY, code);
  } catch {
    /* storage blocked: Store Devices opens on its default store */
  }
  window.location.hash = "#/devices";
}

/** How much needs attention in a store - for "Problems first". */
function trouble(s: DeviceStore) {
  if (!s.terminals) return -1;
  return (s.ho_enabled ? 0 : 1000) + s.silent * 20 + s.off * 10 + s.draining * 5 + s.not_paired * 3;
}

function AddDialog({ stores, preset, onClose, onDone }: {
  stores: DeviceStore[];
  preset: string[] | null; // the selected stores or the row's store, or null = choose here
  onClose: () => void;
  onDone: (added: string[], where: string) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const single = preset?.length === 1 ? preset[0] : "";
  const [where, setWhere] = useState<"one" | "picked" | "all">(preset && preset.length > 1 ? "picked" : "one");
  const [code, setCode] = useState(single);
  const [count, setCount] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ added: string[]; at_limit: string[] } | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => ref.current?.showModal(), []);

  const one = stores.find((s) => s.code === code.trim().toUpperCase());
  const max = one?.max_mpos ?? stores[0]?.max_mpos ?? 5;
  const submit = async () => {
    setError(null);
    if (where === "one" && !one) return setError("Pick a store from the list");
    const targets = where === "all" ? null : where === "picked" ? preset : [one!.code];
    if (where === "all" && !window.confirm(`Add ${count} mPOS to every store (${stores.length} stores)?`)) return;
    setBusy(true);
    try {
      const r = await api.addTerminals(targets, count);
      setResult(r);
      onDone(r.added, where === "all" ? "across every store" : where === "picked" ? `to ${preset!.length} stores` : `to ${one!.code}`);
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
          {preset && preset.length > 1 && (
            <label className="check">
              <input type="radio" checked={where === "picked"} onChange={() => setWhere("picked")} />
              The {preset.length} selected stores
            </label>
          )}
          <label className="check">
            <input type="radio" checked={where === "one"} onChange={() => setWhere("one")} />
            One store
          </label>
          {where === "one" && (
            <>
              <input list="dev-store-list" placeholder="Store code" value={code} onChange={(e) => setCode(e.target.value)} autoFocus={!single} />
              <datalist id="dev-store-list">
                {stores.map((s) => (
                  <option key={s.code} value={s.code}>
                    {s.name}
                  </option>
                ))}
              </datalist>
              {one && (
                <span className="field-hint">
                  {one.name} · {one.code} has {one.terminals} of {one.max_mpos}
                  {one.phones?.length ? `: ${one.phones.map((p) => p.id.split("_").pop()).join(", ")}` : ""}
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
            Each takes the store's next number (MPOS3, MPOS4…), never reused. A store stops at its limit ({max}). Then pair a
            phone to each - from here or the store's page.
          </span>
        </label>
        {error && <span className="field-error">{error}</span>}
        {result && (
          <div className="notice">
            {result.added.length ? <>Added {result.added.length}: {result.added.slice(0, 8).join(", ")}{result.added.length > 8 ? "…" : ""}</> : "Nothing added."}
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
              {busy ? "Adding…" : where === "all" ? `Add ${count} to every store` : `Add ${count}`}
            </button>
          )}
        </div>
      </div>
    </dialog>
  );
}
