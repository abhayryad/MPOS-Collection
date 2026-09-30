"""Electronic General report: item + payment lines (all stores or one), as an Excel file
laid out like the reference sheet (header row, frozen, filtered, dd/MM/yyyy dates).

The query lives in sql/views/VW_ELECTRONIC_JOURNAL.sql. If dbo.VW_ELECTRONIC_JOURNAL exists
with every report column it is used; otherwise the script's SELECT is run directly, so the
report works before the view is created or updated.
"""
import io

import pandas as pd
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell

from ..config import settings
from ..db import read_sql

VIEW = "dbo.VW_ELECTRONIC_JOURNAL"
SQL_FILE = settings.sql_dir / "views" / "VW_ELECTRONIC_JOURNAL.sql"
ORDER = "[Transaction date], [Transaction time], [Transaction number], SortLine, [Line number]"

# Output columns in report order, with the column widths of the reference sheet.
COLUMNS = [
    ("Transaction date", 16.7),
    ("Transaction time", 16.4),
    ("Cash register number", 12.0),
    ("Transaction type", 16.4),
    ("Transaction number", 20.0),
    ("Receipt number", 22.0),
    ("Line type", 9.3),
    ("Item number", 16.4),
    ("Product name", 19.9),
    ("Quantity", 10.0),
    ("Price", 9.0),
    ("Tax amount", 11.6),
    ("Cash discount amount", 12.3),
    ("Staff", 9.0),
    ("Net amount", 12.0),
    ("Payment method", 15.3),
    ("Tendered", 10.1),
    ("Store code", 10.0),
    ("Store name", 18.0),
    ("Shift", 10.0),
]
TEXT = {"Transaction time", "Cash register number", "Transaction type", "Transaction number",
        "Receipt number", "Line type", "Item number", "Product name", "Staff", "Payment method",
        "Store code", "Store name", "Shift"}


def _source(conn):
    """The view when it exists and has every report column; otherwise the SQL file's SELECT
    (e.g. before the view is created, or before it is updated to a newer version of the script)."""
    cur = conn.cursor()
    cur.execute("SELECT name FROM sys.columns WHERE object_id = OBJECT_ID(?)", VIEW)
    view_cols = {r[0] for r in cur.fetchall()}
    if view_cols and view_cols >= {name for name, _ in COLUMNS} | {"Store", "SortLine", "Line number"}:
        return VIEW
    body = SQL_FILE.read_text(encoding="utf-8").split("-- BODY", 1)[1].split("\n", 1)[1]
    return f"({body.strip().rstrip(';')}) j"


def _where(start, end, store):
    """Date range, plus one store when given ("" / None = all stores)."""
    if store:
        return "[Transaction date] BETWEEN ? AND ? AND LTRIM(RTRIM([Store])) = ?", [start, end, store]
    return "[Transaction date] BETWEEN ? AND ?", [start, end]


def load(conn, start, end, store=None):
    where, params = _where(start, end, store)
    sql = f"SELECT * FROM {_source(conn)} WHERE {where} ORDER BY {ORDER}"
    return read_sql(sql, conn, params)


def load_page(conn, start, end, offset, limit, store=None):
    """One page of report rows in report order, for the on-screen preview."""
    where, params = _where(start, end, store)
    sql = f"SELECT * FROM {_source(conn)} WHERE {where} ORDER BY {ORDER} OFFSET ? ROWS FETCH NEXT ? ROWS ONLY"
    df = read_sql(sql, conn, params + [int(offset), int(limit)])
    names = [name for name, _ in COLUMNS]
    rows = []
    for row in df[names].itertuples(index=False, name=None):
        out = []
        for name, value in zip(names, row):
            if pd.isna(value):
                value = "" if name in TEXT else 0
            elif name == "Transaction date":
                value = pd.Timestamp(value).strftime("%d/%m/%Y")
            elif name not in TEXT:
                value = float(value)
            out.append(value)
        rows.append(out)
    return {"columns": names, "text_columns": sorted(TEXT), "rows": rows}


def summary(df):
    return {
        "lines": int(len(df)),
        "item_lines": int((df["Line type"] == "Sales").sum()),
        "payment_lines": int((df["Line type"] == "Payment").sum()),
        "receipts": int(df[["Store", "Receipt number"]].drop_duplicates().shape[0]),
        "stores": sorted(df["Store"].dropna().str.strip().unique().tolist()),
    }


def to_xlsx(df):
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("Electronic journal")
    for i, (_, width) in enumerate(COLUMNS):
        ws.column_dimensions[chr(ord("A") + i)].width = width
    ws.freeze_panes = "A2"
    last_col = chr(ord("A") + len(COLUMNS) - 1)
    ws.auto_filter.ref = f"A1:{last_col}{len(df) + 1}"

    ws.append([name for name, _ in COLUMNS])
    names = [name for name, _ in COLUMNS]
    for row in df[names].itertuples(index=False, name=None):
        cells = []
        for name, value in zip(names, row):
            if pd.isna(value):
                value = "" if name in TEXT else 0
            elif name == "Transaction date":
                value = pd.Timestamp(value).to_pydatetime()
            elif name not in TEXT:
                value = float(value)
            cell = WriteOnlyCell(ws, value=value)
            if name == "Transaction date":
                cell.number_format = "dd/MM/yyyy"
            cells.append(cell)
        ws.append(cells)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
