"""Excel layout and summary tests for the Electronic General report (no database needed)."""
import io
import unittest
from datetime import date, datetime

import openpyxl
import pandas as pd

from mpos.reports import electronic_journal as ej

NAMES = [name for name, _ in ej.COLUMNS]


def journal(*rows):
    """Rows as the view returns them: report columns plus Store / Line number / SortLine."""
    base = {name: None for name in NAMES}
    out = []
    for r in rows:
        row = {**base, **r}
        row.setdefault("Store", row["Store code"])
        row.setdefault("Line number", 1)
        row.setdefault("SortLine", 1 if row["Line type"] == "Sales" else 2)
        out.append(row)
    return pd.DataFrame(out)


SALE = {"Transaction date": date(2026, 9, 29), "Transaction time": "13:47:00",
        "Cash register number": "Terminal 03", "Transaction type": "Sales",
        "Transaction number": "TXN1", "Receipt number": "HD2229-1", "Line type": "Sales",
        "Item number": "1130126010001", "Product name": "KI_B_SUIT_SL", "Quantity": 1, "Price": 450,
        "Tax amount": 21.43, "Cash discount amount": 0, "Staff": "V42801", "Net amount": 450,
        "Payment method": None, "Tendered": 0, "Store code": "HD22", "Store name": "KAPASHERA",
        "Shift": "SHIFT01"}
PAYMENT = {**SALE, "Line type": "Payment", "Item number": None, "Product name": None, "Quantity": 0,
           "Price": 0, "Tax amount": 0, "Net amount": 0, "Payment method": "CASH", "Tendered": 500}


class XlsxTest(unittest.TestCase):
    def setUp(self):
        wb = openpyxl.load_workbook(io.BytesIO(ej.to_xlsx(journal(SALE, PAYMENT))))
        self.ws = wb.active

    def test_sheet_layout(self):
        self.assertEqual(self.ws.title, "Electronic journal")
        self.assertEqual(self.ws.freeze_panes, "A2")
        self.assertEqual(self.ws.auto_filter.ref, "A1:T3")
        self.assertEqual([c.value for c in self.ws[1]], NAMES)

    def test_values_and_formats(self):
        sale = [c.value for c in self.ws[2]]
        pay = [c.value for c in self.ws[3]]
        self.assertEqual(sale[0], datetime(2026, 9, 29))
        self.assertEqual(self.ws["A2"].number_format, "dd/MM/yyyy")
        self.assertEqual(sale[NAMES.index("Item number")], "1130126010001")  # stays text, as stored
        self.assertEqual(pay[NAMES.index("Tendered")], 500)
        self.assertIn(pay[NAMES.index("Item number")], ("", None))  # blank text cell on payment lines


class SummaryTest(unittest.TestCase):
    def test_counts(self):
        s = ej.summary(journal(SALE, PAYMENT, {**SALE, "Receipt number": "HD2229-2"}))
        self.assertEqual(s, {"lines": 3, "item_lines": 2, "payment_lines": 1, "receipts": 2, "stores": ["HD22"]})


if __name__ == "__main__":
    unittest.main()
