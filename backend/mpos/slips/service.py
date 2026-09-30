"""Loading source data and producing every slip type from it. Slips are built on demand
from the database each time; nothing is stored."""
from datetime import date

from ..db import connect, read_table
from . import cancel, mop, sales

KINDS = {
    "M": "Mode of payment",
    "S": "Sale",
    "R": "Return",
    "C": "Cancel",
}
TODAY_BLOCKED = "Today's slips can be downloaded from tomorrow, once the day is closed"


def downloadable(day):
    """Slips can be downloaded only for days that are over (server clock), never today."""
    return day < date.today().isoformat()


def load(start=None, end=None, stores=None):
    """(mop codes, payment, item, bill) rows for a date range (None = all dates) and stores
    (a code, a list of codes, or None = every store)."""
    if isinstance(stores, str):
        stores = [stores]
    where, params = [], []
    if start and end:
        where.append("TRANSDATE BETWEEN ? AND ?")
        params += [start, end]
    if stores is not None:
        if not stores:
            stores = [""]  # no stores -> no rows
        where.append(f"LTRIM(RTRIM(STORE)) IN ({', '.join('?' * len(stores))})")
        params += list(stores)
    where = " AND ".join(where) or None
    with connect() as conn:
        codes = mop.all_mop_codes(conn)
        pay = read_table("PAYMENT", where=where, params=params, conn=conn)
        item = read_table("ITEM", where=where, params=params, conn=conn)
        bill = read_table("BILL", where=where, params=params, conn=conn)
    for df in (pay, item, bill):
        df["STORE"] = df["STORE"].fillna("").astype(str).str.strip()
        df["TRANSDATE"] = df["TRANSDATE"].astype(str).str[:10]
    return codes, pay, item, bill


def build_all(codes, pay, item, bill):
    """{kind: {(store, day): text}} for the rows returned by load()."""
    item = sales.prepare(item)
    return {
        "M": mop.render(mop.build(pay, codes)) if not pay.empty else {},
        "S": sales.render(item[item["QTY"] >= 0]),
        "R": sales.render(item[item["QTY"] < 0]),
        "C": cancel.render(cancel.prepare(bill)),
    }
