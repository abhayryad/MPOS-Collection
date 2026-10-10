"""M slip - mode of payment (MOP) summary per store per day, from PAYMENT_WISE_TRANSACTIONS.

One tab-separated file per store and business date, <STORE>_<YYYY-MM-DD>_M.txt:

    HD22<TAB>09-15-2026<TAB>CN<TAB><TAB>-<TAB>450.00<TAB>
    HD22<TAB>09-15-2026<TAB>PUPI<TAB><TAB>+<TAB>450.00<TAB>

Two tabs after the MOP code and a tab at the end of every line, matching the
POS files, so columns stay aligned however long the code or amount is.

Only MOPs the store actually used that day are listed; no 0.00 lines. CASH is
written as CA, CREDITMEMO as CN, UPI as PUPI, any other MOP_TYPE as it is in the
table. Each MOP gets a '+' line for its positive amounts and a '-' line for its
negative amounts, whichever it has. Amounts are shown unsigned.
"""
# DB MOP_TYPE -> code in the summary file; anything not listed is written as-is
MOP_CODES = {
    "CASH": "CA",
    "CREDITMEMO": "CN",
    "UPI": "PUPI",
}
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


def build(df):
    """Payment rows -> one row per store / day / MOP code / sign actually used."""
    df = df.copy()
    for c in ("STORE", "MOP_TYPE"):
        df[c] = df[c].str.strip()
    df["CODE"] = df["MOP_TYPE"].map(mop_code)
    df["AMOUNT"] = df[AMOUNT_COL].astype(float)
    df["SIGN"] = df["AMOUNT"].map(lambda a: "-" if a < 0 else "+")
    df = df[df["AMOUNT"] != 0]
    summary = df.groupby(["STORE", "TRANSDATE", "CODE", "SIGN"])["AMOUNT"].sum().abs().reset_index()
    return summary[summary["AMOUNT"].round(2) != 0].sort_values(["STORE", "TRANSDATE", "CODE", "SIGN"])


def render(summary):
    """{(store, 'YYYY-MM-DD'): slip text} for the output of build()."""
    slips = {}
    for (store, day), grp in summary.groupby(["STORE", "TRANSDATE"]):
        day = str(day)[:10]  # YYYY-MM-DD
        mdy = f"{day[5:7]}-{day[8:10]}-{day[:4]}"
        slips[(store, day)] = "".join(
            f"{store}\t{mdy}\t{r.CODE}\t\t{r.SIGN}\t{r.AMOUNT:.2f}\t\n" for r in grp.itertuples())
    return slips
