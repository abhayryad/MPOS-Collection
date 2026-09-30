"""Mode of payment (MOP) summary per store per day from PAYMENT_WISE_TRANSACTIONS.

Writes one tab-separated file per store and business date into output/,
named <STORE>_<YYYY-MM-DD>_M.txt, e.g.

    HD22    09-15-2026    CA      +    0.00
    HD22    09-15-2026    CN      +    0.00
    HD22    09-15-2026    CN      -    450.00
    HD22    09-15-2026    UPI     +    450.00

Every file lists every MOP found in the table, with 0.00 when the store had
none that day. CASH is written as CA, CREDITMEMO as CN, any other MOP_TYPE
as it is in the table. CN always gets a '+' and a '-' line; other MOPs get a
'-' line only when they have negative amounts that day. Amounts are shown
unsigned.

    python store_summary.py                     -> every store / date
    python store_summary.py 2026-09-28          -> one date, all stores
    python store_summary.py 2026-09-28 HD22     -> one date, one store
"""
import os
import sys

import pandas as pd

from db_v2 import connect, read_table

# DB MOP_TYPE -> code in the summary file; anything not listed is written as-is
MOP_CODES = {
    "CASH": "CA",
    "CREDITMEMO": "CN",
}
# MOPs that always get both a '+' and a '-' line
BOTH_SIGNS = {"CN"}
# AMOUNTCUR = amount actually applied to the bill (AMOUNTTENDERED includes change given back)
AMOUNT_COL = "AMOUNTCUR"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")


def mop_code(mop):
    return MOP_CODES.get(mop, mop)


def all_mop_codes(conn):
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT LTRIM(RTRIM(MOP_TYPE)) FROM dbo.PAYMENT_WISE_TRANSACTIONS "
                "WHERE MOP_TYPE IS NOT NULL")
    return sorted({mop_code(r[0]) for r in cur.fetchall()})


def build(df, codes):
    df = df.copy()
    for c in ("STORE", "MOP_TYPE"):
        df[c] = df[c].str.strip()
    df["CODE"] = df["MOP_TYPE"].map(mop_code)
    df["AMOUNT"] = df[AMOUNT_COL].astype(float)
    df["SIGN"] = df["AMOUNT"].map(lambda a: "-" if a < 0 else "+")
    df = df[df["AMOUNT"] != 0]
    summary = df.groupby(["STORE", "TRANSDATE", "CODE", "SIGN"])["AMOUNT"].sum().abs().reset_index()

    # Every store/day gets every MOP ('+', plus '-' for BOTH_SIGNS); missing lines are 0.00
    lines = [(c, "+") for c in codes] + [(c, "-") for c in codes if c in BOTH_SIGNS]
    days = df[["STORE", "TRANSDATE"]].drop_duplicates()
    grid = days.merge(pd.DataFrame(lines, columns=["CODE", "SIGN"]), how="cross")
    return (grid.merge(summary, how="outer", on=["STORE", "TRANSDATE", "CODE", "SIGN"])
                .fillna({"AMOUNT": 0.0})
                .sort_values(["STORE", "TRANSDATE", "CODE", "SIGN"]))


def render(summary):
    """{(store, 'YYYY-MM-DD'): slip text} for the output of build()."""
    slips = {}
    for (store, day), grp in summary.groupby(["STORE", "TRANSDATE"]):
        day = str(day)[:10]  # YYYY-MM-DD
        mdy = f"{day[5:7]}-{day[8:10]}-{day[:4]}"
        slips[(store, day)] = "".join(
            f"{store}\t{mdy}\t{r.CODE}\t{r.SIGN}\t{r.AMOUNT:.2f}\n" for r in grp.itertuples())
    return slips


def write_files(summary):
    os.makedirs(OUT_DIR, exist_ok=True)
    paths = []
    for (store, day), text in render(summary).items():
        path = os.path.join(OUT_DIR, f"{store}_{day}_M.txt")
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
    with connect() as conn:
        codes = all_mop_codes(conn)
        df = read_table("PAYMENT", where=" AND ".join(where) or None, params=params, conn=conn)
    if df.empty:
        sys.exit("No payment rows found for that filter.")
    paths = write_files(build(df, codes))
    print(f"Wrote {len(paths)} file(s) to {OUT_DIR}")
    for p in paths:
        print("  " + os.path.basename(p))


if __name__ == "__main__":
    main()
