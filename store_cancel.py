"""Cancelled-bill file per store per day from BILL_WISE_ORDER_DATA.

PROVISIONAL: the real _C format has not been specified yet. Assumptions,
all kept at the top of this file so they are easy to change:
  - a bill is cancelled when ENTRYSTATUS = 0 (IS_CANCELLED below)
  - one line per bill (exact duplicate ORDERID + BILL_NO rows written once)
  - layout follows the _S file: a header line, then one '2' line per bill

Writes one tab-separated file per store and business date into output/,
named <STORE>_<YYYY-MM-DD>_C.txt:

    1   DH24     20260821   *  *  *  *  *
    2   332121   123555     -  7  +  1058.5  -  DISC  75

    python store_cancel.py                     -> every store / date
    python store_cancel.py 2026-08-21          -> one date, all stores
    python store_cancel.py 2026-08-21 DH24     -> one date, one store
"""
import os
import sys

from db_v2 import read_table
from store_sales import num

# SQL condition that marks a bill as cancelled
IS_CANCELLED = "ENTRYSTATUS = 0"
HEADER_STARS = 5
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")


def bill_line(r):
    return "\t".join([
        "2", r.BILL_NO, r.ORDERID, "-", num(r.NUMBEROFITEMS), "+", num(r.NETAMOUNT),
        "-", "DISC", num(r.TOTALDISCAMOUNT),
    ])


def prepare(df):
    """Cancelled bills only (mirrors IS_CANCELLED), trimmed and de-duplicated."""
    df = df[df["ENTRYSTATUS"] == 0].copy()
    for c in ("STORE", "BILL_NO", "ORDERID"):
        df[c] = df[c].fillna("").astype(str).str.strip()
    return (df.drop_duplicates(["STORE", "ORDERID", "BILL_NO"])
              .sort_values(["STORE", "TRANSDATE", "TRANSTIME", "BILL_NO"]))


def render(df):
    """{(store, 'YYYY-MM-DD'): slip text} for rows returned by prepare()."""
    slips = {}
    for (store, day), grp in df.groupby(["STORE", "TRANSDATE"]):
        day = str(day)[:10]  # YYYY-MM-DD
        header = "\t".join(["1", store, day.replace("-", "")] + ["*"] * HEADER_STARS)
        slips[(store, day)] = "".join([header + "\n"] + [bill_line(r) + "\n" for r in grp.itertuples()])
    return slips


def write_files(df):
    os.makedirs(OUT_DIR, exist_ok=True)
    paths = []
    for (store, day), text in render(prepare(df)).items():
        path = os.path.join(OUT_DIR, f"{store}_{day}_C.txt")
        with open(path, "w", encoding="utf-8", newline="\n") as out:
            out.write(text)
        paths.append(path)
    return paths


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else None
    store = sys.argv[2] if len(sys.argv) > 2 else None
    where, params = [IS_CANCELLED], []
    if date:
        where.append("TRANSDATE = ?")
        params.append(date)
    if store:
        where.append("LTRIM(RTRIM(STORE)) = ?")
        params.append(store)
    df = read_table("BILL", where=" AND ".join(where), params=params)
    if df.empty:
        sys.exit("No cancelled bills found for that filter.")
    paths = write_files(df)
    print(f"Wrote {len(paths)} file(s) to {OUT_DIR}")
    for p in paths:
        print("  " + os.path.basename(p))


if __name__ == "__main__":
    main()
