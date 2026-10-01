"""Daily Sales Summary: per-counter maths and Excel layout, checked against the HW47 29/09 sheet
(no database needed)."""
import io
import unittest

import openpyxl

from mpos.reports import daily_sales_summary as dss

# terminal, bills, gross, discount (manual + campaign), net, CN issued (count, amount), CN applied
SHEET = [
    ("HW4707", 42, 52649, 2567, 50082, 0, 0, 700),
    ("HW4706", 224, 259813, 2411, 257402, 0, 0, 24400),
    ("HW4701", 4, 7287, 844, 6443, 0, 0, 450),
    ("HW4704", 174, 185080, 7712, 177368, 0, 0, 12825),
    ("HW4705", 5, 9421, 76, 9345, 0, 0, 1225),
    ("HW4708", 173, 184355, 4660, 179695, 0, 0, 20825),
    ("HW4703", 213, 217573, 4572, 213001, 0, 0, 27375),
    ("HW47R1", 138, 0, 0, 0, 138, 104625, 0),
    ("HW4702", 113, 127647, 3412, 124235, 0, 0, 13725),
]


def report():
    bills = [{"terminal": t, "bills": b, "gross": g, "discount": d, "net": n} for t, b, g, d, n, *_ in SHEET]
    cns = [{"terminal": t, "cn_count": c, "cn_issue": i, "cn_applied": a}
           for t, *_, c, i, a in SHEET if c or a]
    return dss.build("2026-09-29", "HW47", bills, cns)


class BuildTest(unittest.TestCase):
    def test_totals_match_sheet(self):
        t = report()["totals"]
        self.assertEqual(t["bills"], 1086)
        self.assertEqual(t["gross"], 1043825)
        self.assertEqual(t["discount"], 24773 + 1481)
        self.assertEqual(t["net"], 1017571)
        self.assertEqual((t["cn_count"], t["cn_issue"]), (138, 104625))
        self.assertEqual(t["cn_applied"], 101525)
        self.assertEqual(t["cn_redeem"], 101525)
        self.assertEqual(t["payable"], 916046)
        self.assertEqual(t["net_sale"], 912946)

    def test_return_counter_row(self):
        r1 = next(r for r in report()["rows"] if r["terminal"] == "HW47R1")
        self.assertEqual((r1["bills"], r1["net"], r1["payable"], r1["net_sale"]), (138, 0, 0, -104625))

    def test_counter_with_credit_notes_but_no_bills(self):
        rows = dss.build("2026-09-29", "HW47", [], [{"terminal": "HW47R2", "cn_count": 1, "cn_issue": 500,
                                                    "cn_applied": 0}])["rows"]
        self.assertEqual((rows[0]["bills"], rows[0]["net_sale"]), (0, -500))


class XlsxTest(unittest.TestCase):
    def test_layout(self):
        ws = openpyxl.load_workbook(io.BytesIO(dss.to_xlsx(report()))).active
        rows = list(ws.iter_rows(values_only=True))
        self.assertEqual(rows[0][:4], ("Store", "Date", "CTR.No", "No Of Bill"))
        self.assertEqual(rows[0][-1], "Net Sale")
        self.assertEqual(rows[-1][0], "Totals")
        self.assertEqual(rows[-1][-1], 912946)
        self.assertEqual(len(rows), len(SHEET) + 2)


if __name__ == "__main__":
    unittest.main()
