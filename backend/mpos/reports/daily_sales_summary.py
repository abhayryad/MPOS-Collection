"""Daily Sales Summary report, for one store on one day: one row per counter (terminal), with a
totals row. On screen and as Excel.

    No of bill        : bills in BILL_WISE_ORDER_DATA (ORDERID + BILL_NO), cancelled bills
                        (ENTRYSTATUS = 0) left out; return bills count too
    Gross sale        : SUM(GROSSAMOUNT) of sale bills (NETAMOUNT >= 0)
    Discount          : SUM(TOTALDISCAMOUNT) of sale bills - every discount in one column
    Net amount        : SUM(NETAMOUNT) of sale bills
    No of CN issue    : receipts with a negative CREDITMEMO line in PAYMENT_WISE_TRANSACTIONS
    Credit note issue : those negative CREDITMEMO amounts, shown unsigned
    Credit note applied / redeem : positive CREDITMEMO amounts (the two columns are the same)
    Cash refund       : 0 for now
    Payable amount    : Net amount - Credit note applied
    Rounded amt       : 0 for now
    Net sale          : Net amount - Credit note issue - Cash refund + Rounded amt
Credit note amounts use AMOUNTCUR, as the M slip does (AMOUNTTENDERED includes change given back).
"""
import io

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font

from ..db import read_sql
from ..slips.mop import AMOUNT_COL

# (key, Excel header, column width); the first three are text, the rest numbers
COLUMNS = [
    ("store", "Store", 8.0),
    ("date", "Date", 12.0),
    ("terminal", "CTR.No", 12.0),
    ("bills", "No Of Bill", 11.0),
    ("gross", "Gross Sale", 14.0),
    ("discount", "Discount", 12.0),
    ("net", "NET Amount", 14.0),
    ("cn_count", "No Of Credit Note Issue", 14.0),
    ("cn_issue", "Credit Note Issue", 14.0),
    ("cn_applied", "Credit Note Applied", 14.0),
    ("cn_redeem", "Credit Note Redeem", 14.0),
    ("cash_refund", "Cash Refund", 12.0),
    ("payable", "Payable Amount", 14.0),
    ("rounded", "Rounded Amt", 12.0),
    ("net_sale", "Net Sale", 14.0),
]
NUMBERS = [key for key, _, _ in COLUMNS[3:]]

BILLS_SQL = """
    SELECT b.terminal, COUNT(*) AS bills,
           SUM(CASE WHEN b.NETAMOUNT >= 0 THEN b.GROSSAMOUNT ELSE 0 END) AS gross,
           SUM(CASE WHEN b.NETAMOUNT >= 0 THEN b.TOTALDISCAMOUNT ELSE 0 END) AS discount,
           SUM(CASE WHEN b.NETAMOUNT >= 0 THEN b.NETAMOUNT ELSE 0 END) AS net
    FROM (
        SELECT DISTINCT LTRIM(RTRIM(TERMINAL)) AS terminal, ORDERID, BILL_NO,
               GROSSAMOUNT, TOTALDISCAMOUNT, NETAMOUNT
        FROM dbo.BILL_WISE_ORDER_DATA
        WHERE TRANSDATE = ? AND LTRIM(RTRIM(STORE)) = ? AND ENTRYSTATUS <> 0
    ) b
    GROUP BY b.terminal"""

CREDIT_NOTES_SQL = f"""
    SELECT LTRIM(RTRIM(TERMINAL)) AS terminal,
           COUNT(DISTINCT CASE WHEN {AMOUNT_COL} < 0 THEN RECEIPTID END) AS cn_count,
           -SUM(CASE WHEN {AMOUNT_COL} < 0 THEN {AMOUNT_COL} ELSE 0 END) AS cn_issue,
           SUM(CASE WHEN {AMOUNT_COL} > 0 THEN {AMOUNT_COL} ELSE 0 END) AS cn_applied
    FROM dbo.PAYMENT_WISE_TRANSACTIONS
    WHERE TRANSDATE = ? AND LTRIM(RTRIM(STORE)) = ? AND LTRIM(RTRIM(MOP_TYPE)) = 'CREDITMEMO'
    GROUP BY LTRIM(RTRIM(TERMINAL))"""


def load(conn, day, store):
    bills = read_sql(BILLS_SQL, conn, [day, store]).to_dict("records")
    credit_notes = read_sql(CREDIT_NOTES_SQL, conn, [day, store]).to_dict("records")
    return build(day, store, bills, credit_notes)


def build(day, store, bills, credit_notes):
    """Per-terminal bill and credit-note totals -> report rows (by counter) plus a totals row."""
    by_terminal = {}
    for rec in bills + credit_notes:
        row = by_terminal.setdefault(rec["terminal"] or "", {})
        for key, value in rec.items():
            if key != "terminal":
                row[key] = float(value or 0)

    rows = []
    for terminal in sorted(by_terminal):
        v = by_terminal[terminal]
        net, cn_issue, cn_applied = v.get("net", 0.0), v.get("cn_issue", 0.0), v.get("cn_applied", 0.0)
        cash_refund = rounded = 0.0
        row = {
            "store": store, "date": day, "terminal": terminal,
            "bills": int(v.get("bills", 0)),
            "gross": v.get("gross", 0.0), "discount": v.get("discount", 0.0), "net": net,
            "cn_count": int(v.get("cn_count", 0)), "cn_issue": cn_issue,
            "cn_applied": cn_applied, "cn_redeem": cn_applied,
            "cash_refund": cash_refund,
            "payable": net - cn_applied,
            "rounded": rounded,
            "net_sale": net - cn_issue - cash_refund + rounded,
        }
        rows.append({k: round(x, 2) if isinstance(x, float) else x for k, x in row.items()})

    totals = {k: round(sum(r[k] for r in rows), 2) for k in NUMBERS}
    totals["bills"], totals["cn_count"] = int(totals["bills"]), int(totals["cn_count"])
    return {"columns": [{"key": k, "label": label} for k, label, _ in COLUMNS], "rows": rows, "totals": totals}


def to_xlsx(report):
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Daily sales summary")
    for i, (_, _, width) in enumerate(COLUMNS):
        ws.column_dimensions[chr(ord("A") + i)].width = width
    ws.freeze_panes = "A2"
    last_col = chr(ord("A") + len(COLUMNS) - 1)
    ws.auto_filter.ref = f"A1:{last_col}{len(report['rows']) + 1}"

    ws.append([label for _, label, _ in COLUMNS])
    for r in report["rows"]:
        ws.append([r[key] for key, _, _ in COLUMNS])
    bold = Font(bold=True)
    cells = []
    for value in ["Totals", None, None] + [report["totals"][k] for k in NUMBERS]:
        cell = WriteOnlyCell(ws, value=value)
        cell.font = bold
        cells.append(cell)
    ws.append(cells)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
