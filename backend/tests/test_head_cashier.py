"""Head Cashier: per counter + cashier maths and Excel layout, checked against the HW47 head cashier
sheet (no database needed). Paytm / card tenders on the sheet are not reported, so Net Sale there is
more than Cash + Credit Note + PUPI on some counters."""
import io
import unittest

import openpyxl

from mpos.reports import head_cashier as hc

# terminal, cashier, cash, credit note, PUPI, net, gross, discount (manual + campaign), bills
SHEET = [
    ("HW4708", "AX0020", 106576, 20825, 0, 179695, 184355, 4660, 173),
    ("HW4701", "AX0013", 5993, 450, 0, 6443, 7287, 844, 4),
    ("HW4702", "AX0014", 47777, 13725, 0, 124235, 127647, 3412, 113),
    ("HW4703", "AX0015", 146958, 27375, 38293, 213001, 217573, 4572, 213),
    ("HW4704", "AX0016", 123841, 12825, 0, 177368, 185080, 7712, 174),
    ("HW4705", "AX0017", 0, 1225, 8120, 9345, 9421, 76, 5),
    ("HW4706", "AX0018", 166383, 24400, 0, 257402, 259813, 2411, 224),
    ("HW4707", "AX0019", 0, 700, 0, 50082, 52649, 2567, 42),
    ("HW47R1", "AX0033", 0, -104625, 0, -104625, -104625, 0, 138),
]


def report():
    bills = [{"is_return": int(n < 0), "terminal": t, "cashier": c, "bills": b, "gross": g, "discount": d,
              "net": n}
             for t, c, _, _, _, n, g, d, b in SHEET]
    tenders = [{"is_return": int(n < 0), "terminal": t, "cashier": c, "cash": ca, "credit_note": cn, "pupi": u}
               for t, c, ca, cn, u, n, *_ in SHEET]
    return hc.build("2026-09-29", "HW47", bills, tenders)


class BuildTest(unittest.TestCase):
    def test_totals_match_sheet(self):
        t = report()["totals"]
        self.assertEqual(t["cash"], 597528)
        self.assertEqual(t["credit_note"], 101525 - 104625)
        self.assertEqual(t["pupi"], 46413)
        self.assertEqual((t["cash_paid"], t["credit_note_paid"], t["pupi_paid"]), (0, 0, 0))
        self.assertEqual(t["net"], 912946)
        self.assertEqual((t["net_paid"], t["diff"]), (0, 912946))
        self.assertEqual(t["gross"], 939200)
        self.assertEqual(t["bills"], 1086)
        self.assertEqual(t["avg_ticket"], round(912946 / 1086, 2))

    def test_rows_and_average_ticket_match_sheet(self):
        rows = report()["rows"]
        self.assertEqual([r["terminal"] for r in rows][:2], ["HW4701", "HW4702"])
        self.assertEqual((rows[-1]["terminal"], rows[-1]["cashier"]), ("HW47R1", "AX0033"))
        avg = {r["terminal"]: r["avg_ticket"] for r in rows}
        self.assertEqual((avg["HW4701"], avg["HW4702"], avg["HW4708"]), (1610.75, 1099.42, 1038.7))
        self.assertEqual(avg["HW47R1"], -758.15)

    def test_one_row_per_counter_and_cashier(self):
        bills = [{"terminal": "Terminal 01", "cashier": "V20566", "bills": 6, "gross": 1860, "discount": 0, "net": 1860},
                 {"terminal": "Terminal 01", "cashier": "V21513", "bills": 30, "gross": 20650, "discount": 625,
                  "net": 20025}]
        tenders = [{"terminal": "Terminal 01", "cashier": "V21513", "cash": 5070, "credit_note": 0, "pupi": 14955},
                   {"terminal": "Terminal 01", "cashier": "V20566", "cash": 0, "credit_note": 0, "pupi": 1860}]
        rows = hc.build("2026-09-30", "HD22", bills, tenders)["rows"]
        self.assertEqual([(r["cashier"], r["cash"], r["pupi"], r["bills"]) for r in rows],
                         [("V20566", 0, 1860, 6), ("V21513", 5070, 14955, 30)])

    def test_return_rows_come_after_sale_rows(self):
        bills = [{"is_return": 1, "terminal": "HD22R1", "cashier": "V00529", "bills": 1, "gross": -450,
                  "discount": 0, "net": -450},
                 {"is_return": 0, "terminal": "Terminal 02", "cashier": "V04362", "bills": 22, "gross": 20703,
                  "discount": 250, "net": 20453}]
        rows = hc.build("2026-10-11", "HD22", bills, [])["rows"]
        self.assertEqual([(r["terminal"], r["cashier"]) for r in rows],
                         [("Terminal 02", "V04362"), ("HD22R1", "V00529")])

    def test_diff_is_net_sale_minus_paid(self):
        bills = [{"terminal": "T1", "cashier": "X", "bills": 2, "gross": 1000, "discount": 0, "net": 1000}]
        tenders = [{"terminal": "T1", "cashier": "X", "cash": 600, "credit_note": 100, "pupi": 300,
                    "cash_paid": 590, "credit_note_paid": 0, "pupi_paid": 300}]
        row = hc.build("2026-10-11", "HD22", bills, tenders)["rows"][0]
        self.assertEqual((row["net_paid"], row["diff"]), (890, 110))

    def test_tenders_without_bills_have_no_average(self):
        rows = hc.build("2026-09-29", "HW47", [], [{"terminal": "T1", "cashier": "X", "cash": 10,
                                                    "credit_note": 0, "pupi": 0}])["rows"]
        self.assertEqual((rows[0]["bills"], rows[0]["avg_ticket"]), (0, 0.0))


class XlsxTest(unittest.TestCase):
    def test_layout(self):
        ws = openpyxl.load_workbook(io.BytesIO(hc.to_xlsx(report()))).active
        rows = list(ws.iter_rows(values_only=True))
        self.assertEqual(rows[0][4:10], ("Cash Sale", "Cash Paid", "Credit Note", "Credit Note Paid",
                                         "PUPI", "PUPI Paid"))
        self.assertEqual(rows[0][10:15], ("Net Sale", "Net Sale Paid", "Total Sale Diff", "Gross Sale", "Discount"))
        self.assertEqual(rows[0][-1], "Average Ticket Size")
        self.assertEqual(rows[-2][2:4], ("HW47R1", "AX0033"))
        self.assertEqual(rows[-1][0], "Totals")
        self.assertEqual(rows[-1][4], 597528)
