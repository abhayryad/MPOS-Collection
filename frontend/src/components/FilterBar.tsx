import { useEffect, useState } from "react";
import type { Filters, Meta } from "../lib/api";
import { StoreSearch } from "./StoreSearch";

export type Preset = "yesterday" | "latest" | "all";

const PRESETS: { id: Preset; label: string }[] = [
  { id: "yesterday", label: "Yesterday" },
  { id: "latest", label: "Latest day" },
  { id: "all", label: "All" },
];

/** Calendar yesterday on this PC's clock, as YYYY-MM-DD. */
function yesterday() {
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function presetRange(meta: Meta, preset: Preset): Pick<Filters, "start" | "end"> {
  if (preset === "yesterday") {
    const y = yesterday();
    return { start: y, end: y };
  }
  const max = meta.max_date ?? "";
  return { start: preset === "all" ? meta.min_date ?? "" : max, end: max };
}

interface Props {
  meta: Meta;
  filters: Filters;
  preset: Preset | null;
  /** Patch the current filters; merged against the latest state, so rapid changes don't clobber each other. */
  onChange: (patch: Partial<Filters>, preset: Preset | null) => void;
  onRefresh: () => void;
  refreshing: boolean;
  /** Hide the store picker for reports that always cover every store. */
  showStore?: boolean;
}

export function FilterBar({ meta, filters, preset, onChange, onRefresh, refreshing, showStore = true }: Props) {
  return (
    <div className="filters" role="group" aria-label="Filters">
      <div className="presets">
        {PRESETS.map((p) => (
          <button
            key={p.id}
            type="button"
            aria-pressed={preset === p.id}
            onClick={() => onChange(presetRange(meta, p.id), p.id)}
          >
            {p.label}
          </button>
        ))}
      </div>
      <label className="single-date">
        Date
        <input
          type="date"
          aria-label="Specific date"
          title="Show one specific day"
          value={filters.start === filters.end ? filters.start : ""}
          min={meta.min_date ?? undefined}
          max={meta.max_date ?? undefined}
          onChange={(e) => e.target.value && onChange({ start: e.target.value, end: e.target.value }, null)}
        />
      </label>
      <span className="or">or range</span>
      <label>
        From
        <input
          type="date"
          value={filters.start}
          min={meta.min_date ?? undefined}
          max={filters.end}
          onChange={(e) => e.target.value && onChange({ start: e.target.value }, null)}
        />
      </label>
      <label>
        To
        <input
          type="date"
          value={filters.end}
          min={filters.start}
          onChange={(e) => e.target.value && onChange({ end: e.target.value }, null)}
        />
      </label>
      {showStore && (
        <div className="filter-field">
          Store
          <StoreSearch
            stores={meta.stores}
            value={filters.store}
            onChange={(store) => onChange({ store }, preset)}
            allLabel={meta.all_stores ? "All stores" : `All my stores (${meta.stores.length})`}
          />
        </div>
      )}
      <div className="spacer" />
      <button type="button" onClick={onRefresh} disabled={refreshing} title="Reload from the database">
        ↻ {refreshing ? "Loading…" : "Refresh"}
      </button>
    </div>
  );
}

/** Filter state for a page: defaults to "All" once metadata loads; patches merge into the latest state. */
export function useFilterState(meta: Meta | null) {
  const [filters, setFilters] = useState<Filters | null>(null);
  const [preset, setPreset] = useState<Preset | null>("all");

  useEffect(() => {
    if (meta && !filters) setFilters({ ...presetRange(meta, "all"), store: "" });
  }, [meta, filters]);

  const onChange = (patch: Partial<Filters>, p: Preset | null) => {
    setFilters((cur) => (cur ? { ...cur, ...patch } : cur));
    // a store change keeps whatever date preset is active
    setPreset((cur) => ("store" in patch ? cur : p));
  };
  const invalidRange = !!filters && filters.start > filters.end;
  // no dates to query: the tables are empty (All / Latest day) or a date is unset
  const noDates = !!filters && (!filters.start || !filters.end);
  return { filters, preset, onChange, invalidRange, noDates };
}
