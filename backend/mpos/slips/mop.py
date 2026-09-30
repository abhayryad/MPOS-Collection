"""M slip - mode of payment (MOP) summary per store per day, from PAYMENT_WISE_TRANSACTIONS.

One tab-separated file per store and business date, <STORE>_<YYYY-MM-DD>_M.txt:

    HD22    09-15-2026    CA      +    0.00
    HD22    09-15-2026    CN      +    0.00
    HD22    09-15-2026    CN      -    450.00
    HD22    09-15-2026    UPI     +    450.00

Every file lists every MOP found in the table, with 0.00 when the store had
none that day. CASH is written as CA, CREDITMEMO as CN, any other MOP_TYPE
as it is in the table. CN always gets a '+' and a '-' line; other MOPs get a
'-' line only when they have negative amounts that day. Amounts are shown
unsigned.
"""
import pandas as pd

# DB MOP_TYPE -> code in the summary file; anything not listed is written as-is
MOP_CODES = {
    "CASH": "CA",
    "CREDITMEMO": "CN",
}
# MOPs that always get both a '+' and a '-' line
BOTH_SIGNS = {"CN"}
# AMOUNTCUR = amount actually applied to the bill (AMOUNTTENDERED includes change given back)
AMOUNT_COL = "AMOUNTCUR"


def mop_code(mop):
    return MOP_CODES.get(mop, mop)


def all_mop_codes(conn):
    """Codes of every MOP ever used, so each M slip lists the same MOPs."""
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT LTRIM(RTRIM(MOP_TYPE)) FROM dbo.PAYMENT_WISE_TRANSACTIONS "
                "WHERE MOP_TYPE IS NOT NULL")
    return sorted({mop_code(r[0]) for r in cur.fetchall()})


def build(df, codes):
    """Payment rows -> one row per store / day / MOP code / sign, missing lines as 0.00."""
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
