"""Exact-output tests for the M, S, R and C slip formats (no database needed).

Run from the project root:  python -m unittest discover -s backend/tests -t backend
"""
import unittest

import pandas as pd

from mpos.slips import cancel, mop, sales
from mpos.slips.common import num
from mpos.slips.service import build_all


def payments(*rows):
    return pd.DataFrame(rows, columns=["STORE", "TRANSDATE", "MOP_TYPE", "AMOUNTCUR"])


def items(*rows):
    return pd.DataFrame(rows, columns=["STORE", "TRANSDATE", "TRANSACTIONID", "LINENUM",
                                       "BARCODE", "QTY", "PRICE", "DISCAMOUNT"])


def bills(*rows):
    return pd.DataFrame(rows, columns=["STORE", "TRANSDATE", "TRANSTIME", "ORDERID", "BILL_NO",
                                       "NUMBEROFITEMS", "NETAMOUNT", "TOTALDISCAMOUNT", "ENTRYSTATUS"])


class NumTest(unittest.TestCase):
    def test_trims_zeros_and_sign(self):
        self.assertEqual(num(450.0), "450")
        self.assertEqual(num(324.50), "324.5")
        self.assertEqual(num(-1), "1")
        self.assertEqual(num(None), "0")
        self.assertEqual(num(0.0), "0")


class MopSlipTest(unittest.TestCase):
    CODES = ["CA", "CN", "UPI"]

    def test_every_mop_listed_with_zero_fill_and_cn_both_signs(self):
        df = payments(("HD22 ", "2026-09-15", "CREDITMEMO", -450.0),
                      ("HD22", "2026-09-15", "UPI", 450.0))
        slips = mop.render(mop.build(df, self.CODES))
        self.assertEqual(slips[("HD22", "2026-09-15")],
                         "HD22\t09-15-2026\tCA\t+\t0.00\n"
                         "HD22\t09-15-2026\tCN\t+\t0.00\n"
                         "HD22\t09-15-2026\tCN\t-\t450.00\n"
                         "HD22\t09-15-2026\tUPI\t+\t450.00\n")

    def test_amounts_summed_per_mop_and_sign(self):
        df = payments(("HD22", "2026-08-26", "CASH", 393.75),
                      ("HD22", "2026-08-26", "CASH", 375.0),
                      ("HD22", "2026-08-26", "UPI", 1325.0),
                      ("HD22", "2026-08-26", "CASH", -50.0))
        text = mop.render(mop.build(df, self.CODES))[("HD22", "2026-08-26")]
        self.assertIn("HD22\t08-26-2026\tCA\t+\t768.75\n", text)
        self.assertIn("HD22\t08-26-2026\tCA\t-\t50.00\n", text)  # negative non-CN MOP gets its own line
        self.assertIn("HD22\t08-26-2026\tUPI\t+\t1325.00\n", text)

    def test_mop_codes(self):
        self.assertEqual(mop.mop_code("CASH"), "CA")
        self.assertEqual(mop.mop_code("CREDITMEMO"), "CN")
        self.assertEqual(mop.mop_code("UPI"), "UPI")


class SaleReturnSlipTest(unittest.TestCase):
    def test_sale_slip_layout(self):
        df = sales.prepare(items(("HD22", "2026-09-28", "TXN2", 1, " 84051491 ", 2.0, 450.0, 0.0),
                                 ("HD22", "2026-09-28", "TXN1", 1, "84051491", 1.0, 324.5, 50.0)))
        text = sales.render(df)[("HD22", "2026-09-28")]
        self.assertEqual(text,
                         "1\tHD22\t20260928" + "\t*" * 13 + "\n"
                         "2\t84051491\t-\t1\t+\t324.5\t-\tZPR1\t0\t-\tZPR2\t0\t-\tZPR3\t50\n"
                         "2\t84051491\t-\t2\t+\t450\t-\tZPR1\t0\t-\tZPR2\t0\t-\tZPR3\t0\n")

    def test_returns_go_to_r_slip_unsigned(self):
        pay = payments(("HD22", "2026-09-28", "UPI", 450.0))
        it = items(("HD22", "2026-09-28", "TXN1", 1, "84051491", 1.0, 450.0, 0.0),
                   ("HD22", "2026-09-28", "RTN1", 1, "84051491", -1.0, 450.0, 0.0))
        slips = build_all(["UPI"], pay, it, bills())
        self.assertEqual(slips["S"][("HD22", "2026-09-28")].count("\n2\t"), 1)
        self.assertEqual(slips["R"][("HD22", "2026-09-28")].splitlines()[1],
                         "2\t84051491\t-\t1\t+\t450\t-\tZPR1\t0\t-\tZPR2\t0\t-\tZPR3\t0")


class CancelSlipTest(unittest.TestCase):
    def test_only_cancelled_bills_once_each(self):
        df = bills(("DH24", "2026-08-21", 52520, "123555", "332121", 7, 1058.5, 75.0, 0),
                   ("DH24", "2026-08-21", 52520, "123555", "332121", 7, 1058.5, 75.0, 0),
                   ("DH24", "2026-08-21", 60000, "999", "444", 1, 100.0, 0.0, 1))
        slips = cancel.render(cancel.prepare(df))
        self.assertEqual(slips[("DH24", "2026-08-21")],
                         "1\tDH24\t20260821" + "\t*" * 5 + "\n"
                         "2\t332121\t123555\t-\t7\t+\t1058.5\t-\tDISC\t75\n")


if __name__ == "__main__":
    unittest.main()
