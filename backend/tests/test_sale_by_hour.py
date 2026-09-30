"""Totals, averages and Excel layout for the Store Sale by Hour report (no database needed)."""
import io
import unittest

import openpyxl

from mpos.reports import sale_by_hour as sbh

HOURS = [(10, 1, 1459), (11, 9, 6850), (12, 46, 15794), (13, 87, 42592), (14, 97, 53752), (15, 66, 49489)]


class BuildTest(unittest.TestCase):
    def test_matches_reference_sheet(self):
        report = sbh.build(HOURS)
        self.assertEqual([r["avg"] for r in report["rows"]], [1459, 761.11, 343.35, 489.56, 554.14, 749.83])
        self.assertEqual(report["totals"], {"transactions": 306, "amount": 169936})

    def test_zero_transactions_and_nulls(self):
        report = sbh.build([(9, 0, None)])
        self.assertEqual(report["rows"], [{"hour": 9, "transactions": 0, "amount": 0.0, "avg": 0.0}])


class XlsxTest(unittest.TestCase):
    def test_layout(self):
        ws = openpyxl.load_workbook(io.BytesIO(sbh.to_xlsx(sbh.build(HOURS)))).active
        rows = list(ws.iter_rows(values_only=True))
        self.assertEqual(rows[0], ("Sales hour", "Number of transactions", "Sales amount", "Avg sales amount"))
        self.assertEqual(rows[1], (10, 1, 1459, 1459))
        self.assertEqual(rows[-1], ("Totals", 306, 169936, None))
        self.assertEqual(ws.freeze_panes, "A2")


if __name__ == "__main__":
    unittest.main()
