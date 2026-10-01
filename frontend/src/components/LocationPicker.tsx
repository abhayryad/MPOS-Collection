import { useMemo, useState } from "react";
import type { StoreInfo } from "../lib/api";

export const HO = "HO";

interface Props {
  stores: StoreInfo[]; // active stores from the store master
  value: string[]; // ['HO'] or store codes
  onChange: (locations: string[]) => void;
  loading?: boolean;
  error?: string | null;
  allowHo?: boolean; // false for an admin limited to their own stores
  legend?: string;
}

/** HO (all stores) or a searchable multi-select of store codes. */
export function LocationPicker({
  stores,
  value,
  onChange,
  loading,
  error,
  allowHo = true,
  legend = "Location - which stores this user can see",
}: Props) {
  const [search, setSearch] = useState("");
  const isHo = value.includes(HO);
  const byCode = useMemo(() => new Map(stores.map((s) => [s.code, s])), [stores]);

  const matches = useMemo(() => {
    const q = search.trim().toLowerCase();
    const list = q
      ? stores.filter((s) =>
          [s.code, s.name, s.zone, s.region, s.state].some((v) => v.toLowerCase().includes(q)),
        )
      : stores;
    return list;
  }, [stores, search]);

  const toggle = (code: string) =>
    onChange(value.includes(code) ? value.filter((c) => c !== code) : [...value.filter((c) => c !== HO), code].sort());

  return (
    <fieldset className="location-picker">
      <legend>{legend}</legend>
      {allowHo && (
        <label className="check">
          <input type="checkbox" checked={isHo} onChange={() => onChange(isHo ? [] : [HO])} />
          <span className="chip chip-admin">HO</span> Head office - all stores
        </label>
      )}

      {!isHo && (
        <>
          <div className="picked">
            {value.length === 0 && <span className="field-hint">No store chosen yet.</span>}
            {value.map((c) => (
              <button type="button" key={c} className="chip chip-removable" onClick={() => toggle(c)} title="Remove">
                {c}
                {byCode.get(c) && <span className="chip-sub"> {byCode.get(c)!.name}</span>} ✕
              </button>
            ))}
          </div>
          <input
            type="search"
            placeholder="Search store code, name, zone, region…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search stores"
          />
          <div className="store-options" role="group" aria-label="Stores">
            {loading && <div className="field-hint">Loading stores…</div>}
            {error && <div className="field-error">{error}</div>}
            {!loading &&
              matches.map((s) => (
                <label key={s.code} className="check store-option">
                  <input type="checkbox" checked={value.includes(s.code)} onChange={() => toggle(s.code)} />
                  <span className="mono">{s.code}</span>
                  <span>{s.name}</span>
                  <span className="muted">
                    {[s.zone, s.region].filter(Boolean).join(" · ")}
                  </span>
                </label>
              ))}
            {!loading && matches.length === 0 && <div className="field-hint">No active store matches.</div>}
          </div>
          <span className="field-hint">
            {search ? `${matches.length} of ${stores.length}` : stores.length} active stores (ST_TYP = STORE, ST_STAT = ACT)
          </span>
        </>
      )}
    </fieldset>
  );
}
