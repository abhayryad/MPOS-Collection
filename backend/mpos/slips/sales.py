"""S and R slips - item-level sales and returns per store per day, from ITEM_WISE_TRANSACTIONS.

<STORE>_<YYYY-MM-DD>_S.txt holds sales (QTY >= 0), <STORE>_<YYYY-MM-DD>_R.txt returns
(QTY < 0). Both have the same tab-separated layout:

    1   HD22       20260928  *  *  *  ...
    2   84051491   -  1  +  450  -  ZPR1  0  -  ZPR2  0  -  ZPR3  0

Line 1 is the header (store, date as YYYYMMDD, then '*' fillers). Every
other line is one item: barcode, qty, price, and DISCAMOUNT after ZPR3.
In the return file qty and amounts are written without the minus sign.
"""
from .common import header_line, num

HEADER_STARS = 13


def item_line(r):
    return "\t".join([
        "2", r.BARCODE, "-", num(r.QTY), "+", num(r.PRICE),
        "-", "ZPR1", "0", "-", "ZPR2", "0", "-", "ZPR3", num(r.DISCAMOUNT),
    ])


def prepare(df):
    df = df.copy()
    df["STORE"] = df["STORE"].str.strip()
    df["BARCODE"] = df["BARCODE"].fillna("").str.strip()
    return df


def render(df):
    """{(store, 'YYYY-MM-DD'): slip text}; pass sale rows for _S or return rows for _R."""
    df = df.sort_values(["STORE", "TRANSDATE", "TRANSACTIONID", "LINENUM"])
    slips = {}
    for (store, day), grp in df.groupby(["STORE", "TRANSDATE"]):
        day = str(day)[:10]  # YYYY-MM-DD
        header = header_line(store, day, HEADER_STARS)
        slips[(store, day)] = "".join([header + "\n"] + [item_line(r) + "\n" for r in grp.itertuples()])
    return slips
