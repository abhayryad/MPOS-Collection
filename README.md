# MPOS Collection

Internal web app over the **datav2** database on **server 28** (SQL Server).

- **Sale Posting Download** – collection dashboard and the posting slips per store and day:
  **M** mode of payment, **S** sale, **R** return, **C** cancel. Preview any slip; download one
  `.txt` or all of a filter as a ZIP. Today's slips are preview-only until the day is closed.
- **Reports → Electronic General** – every item and payment line (all stores or one) with
  product name, store name and shift; on-screen preview (100 rows per page) and Excel download.

Slips and reports are built from the database **on every request**; nothing is stored.

## Login and users

Everyone logs in. Site users are **not** Snowflake users – they are rows in
`V2RETAIL.BRONZE.MPOS_USERS`; every login, failed login, logout, password change and admin
change is written to `V2RETAIL.BRONZE.MPOS_AUTH_HISTORY` (both created on first use from
`sql/snowflake/auth_tables.sql`). The site reaches Snowflake with the `SNOWFLAKE_*` settings in `.env` – key-pair login
(`SNOWFLAKE_PRIVATE_KEY_PATH`, plus `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` if the key is encrypted)
or, failing that, `SNOWFLAKE_PASSWORD`.

- **admin** – built-in account; its password is `MPOS_ADMIN_PASSWORD` in `.env` (whoever knows it is
  admin). Sees every tab plus **Admin → User Management**: add users, edit name and roles,
  reset passwords, activate / deactivate, and the **Activity** log. Works even if Snowflake is down.
- **Roles** give tabs: `SALE_POSTING` → Sale Posting Download, `REPORTS` → Reports
  (defined in `backend/mpos/auth/roles.py` and `frontend/src/App.tsx`).
- **Location** limits which stores a user sees: `HO` = all stores, otherwise one or more store
  codes picked from the active stores in Snowflake `V2RETAIL.GOLD.STORE_PLANT_MASTER`
  (`ST_TYP = 'STORE'`, `ST_STAT = 'ACT'`). Enforced on the server for the dashboard, slips, ZIP,
  report preview and Excel (`backend/mpos/auth/locations.py`); admin is always `HO`.
- New users and password resets start with the default password (`MPOS_DEFAULT_PASSWORD` in `.env`).
  Asking the user to change it at first login is optional (a checkbox, off by default).
- 5 failed logins within 15 minutes lock that username for 15 minutes. Sessions last 8 hours.
- Passwords are stored only as scrypt hashes. If Snowflake is unreachable, history events are
  kept in `output/auth_events_fallback.jsonl` instead of being lost.

## Setup

Requirements: Python 3.10+, Node 20+, *ODBC Driver 18 for SQL Server*, network access to 192.168.151.28.

```bat
copy .env.example .env                 & rem then set SQL28_PWD in .env
pip install -r backend\requirements.txt
cd frontend && npm install && npm run build
```

`.env` holds the database password and is git-ignored – never commit it.

## Run

| What | Command |
|---|---|
| Serve on the LAN (port 8028) | `start_lan.bat` |
| Serve locally with auto-reload | `python -m uvicorn mpos.main:app --app-dir backend --port 8028 --reload` |
| Frontend dev server (hot reload, proxies `/api` to :8028) | `npm --prefix frontend run dev` → http://localhost:5173 |
| Rebuild the frontend after changes | `npm --prefix frontend run build` (served immediately, no restart) |
| Tests | `python -m unittest discover -s backend/tests -t backend` |

After changing backend code, restart the server (or use `--reload`).

### Command line

```bat
run_cli export-slips                                 & rem all dates, all stores, M S R C -> output\
run_cli export-slips --date 2026-09-28
run_cli export-slips --from 2026-09-01 --to 2026-09-28 --store HD22 --kinds SR
run_cli check-db                                     & rem test the connection, show table sizes
run_cli payment-mop                                  & rem payment listing with totals per store / MOP
```

`export-slips` writes exactly what the website serves. Unlike the website it does not hold back today's slips.

## Layout

```
backend/
  mpos/
    config.py              settings (server, database, user, paths) from .env
    db.py                  connections and table reads
    slips/
      mop.py               M slip format
      sales.py             S and R slip format
      cancel.py            C slip format (provisional - format not yet specified)
      common.py            shared helpers (number format, header line)
      service.py           load source rows, build every slip, today's-slip rule
    reports/
      electronic_journal.py  Electronic General query, preview paging, Excel export
    auth/                  login: passwords.py, sessions.py, roles.py, store.py (Snowflake), deps.py
    api/                   HTTP endpoints: auth, admin, dashboard, slips, reports
    main.py                FastAPI app; serves the built frontend
    cli.py                 command-line tools (python -m mpos ...)
  tests/                   exact-output tests for slip formats and the Excel layout
  requirements.txt
sql/
  views/VW_ELECTRONIC_JOURNAL.sql   view behind the Electronic General report (SQL Server 28)
  snowflake/auth_tables.sql         users + authentication history tables (Snowflake)
frontend/                  React + TypeScript + Vite
  src/
    pages/                 SalePostingDownload.tsx, Reports.tsx, Admin.tsx, Login.tsx
    components/            charts, filters, slip table and preview, report preview
    lib/                   api.ts (typed API client), format.ts, hooks.ts
output/                    files written by the command line (git-ignored)
```

## Business rules – where they live

| Rule | Place |
|---|---|
| MOP codes (CASH→CA, CREDITMEMO→CN), which MOPs get +/− lines, amount column | `backend/mpos/slips/mop.py` |
| S/R line layout, number of `*` in the header | `backend/mpos/slips/sales.py` |
| What counts as a cancelled bill (`ENTRYSTATUS = 0`), C layout | `backend/mpos/slips/cancel.py` |
| Today's slips not downloadable | `backend/mpos/slips/service.py` → `downloadable()` |
| Electronic General columns and joins (DIM_PRODUCT, STORE_PLANT_MASTER) | `sql/views/VW_ELECTRONIC_JOURNAL.sql` |
| Electronic General column order and Excel widths | `backend/mpos/reports/electronic_journal.py` |

The report uses `dbo.VW_ELECTRONIC_JOURNAL` when the view exists with every report column;
otherwise it runs the SELECT from the `.sql` file directly. After editing the `.sql` file,
run it on datav2 (`CREATE OR ALTER VIEW`) to update the view.

Changing a slip format? Update the matching test in `backend/tests/test_slips.py` in the same change.
