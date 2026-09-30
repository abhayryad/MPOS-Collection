"""Store Sale by Hour report, for one store on one day: per sales hour, the number of
transactions (receipts), the sales amount and the average sale per transaction, with a totals
row. On screen and as Excel.

    Sales hour  : hour the receipt was paid - TRANSTIME (HHMMSS) of its last line in
                  dbo.PAYMENT_WISE_TRANSACTIONS (13 = paid 13:00:00 to 13:59:59). A receipt with no
                  payment line falls back to its first item line's time. Each receipt is in one hour.
    Transactions: receipts (RECEIPTID) in that hour
    Sales amount: SUM(NETAMOUNTINCLTAX) of the receipt's lines in dbo.ITEM_WISE_TRANSACTIONS -
                  returns are negative, so this is net of returns (payment tendered includes change)
    Avg amount  : Sales amount / Transactions
Aggregated in SQL, so only one row per hour comes back.
"""
import io

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font

from ..db import read_sql

COLUMNS = [
    ("Sales hour", 12.0),
    ("Number of transactions", 24.0),
    ("Sales amount", 16.0),
    ("Avg sales amount", 18.0),
]


def load(conn, day, store):
    """One row per sales hour for one store on one day: hour, transactions, amount (in hour order)."""
    sql = """
        SELECT r.hour, COUNT(*) AS transactions, SUM(r.amount) AS amount
        FROM (
            SELECT i.RECEIPTID, COALESCE(p.paid_at, i.first_line) / 10000 AS hour, i.amount
            FROM (
                SELECT RECEIPTID, MIN(TRANSTIME) AS first_line, SUM(NETAMOUNTINCLTAX) AS amount
                FROM dbo.ITEM_WISE_TRANSACTIONS
                WHERE TRANSDATE = ? AND LTRIM(RTRIM(STORE)) = ?
                GROUP BY RECEIPTID
            ) i
            LEFT JOIN (
                SELECT RECEIPTID, MAX(TRANSTIME) AS paid_at
                FROM dbo.PAYMENT_WISE_TRANSACTIONS
                WHERE TRANSDATE = ? AND LTRIM(RTRIM(STORE)) = ?
                GROUP BY RECEIPTID
            ) p ON p.RECEIPTID = i.RECEIPTID
        ) r
        WHERE r.hour IS NOT NULL
        GROUP BY r.hour
        ORDER BY r.hour"""
    return build(read_sql(sql, conn, [day, store, day, store]).itertuples(index=False, name=None))


def build(rows):
    """(hour, transactions, amount) tuples -> report rows with averages, plus totals."""
    out = []
    for hour, transactions, amount in rows:
        transactions, amount = int(transactions or 0), round(float(amount or 0), 2)
        out.append({"hour": int(hour), "transactions": transactions, "amount": amount,
                    "avg": round(amount / transactions, 2) if transactions else 0.0})
    return {"rows": out,
            "totals": {"transactions": sum(r["transactions"] for r in out),
                       "amount": round(sum(r["amount"] for r in out), 2)}}


def to_xlsx(report):
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Sale by hour")
    for i, (_, width) in enumerate(COLUMNS):
        ws.column_dimensions[chr(ord("A") + i)].width = width
    ws.freeze_panes = "A2"
    last_col = chr(ord("A") + len(COLUMNS) - 1)
    ws.auto_filter.ref = f"A1:{last_col}{len(report['rows']) + 1}"

    ws.append([name for name, _ in COLUMNS])
    for r in report["rows"]:
        ws.append([r["hour"], r["transactions"], r["amount"], r["avg"]])
    bold = Font(bold=True)
    totals = report["totals"]
    cells = []
    for value in ["Totals", totals["transactions"], totals["amount"], None]:
        cell = WriteOnlyCell(ws, value=value)
        cell.font = bold
        cells.append(cell)
    ws.append(cells)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
