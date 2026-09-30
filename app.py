"""POS Collection - local web app over datav2 on server 28.

Shows collection / item / cancellation visuals for a date range and store,
and serves the M (mode of payment), S (sale), R (return) and C (cancel) slips
as single .txt downloads or one ZIP.

    cd frontend && npm install && npm run build   (once, and after frontend changes)
    python -m uvicorn app:app --port 8028          -> http://localhost:8028
"""
import io
import os
import zipfile
from datetime import date

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import electronic_journal
import store_cancel
import store_sales
import store_summary
from db_v2 import SERVER, DATABASE, TABLES, connect, read_table

HERE = os.path.dirname(os.path.abspath(__file__))
KINDS = {
    "M": "Mode of payment",
    "S": "Sale",
    "R": "Return",
    "C": "Cancel",
}

app = FastAPI(title="POS Collection")


def load(start, end, store=None):
    """Read the three source tables for a date range (and optionally one store)."""
    where, params = ["TRANSDATE BETWEEN ? AND ?"], [start, end]
    if store:
        where.append("LTRIM(RTRIM(STORE)) = ?")
        params.append(store)
    where = " AND ".join(where)
    with connect() as conn:
        codes = store_summary.all_mop_codes(conn)
        pay = read_table("PAYMENT", where=where, params=params, conn=conn)
        item = read_table("ITEM", where=where, params=params, conn=conn)
        bill = read_table("BILL", where=where, params=params, conn=conn)
    for df in (pay, item, bill):
        df["STORE"] = df["STORE"].fillna("").astype(str).str.strip()
        df["TRANSDATE"] = df["TRANSDATE"].astype(str).str[:10]
    return codes, pay, item, bill


def slips_for(codes, pay, item, bill):
    """{kind: {(store, day): text}} - same text the command-line scripts write."""
    item = store_sales.prepare(item)
    return {
        "M": store_summary.render(store_summary.build(pay, codes)) if not pay.empty else {},
        "S": store_sales.render(item[item["QTY"] >= 0]),
        "R": store_sales.render(item[item["QTY"] < 0]),
        "C": store_cancel.render(store_cancel.prepare(bill)),
    }


def downloadable(day):
    """Slips can be downloaded only for days that are over (server clock), never today."""
    return day < date.today().isoformat()


TODAY_BLOCKED = "Today's slips can be downloaded from tomorrow, once the day is closed"


def check_date(value, name):
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise HTTPException(400, f"{name} must be YYYY-MM-DD")


@app.get("/api/meta")
def meta():
    with connect() as conn:
        cur = conn.cursor()
        union = " UNION ".join(
            f"SELECT LTRIM(RTRIM(STORE)) AS STORE, MIN(TRANSDATE) AS D0, MAX(TRANSDATE) AS D1 "
            f"FROM dbo.[{t}] GROUP BY LTRIM(RTRIM(STORE))" for t in TABLES.values())
        cur.execute(f"SELECT STORE, MIN(D0), MAX(D1) FROM ({union}) u GROUP BY STORE ORDER BY STORE")
        rows = cur.fetchall()
        codes = store_summary.all_mop_codes(conn)
    return {
        "server": SERVER,
        "database": DATABASE,
        "stores": [r[0] for r in rows if r[0]],
        "min_date": str(min(r[1] for r in rows))[:10] if rows else None,
        "max_date": str(max(r[2] for r in rows))[:10] if rows else None,
        "mop_codes": codes,
        "kinds": KINDS,
    }


@app.get("/api/data")
def data(start: str, end: str, store: str = ""):
    start, end = check_date(start, "start"), check_date(end, "end")
    codes, pay, item, bill = load(start, end, store or None)
    slips = slips_for(codes, pay, item, bill)

    pay["CODE"] = pay["MOP_TYPE"].fillna("").str.strip().map(store_summary.mop_code)
    pay["AMOUNT"] = pay[store_summary.AMOUNT_COL].astype(float)
    item["QTY"] = item["QTY"].astype(float)
    days = sorted(set(pay["TRANSDATE"]) | set(item["TRANSDATE"]) | set(bill["TRANSDATE"]))

    by_mop = pay.groupby(["TRANSDATE", "CODE"])["AMOUNT"].sum()
    collection = [{"date": d, **{c: round(float(by_mop.get((d, c), 0.0)), 2) for c in codes}} for d in days]

    sold = item[item["QTY"] >= 0].groupby("TRANSDATE")["QTY"].sum()
    returned = item[item["QTY"] < 0].groupby("TRANSDATE")["QTY"].sum().abs()
    items = [{"date": d, "sold": float(sold.get(d, 0)), "returned": float(returned.get(d, 0))} for d in days]

    cancelled = store_cancel.prepare(bill)
    valid_bills = bill[(bill["ENTRYSTATUS"] != 0) & (bill["NETAMOUNT"].astype(float) >= 0)]
    mop_totals = pay.groupby("CODE")["AMOUNT"].sum()

    keys = sorted({k for kind in slips.values() for k in kind}, key=lambda k: (k[1], k[0]), reverse=True)
    return JSONResponse({
        "codes": codes,
        "tiles": {
            "collection": round(float(pay["AMOUNT"].sum()), 2),
            "bills": int(valid_bills.drop_duplicates(["STORE", "ORDERID", "BILL_NO"]).shape[0]),
            "items_sold": float(sold.sum()),
            "items_returned": float(returned.sum()),
            "cancelled": int(len(cancelled)),
        },
        "mop_totals": {c: round(float(mop_totals.get(c, 0.0)), 2) for c in codes},
        "collection": collection,
        "items": items,
        "today": date.today().isoformat(),
        "slips": [{"store": s, "date": d, "downloadable": downloadable(d),
                   **{k: (s, d) in slips[k] for k in KINDS}} for s, d in keys],
    })


@app.get("/api/slip/{kind}/{store}/{day}")
def slip(kind: str, store: str, day: str, download: bool = False):
    kind = kind.upper()
    if kind not in KINDS:
        raise HTTPException(404, "Unknown slip type")
    day = check_date(day, "date")
    if download and not downloadable(day):
        raise HTTPException(403, TODAY_BLOCKED)
    text = slips_for(*load(day, day, store))[kind].get((store, day))
    if text is None:
        raise HTTPException(404, f"No {KINDS[kind].lower()} slip for {store} on {day}")
    headers = {"Content-Disposition": f'attachment; filename="{store}_{day}_{kind}.txt"'} if download else {}
    return Response(text, media_type="text/plain; charset=utf-8", headers=headers)


@app.get("/api/slips.zip")
def slips_zip(start: str, end: str, store: str = "", kinds: str = Query("MSRC")):
    start, end = check_date(start, "start"), check_date(end, "end")
    wanted = [k for k in kinds.upper() if k in KINDS]
    slips = slips_for(*load(start, end, store or None))
    buf = io.BytesIO()
    count = skipped = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for kind in wanted:
            for (s, d), text in sorted(slips[kind].items()):
                if not downloadable(d):
                    skipped += 1
                    continue
                zf.writestr(f"{s}_{d}_{kind}.txt", text)
                count += 1
    if not count:
        raise HTTPException(403 if skipped else 404, TODAY_BLOCKED if skipped else "No slips for that filter")
    name = f"slips_{store or 'ALL'}_{start}_to_{end}.zip"
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/reports/electronic-journal/summary")
def electronic_journal_summary(start: str, end: str):
    start, end = check_date(start, "start"), check_date(end, "end")
    with connect() as conn:
        return electronic_journal.summary(electronic_journal.load(conn, start, end))


@app.get("/api/reports/electronic-journal/rows")
def electronic_journal_rows(start: str, end: str, page: int = Query(1, ge=1), size: int = Query(100, ge=1, le=500)):
    start, end = check_date(start, "start"), check_date(end, "end")
    with connect() as conn:
        data = electronic_journal.load_page(conn, start, end, (page - 1) * size, size)
    return JSONResponse({**data, "page": page, "size": size})


@app.get("/api/reports/electronic-journal.xlsx")
def electronic_journal_xlsx(start: str, end: str):
    start, end = check_date(start, "start"), check_date(end, "end")
    with connect() as conn:
        df = electronic_journal.load(conn, start, end)
    if df.empty:
        raise HTTPException(404, "No transactions for that date range")
    name = f"electronic_journal_{start}" + (f"_to_{end}" if end != start else "") + ".xlsx"
    return Response(electronic_journal.to_xlsx(df),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# React frontend (frontend/dist, built with `npm run build`). Mounted last so /api routes win.
DIST = os.path.join(HERE, "frontend", "dist")


class FrontendFiles(StaticFiles):
    """index.html is always revalidated so a rebuild reaches every browser;
    hashed files under assets/ never change and can be cached for a year."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path.replace("\\", "/").startswith("assets/"):  # Windows passes assets\...
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


if os.path.isdir(DIST):
    app.mount("/", FrontendFiles(directory=DIST, html=True), name="frontend")
else:
    @app.get("/")
    def frontend_missing():
        return Response("Frontend not built. Run:  cd frontend && npm install && npm run build",
                        media_type="text/plain", status_code=503)
