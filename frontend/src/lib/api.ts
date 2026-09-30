// Typed client for the FastAPI backend (app.py).

export type Kind = "M" | "S" | "R" | "C";

export interface Meta {
  server: string;
  database: string;
  stores: string[];
  min_date: string | null;
  max_date: string | null;
  mop_codes: string[];
  kinds: Record<Kind, string>;
}

export interface Filters {
  start: string;
  end: string;
  store: string;
}

export type CollectionRow = { date: string } & Record<string, number | string>;

export interface ItemRow {
  date: string;
  sold: number;
  returned: number;
}

/** downloadable is false for today's slips (server clock): preview only until the day is closed. */
export type SlipRow = { store: string; date: string; downloadable: boolean } & Record<Kind, boolean>;

export interface DashboardData {
  codes: string[];
  tiles: {
    collection: number;
    bills: number;
    items_sold: number;
    items_returned: number;
    cancelled: number;
  };
  mop_totals: Record<string, number>;
  collection: CollectionRow[];
  items: ItemRow[];
  today: string;
  slips: SlipRow[];
}

export interface JournalSummary {
  lines: number;
  item_lines: number;
  payment_lines: number;
  receipts: number;
  stores: string[];
}

export interface JournalPage {
  columns: string[];
  text_columns: string[];
  rows: (string | number)[][];
  page: number;
  size: number;
}

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const r = await fetch(url, { signal });
  if (!r.ok) {
    let detail = r.statusText;
    try {
      detail = (await r.json()).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return r.json() as Promise<T>;
}

const query = (f: Filters) => new URLSearchParams({ start: f.start, end: f.end, store: f.store }).toString();

export const api = {
  meta: (signal?: AbortSignal) => getJson<Meta>("/api/meta", signal),
  data: (f: Filters, signal?: AbortSignal) => getJson<DashboardData>(`/api/data?${query(f)}`, signal),

  slipUrl(kind: Kind, store: string, date: string, download = false) {
    const path = [kind, store, date].map(encodeURIComponent).join("/");
    return `/api/slip/${path}${download ? "?download=true" : ""}`;
  },

  async slipText(kind: Kind, store: string, date: string): Promise<string> {
    const r = await fetch(api.slipUrl(kind, store, date));
    if (!r.ok) throw new Error((await r.json()).detail ?? r.statusText);
    return r.text();
  },

  journalSummary: (f: Filters, signal?: AbortSignal) =>
    getJson<JournalSummary>(`/api/reports/electronic-journal/summary?start=${f.start}&end=${f.end}`, signal),
  journalRows: (f: Filters, page: number, size: number, signal?: AbortSignal) =>
    getJson<JournalPage>(
      `/api/reports/electronic-journal/rows?start=${f.start}&end=${f.end}&page=${page}&size=${size}`,
      signal,
    ),
  journalXlsxUrl: (f: Filters) => `/api/reports/electronic-journal.xlsx?start=${f.start}&end=${f.end}`,

  zipUrl: (f: Filters, kinds: Kind[]) => `/api/slips.zip?${query(f)}&kinds=${kinds.join("")}`,
};

/** Trigger a browser download for a same-origin URL. */
export function download(url: string) {
  const a = document.createElement("a");
  a.href = url;
  document.body.append(a);
  a.click();
  a.remove();
}
