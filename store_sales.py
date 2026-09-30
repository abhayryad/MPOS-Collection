"""Item-level sales and return files per store per day from ITEM_WISE_TRANSACTIONS.

Writes tab-separated files per store and business date into output/:
<STORE>_<YYYY-MM-DD>_S.txt for sales (QTY >= 0) and <STORE>_<YYYY-MM-DD>_R.txt
for returns (QTY < 0). Both have the same layout:

    1   HD22       20260928  *  *  *  ...
    2   84051491   -  1  +  450  -  ZPR1  0  -  ZPR2  0  -  ZPR3  0

Line 1 is the header (store, date as YYYYMMDD, then '*' fillers). Every
other line is one item: barcode, qty, price, and DISCAMOUNT after ZPR3.
In the return file qty and amounts are written without the minus sign.

    python store_sales.py                     -> every store / date
    python store_sales.py 2026-09-28          -> one date, all stores
    python store_sales.py 2026-09-28 HD22     -> one date, one store
"""
import os
import sys

from db_v2 import read_table

HEADER_STARS = 13
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")


def num(v):
    """450.0 -> '450', 324.50 -> '324.5', None -> '0'."""
    v = abs(float(v or 0))
    return f"{v:.2f}".rstrip("0").rstrip(".") or "0"


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
        header = "\t".join(["1", store, day.replace("-", "")] + ["*"] * HEADER_STARS)
        slips[(store, day)] = "".join([header + "\n"] + [item_line(r) + "\n" for r in grp.itertuples()])
    return slips


def write_files(df, suffix):
    os.makedirs(OUT_DIR, exist_ok=True)
    paths = []
    for (store, day), text in render(df).items():
        path = os.path.join(OUT_DIR, f"{store}_{day}_{suffix}.txt")
        with open(path, "w", encoding="utf-8", newline="\n") as out:
            out.write(text)
        paths.append(path)
    return paths


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else None
    store = sys.argv[2] if len(sys.argv) > 2 else None
    where, params = [], []
    if date:
        where.append("TRANSDATE = ?")
        params.append(date)
    if store:
        where.append("LTRIM(RTRIM(STORE)) = ?")
        params.append(store)
    df = read_table("ITEM", where=" AND ".join(where) or None, params=params)
    if df.empty:
        sys.exit("No item rows found for that filter.")
    df = prepare(df)
    paths = write_files(df[df["QTY"] >= 0], "S") + write_files(df[df["QTY"] < 0], "R")
    print(f"Wrote {len(paths)} file(s) to {OUT_DIR}")
    for p in paths:
        print("  " + os.path.basename(p))


if __name__ == "__main__":
    main()
