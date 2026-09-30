"""C slip - cancelled bills per store per day, from BILL_WISE_ORDER_DATA.

PROVISIONAL: the real _C format has not been specified yet. Assumptions:
  - a bill is cancelled when ENTRYSTATUS = 0 (IS_CANCELLED below)
  - one line per bill (exact duplicate ORDERID + BILL_NO rows written once)
  - layout follows the _S file: a header line, then one '2' line per bill

    1   DH24     20260821   *  *  *  *  *
    2   332121   123555     -  7  +  1058.5  -  DISC  75
"""
from .common import header_line, num

# SQL condition that marks a bill as cancelled (mirrored in prepare())
IS_CANCELLED = "ENTRYSTATUS = 0"
HEADER_STARS = 5


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
        header = header_line(store, day, HEADER_STARS)
        slips[(store, day)] = "".join([header + "\n"] + [bill_line(r) + "\n" for r in grp.itertuples()])
    return slips
