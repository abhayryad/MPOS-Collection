// Typed client for the FastAPI backend (app.py).

export type Kind = "M" | "S" | "R" | "C";

export interface Meta {
  server: string;
  database: string;
  stores: string[];
  min_date: string | null;
  max_date: string | null;
  mop_codes: string[];
  all_stores: boolean; // false = the store list is limited to the user's location
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

export interface SaleByHourRow {
  hour: number;
  transactions: number;
  amount: number;
  avg: number;
}

export interface SaleByHour {
  rows: SaleByHourRow[];
  totals: { transactions: number; amount: number };
}

export interface User {
  username: string;
  full_name: string;
  roles: string[];
  locations: string[]; // ['HO'] = all stores, else store codes
  is_admin: boolean;
  is_active: boolean;
  must_change_password: boolean;
  created_at: string | null;
  created_by: string | null;
  updated_at: string | null;
  updated_by: string | null;
  last_login_at: string | null;
}

/** The logged-in user, plus the names of every assignable role. */
export type Me = User & { role_names: Record<string, string> };

export interface AuthEvent {
  id: number;
  time: string;
  username: string | null;
  actor: string | null;
  event: string;
  detail: string | null;
  ip: string | null;
  user_agent: string | null;
}

export interface StoreInfo {
  code: string;
  name: string;
  zone: string;
  region: string;
  state: string;
}

export interface NewUser {
  username: string;
  full_name: string;
  roles: string[];
  locations: string[];
  password: string;
  must_change_password: boolean;
}

/** Fired when the server says the session is gone (expired, logged out, deactivated). */
export const LOGGED_OUT_EVENT = "mpos:logged-out";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

async function request<T>(method: string, url: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const r = await fetch(url, {
    method,
    signal,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    let detail = r.statusText;
    try {
      detail = (await r.json()).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    if (r.status === 401 && !url.startsWith("/api/auth/")) window.dispatchEvent(new Event(LOGGED_OUT_EVENT));
    throw new ApiError(detail, r.status);
  }
  return r.json() as Promise<T>;
}

const getJson = <T,>(url: string, signal?: AbortSignal) => request<T>("GET", url, undefined, signal);

const query = (f: Filters) => new URLSearchParams({ start: f.start, end: f.end, store: f.store }).toString();
const storeDay = (date: string, store: string) => new URLSearchParams({ date, store }).toString();

export const api = {
  // session
  me: (signal?: AbortSignal) => getJson<Me>("/api/auth/me", signal),
  login: (username: string, password: string) => request<Me>("POST", "/api/auth/login", { username, password }),
  logout: () => request<{ ok: boolean }>("POST", "/api/auth/logout"),
  changePassword: (current_password: string, new_password: string) =>
    request<Me>("POST", "/api/auth/change-password", { current_password, new_password }),

  // admin
  users: (signal?: AbortSignal) => getJson<User[]>("/api/admin/users", signal),
  createUser: (u: NewUser) => request<User>("POST", "/api/admin/users", u),
  updateUser: (username: string, change: Partial<Pick<User, "full_name" | "roles" | "locations" | "is_active">>) =>
    request<User>("PATCH", `/api/admin/users/${encodeURIComponent(username)}`, change),
  resetPassword: (username: string, password: string, must_change_password: boolean) =>
    request<User>("POST", `/api/admin/users/${encodeURIComponent(username)}/reset-password`, {
      password,
      must_change_password,
    }),
  adminDefaults: (signal?: AbortSignal) => getJson<{ default_password: string }>("/api/admin/defaults", signal),
  storeMaster: (signal?: AbortSignal) => getJson<StoreInfo[]>("/api/admin/stores", signal),
  activity: (signal?: AbortSignal) => getJson<AuthEvent[]>("/api/admin/activity?limit=1000", signal),

  meta: (signal?: AbortSignal) => getJson<Meta>("/api/meta", signal),
  data: (f: Filters, signal?: AbortSignal) => getJson<DashboardData>(`/api/data?${query(f)}`, signal),

  slipUrl(kind: Kind, store: string, date: string, download = false) {
    const path = [kind, store, date].map(encodeURIComponent).join("/");
    return `/api/slip/${path}${download ? "?download=true" : ""}`;
  },

  async slipText(kind: Kind, store: string, date: string): Promise<string> {
    const r = await fetch(api.slipUrl(kind, store, date));
    if (r.status === 401) window.dispatchEvent(new Event(LOGGED_OUT_EVENT));
    if (!r.ok) throw new ApiError((await r.json()).detail ?? r.statusText, r.status);
    return r.text();
  },

  journalSummary: (f: Filters, signal?: AbortSignal) =>
    getJson<JournalSummary>(`/api/reports/electronic-journal/summary?${query(f)}`, signal),
  journalRows: (f: Filters, page: number, size: number, signal?: AbortSignal) =>
    getJson<JournalPage>(
      `/api/reports/electronic-journal/rows?${query(f)}&page=${page}&size=${size}`,
      signal,
    ),
  journalXlsxUrl: (f: Filters) => `/api/reports/electronic-journal.xlsx?${query(f)}`,
  saleByHour: (date: string, store: string, signal?: AbortSignal) =>
    getJson<SaleByHour>(`/api/reports/sale-by-hour?${storeDay(date, store)}`, signal),
  saleByHourXlsxUrl: (date: string, store: string) => `/api/reports/sale-by-hour.xlsx?${storeDay(date, store)}`,

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
