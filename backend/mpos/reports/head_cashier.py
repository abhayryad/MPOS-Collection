"""Head Cashier report, for one store on one day: one row per counter (terminal) and cashier, then
one row per return counter and cashier, and a totals row. On screen and as Excel.

    Return bill       : NETAMOUNT < 0. Its CTR.No is the return counter at the start of the receipt
                        number (HD22R1-26-... -> HD22R1; "RETURN" if the number has no "-"), its
                        Employee ID the cashier who took the return
    Cash sale         : SUM(AMOUNTCUR) of CASH lines in PAYMENT_WISE_TRANSACTIONS
    Credit note       : SUM(AMOUNTCUR) of CREDITMEMO lines - redeemed on sale rows (+),
                        issued on the RETURN row (-)
    PUPI              : SUM(AMOUNTCUR) of UPI lines
    Cash / Credit Note / PUPI Paid : what the cashier declared - 0 for now, there is no declaration
                        source in datav2 for MPOS counters yet
    Other MOPs        : left out (only CASH, UPI and CREDITMEMO are used)
    Net sale          : SUM(NETAMOUNT) of the bills (= Cash + Credit note + PUPI)
    Net sale paid     : Cash Paid + Credit Note Paid + PUPI Paid
    Total sale diff   : Net sale - Net sale paid (+ = short, - = over)
    Gross sale        : SUM(GROSSAMOUNT); negative on the RETURN row
    Discount          : SUM(TOTALDISCAMOUNT) - manual and campaign together, MPOS has no split
    No of bill        : bills in BILL_WISE_ORDER_DATA (ORDERID + BILL_NO), cancelled bills
                        (ENTRYSTATUS = 0) left out; payment lines of cancelled bills too
    Avg ticket size   : Net sale / No of bill
Payment lines are matched to bills on RECEIPTID = BILL_NO.
"""
import io

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font

from ..db import read_sql
from ..slips.mop import AMOUNT_COL

RETURN = "RETURN"  # CTR.No of a return bill whose receipt number has no return counter

# (key, Excel header, column width); the first four are text, the rest numbers
COLUMNS = [
    ("store", "Store", 8.0),
    ("date", "Date", 12.0),
    ("terminal", "CTR.No", 13.0),
    ("cashier", "Employee ID", 13.0),
    ("cash", "Cash Sale", 14.0),
    ("cash_paid", "Cash Paid", 14.0),
    ("credit_note", "Credit Note", 14.0),
    ("credit_note_paid", "Credit Note Paid", 14.0),
    ("pupi", "PUPI", 14.0),
    ("pupi_paid", "PUPI Paid", 14.0),
    ("net", "Net Sale", 14.0),
    ("net_paid", "Net Sale Paid", 14.0),
    ("diff", "Total Sale Diff", 14.0),
    ("gross", "Gross Sale", 14.0),
    ("discount", "Discount", 12.0),
    ("bills", "No Of Bill", 11.0),
    ("avg_ticket", "Average Ticket Size", 14.0),
]
TEXT = 4
NUMBERS = [key for key, _, _ in COLUMNS[TEXT:]]
SUMMED = [k for k in NUMBERS if k != "avg_ticket"]

# Bills of the day, one row each, with the counter (return counter for returns) + cashier they are
# grouped under
_BILLS = f"""
    SELECT DISTINCT rcpt AS bill_no, ORDERID, GROSSAMOUNT, TOTALDISCAMOUNT, NETAMOUNT, ENTRYSTATUS,
           CASE WHEN NETAMOUNT < 0 THEN 1 ELSE 0 END AS is_return,
           CASE WHEN NETAMOUNT >= 0 THEN LTRIM(RTRIM(TERMINAL))
                WHEN CHARINDEX('-', rcpt) > 1 THEN LEFT(rcpt, CHARINDEX('-', rcpt) - 1)
                ELSE '{RETURN}' END AS terminal,
           LTRIM(RTRIM(STAFF)) AS cashier
    FROM (SELECT *, LTRIM(RTRIM(BILL_NO)) AS rcpt FROM dbo.BILL_WISE_ORDER_DATA
          WHERE TRANSDATE = ? AND LTRIM(RTRIM(STORE)) = ?) x"""

BILLS_SQL = f"""
    SELECT b.is_return, b.terminal, b.cashier, COUNT(*) AS bills,
           SUM(b.GROSSAMOUNT) AS gross, SUM(b.TOTALDISCAMOUNT) AS discount, SUM(b.NETAMOUNT) AS net
    FROM ({_BILLS}) b
    WHERE b.ENTRYSTATUS <> 0
    GROUP BY b.is_return, b.terminal, b.cashier"""

TENDERS_SQL = f"""
    SELECT COALESCE(b.is_return, 0) AS is_return, COALESCE(b.terminal, LTRIM(RTRIM(p.TERMINAL))) AS terminal,
           COALESCE(b.cashier, LTRIM(RTRIM(p.STAFF))) AS cashier,
           SUM(CASE WHEN LTRIM(RTRIM(p.MOP_TYPE)) = 'CASH' THEN p.{AMOUNT_COL} ELSE 0 END) AS cash,
           SUM(CASE WHEN LTRIM(RTRIM(p.MOP_TYPE)) = 'CREDITMEMO' THEN p.{AMOUNT_COL} ELSE 0 END) AS credit_note,
           SUM(CASE WHEN LTRIM(RTRIM(p.MOP_TYPE)) = 'UPI' THEN p.{AMOUNT_COL} ELSE 0 END) AS pupi
    FROM dbo.PAYMENT_WISE_TRANSACTIONS p
    LEFT JOIN (SELECT DISTINCT bill_no, is_return, terminal, cashier, ENTRYSTATUS FROM ({_BILLS}) y) b
           ON b.bill_no = LTRIM(RTRIM(p.RECEIPTID))
    WHERE p.TRANSDATE = ? AND LTRIM(RTRIM(p.STORE)) = ? AND COALESCE(b.ENTRYSTATUS, 1) <> 0
    GROUP BY COALESCE(b.is_return, 0), COALESCE(b.terminal, LTRIM(RTRIM(p.TERMINAL))),
             COALESCE(b.cashier, LTRIM(RTRIM(p.STAFF)))"""


def load(conn, day, store):
    bills = read_sql(BILLS_SQL, conn, [day, store]).to_dict("records")
    tenders = read_sql(TENDERS_SQL, conn, [day, store, day, store]).to_dict("records")
    return build(day, store, bills, tenders)


KEY = ("is_return", "terminal", "cashier")


def build(day, store, bills, tenders):
    """Per counter + cashier bill and tender totals -> report rows (return counters last) plus a
    totals row."""
    by_key = {}
    for rec in bills + tenders:
        row = by_key.setdefault((int(rec.get("is_return") or 0), rec["terminal"] or "", rec["cashier"] or ""), {})
        for key, value in rec.items():
            if key not in KEY:
                row[key] = row.get(key, 0.0) + float(value or 0)

    rows = []
    for is_return, terminal, cashier in sorted(by_key):
        v = by_key[(is_return, terminal, cashier)]
        row = {"store": store, "date": day, "terminal": terminal, "cashier": cashier}
        row.update({k: round(v.get(k, 0.0), 2) for k in SUMMED})
        row["net_paid"] = round(row["cash_paid"] + row["credit_note_paid"] + row["pupi_paid"], 2)
        row["diff"] = round(row["net"] - row["net_paid"], 2)
        row["bills"] = int(row["bills"])
        row["avg_ticket"] = round(row["net"] / row["bills"], 2) if row["bills"] else 0.0
        rows.append(row)

    totals = {k: round(sum(r[k] for r in rows), 2) for k in SUMMED}
    totals["bills"] = int(totals["bills"])
    totals["avg_ticket"] = round(totals["net"] / totals["bills"], 2) if totals["bills"] else 0.0
    return {"columns": [{"key": k, "label": label} for k, label, _ in COLUMNS], "rows": rows, "totals": totals}


def to_xlsx(report):
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Head cashier")
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
    for value in ["Totals"] + [None] * (TEXT - 1) + [report["totals"][k] for k in NUMBERS]:
        cell = WriteOnlyCell(ws, value=value)
        cell.font = bold
        cells.append(cell)
    ws.append(cells)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
