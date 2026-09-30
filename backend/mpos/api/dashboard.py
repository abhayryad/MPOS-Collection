"""Sale Posting Download page: shared metadata and the dashboard (tiles, charts, slip list)."""
from datetime import date

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..config import settings
from ..db import TABLES, connect
from ..slips import cancel, mop
from ..slips.service import KINDS, build_all, downloadable, load
from .common import check_date

router = APIRouter(prefix="/api")


@router.get("/meta")
def meta():
    with connect() as conn:
        cur = conn.cursor()
        union = " UNION ".join(
            f"SELECT LTRIM(RTRIM(STORE)) AS STORE, MIN(TRANSDATE) AS D0, MAX(TRANSDATE) AS D1 "
            f"FROM dbo.[{t}] GROUP BY LTRIM(RTRIM(STORE))" for t in TABLES.values())
        cur.execute(f"SELECT STORE, MIN(D0), MAX(D1) FROM ({union}) u GROUP BY STORE ORDER BY STORE")
        rows = cur.fetchall()
        codes = mop.all_mop_codes(conn)
    return {
        "server": settings.db_server,
        "database": settings.db_name,
        "stores": [r[0] for r in rows if r[0]],
        "min_date": str(min(r[1] for r in rows))[:10] if rows else None,
        "max_date": str(max(r[2] for r in rows))[:10] if rows else None,
        "mop_codes": codes,
        "kinds": KINDS,
    }


@router.get("/data")
def data(start: str, end: str, store: str = ""):
    start, end = check_date(start, "start"), check_date(end, "end")
    codes, pay, item, bill = load(start, end, store or None)
    slips = build_all(codes, pay, item, bill)

    pay["CODE"] = pay["MOP_TYPE"].fillna("").str.strip().map(mop.mop_code)
    pay["AMOUNT"] = pay[mop.AMOUNT_COL].astype(float)
    item["QTY"] = item["QTY"].astype(float)
    days = sorted(set(pay["TRANSDATE"]) | set(item["TRANSDATE"]) | set(bill["TRANSDATE"]))

    by_mop = pay.groupby(["TRANSDATE", "CODE"])["AMOUNT"].sum()
    collection = [{"date": d, **{c: round(float(by_mop.get((d, c), 0.0)), 2) for c in codes}} for d in days]

    sold = item[item["QTY"] >= 0].groupby("TRANSDATE")["QTY"].sum()
    returned = item[item["QTY"] < 0].groupby("TRANSDATE")["QTY"].sum().abs()
    items = [{"date": d, "sold": float(sold.get(d, 0)), "returned": float(returned.get(d, 0))} for d in days]

    cancelled = cancel.prepare(bill)
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
