"""mPOS switches and settings written to Snowflake by the portal screens (Snowflake faked)."""
import unittest
from unittest import mock

from mpos.devices import store
from tests.test_device_pairing import ScriptedConnection


class WritesTest(unittest.TestCase):
    def use(self, *answers):
        self.conn = ScriptedConnection(answers)
        self.events = []
        for p in (mock.patch.object(store, "_conn", self.conn), mock.patch.object(store, "_tables_ready", True),
                  mock.patch.object(store, "record", lambda event, *a, **kw: self.events.append((event, kw)))):
            p.start()
            self.addCleanup(p.stop)

    def update(self, table="MPOS_TERMINALS"):
        return next((s, p) for s, p in self.conn.statements if s.startswith(f"UPDATE V2RETAIL.BRONZE.{table}"))

    def bumped(self):
        return any("CHANGE_SEQ + 1" in s for s in self.conn.sql())

    def test_global_off_and_on(self):
        self.use()
        store.set_global(False, "Billing incident", "ho1", version=4, ip="10.1.1.1")
        sql, params = self.update("MPOS_GLOBAL")
        self.assertIn("AND VERSION = ?", sql)
        self.assertEqual(params, [False, "Billing incident", "ho1", 1, 4])
        self.assertTrue(self.bumped())
        self.assertEqual(self.events[-1][0], "GLOBAL_OFF")
        self.use()
        store.set_global(True, "ignored when on", "ho1")
        self.assertEqual(self.update("MPOS_GLOBAL")[1], [True, None, "ho1", 1])  # no version: no guard
        self.assertEqual(self.events[-1][0], "GLOBAL_ON")

    def test_stale_screen_is_refused(self):
        self.use(("UPDATE V2RETAIL.BRONZE.MPOS_GLOBAL SET ENABLED", [], 0))
        with self.assertRaisesRegex(store.Conflict, "changed by someone else"):
            store.set_global(False, "x", "ho1", version=3)
        self.assertFalse(self.bumped())
        self.assertEqual(self.events, [])

    def test_stores_switch_some_or_all_in_one_statement(self):
        self.use(("UPDATE V2RETAIL.BRONZE.MPOS_STORES", [], 2))
        self.assertEqual(store.set_stores([" hd22", "DH24", "HD22"], False, "Audit", "ho1"), 2)
        sql, params = self.update("MPOS_STORES")
        self.assertIn("WHERE STORE_CODE IN (?, ?)", sql)
        self.assertEqual(params, [False, "Audit", "ho1", "DH24", "HD22"])
        self.assertEqual((self.events[-1][0], self.events[-1][1]["affected"]), ("STORE_OFF", 2))

        self.use(("UPDATE V2RETAIL.BRONZE.MPOS_STORES", [], 432))
        self.assertEqual(store.set_stores(None, True, "", "ho1"), 432)
        self.assertNotIn("WHERE", self.update("MPOS_STORES")[0])  # every store

    def test_one_store_with_version(self):
        self.use(("UPDATE V2RETAIL.BRONZE.MPOS_STORES", [], 0))
        with self.assertRaises(store.Conflict):
            store.set_stores(["HD22"], False, "x", "ho1", version=7)

    def test_terminal_switch_veto_lock_only_on_active_terminals(self):
        for fn, args, event, sets in [
                (store.set_terminal, ("HD22_MPOS1", False, "v1"), "TERMINAL_OFF", "STORE_ENABLED = ?"),
                (store.set_veto, ("HD22_MPOS1", True, "Audit", "ho1"), "VETO_ON", "HO_VETO = ?, VETO_REASON = ?"),
                (store.set_veto, ("HD22_MPOS1", False, "", "ho1"), "VETO_OFF", "HO_VETO = ?"),
                (store.set_lock, ("HD22_MPOS1", True, "Stolen", "ho1"), "LOCK_ON", "IMMEDIATE_LOCK = ?"),
                (store.set_lock, ("HD22_MPOS1", False, "", "ho1"), "LOCK_OFF", "IMMEDIATE_LOCK = ?")]:
            with self.subTest(event=event):
                self.use()
                fn(*args)
                sql, params = self.update()
                self.assertIn(sets, sql)
                self.assertIn("AND STATUS = 'ACTIVE'", sql)
                self.assertEqual(self.events[-1][0], event)
                self.assertTrue(self.bumped())
        self.use()
        store.set_veto("HD22_MPOS1", False, "old reason", "ho1")
        self.assertEqual(self.update()[1][:2], [False, None])  # lifting clears the reason

    def test_day_only_for_stores_using_it(self):
        self.use()
        store.set_day("hd22", True, "v1")
        sql, params = self.update("MPOS_STORES")
        self.assertIn("DAY_OPENED_AT = CURRENT_TIMESTAMP()", sql)
        self.assertIn("AND DAY_CONTROL", sql)
        self.assertEqual(params, [True, "v1", "v1", "HD22"])
        self.use(("UPDATE V2RETAIL.BRONZE.MPOS_STORES", [], 0))
        with self.assertRaisesRegex(store.Conflict, "does not use Open day"):
            store.set_day("HD22", False, "v1")

    def test_store_settings(self):
        self.use()
        store.set_store_settings("hd22", {"max_mpos": 3, "approver": "v25151", "bogus": 1}, "ho1", version=2)
        merge = next(p for s, p in self.conn.statements if s.startswith("MERGE INTO V2RETAIL.BRONZE.MPOS_STORES"))
        self.assertEqual(merge, ["HD22", 5, "ho1"])  # row made first if the store never had mPOS
        sql, params = self.update("MPOS_STORES")
        self.assertIn("SET MAX_MPOS = ?, APPROVER = ?", sql)
        self.assertNotIn("bogus", sql)
        self.assertEqual(params, [3, "v25151", "ho1", "HD22", 2])
        self.assertEqual([e for e, _ in self.events], ["MAX_SET", "APPROVER_SET"])

    def test_add_terminals_to_many_stores_in_one_transaction(self):
        self.use(("SELECT s.STORE_CODE, s.NEXT_SEQ",
                  [{"STORE_CODE": "DH24", "NEXT_SEQ": 1, "MAX_MPOS": 5, "ACTIVE": 0},
                   {"STORE_CODE": "HD22", "NEXT_SEQ": 4, "MAX_MPOS": 5, "ACTIVE": 4}], 0),
                 ("UPDATE V2RETAIL.BRONZE.MPOS_STORES s SET NEXT_SEQ", [], 2))
        added, full = store.add_terminals(["hd22", "DH24"], 2, "ho1")
        self.assertEqual(added, ["DH24_MPOS1", "DH24_MPOS2", "HD22_MPOS4"])  # HD22 had room for one
        self.assertEqual(full, ["HD22"])
        insert = next(p for s, p in self.conn.statements if s.startswith("INSERT INTO V2RETAIL.BRONZE.MPOS_TERMINALS"))
        self.assertEqual(insert, [["DH24_MPOS1", "DH24", 1, "ho1"], ["DH24_MPOS2", "DH24", 2, "ho1"],
                                  ["HD22_MPOS4", "HD22", 4, "ho1"]])  # one statement for all rows
        merge = next(s for s, _ in self.conn.statements if s.startswith("MERGE INTO V2RETAIL.BRONZE.MPOS_STORES"))
        self.assertIn("FROM VALUES (?), (?)) n", merge)
        self.assertIn("FROM VALUES (?, ?, ?), (?, ?, ?)) n", next(s for s, _ in self.conn.statements if "NEW_SEQ" in s))
        guard = next(p for s, p in self.conn.statements if "SET NEXT_SEQ = n.NEW_SEQ" in s)
        self.assertEqual(guard, ["ho1", "DH24", 1, 3, "HD22", 4, 5])
        sql = self.conn.sql()
        self.assertLess(sql.index("BEGIN"), sql.index("COMMIT"))
        self.assertEqual(self.events[-1][0], "TERMINAL_ADDED")
        self.assertEqual(self.events[-1][1]["affected"], 3)

    def test_hard_limit_of_5_whatever_the_row_says(self):
        self.use(("SELECT s.STORE_CODE, s.NEXT_SEQ", [{"STORE_CODE": "HD22", "NEXT_SEQ": 6, "MAX_MPOS": 10, "ACTIVE": 4}], 0),
                 ("UPDATE V2RETAIL.BRONZE.MPOS_STORES s SET NEXT_SEQ", [], 1))
        self.assertEqual(store.add_terminals(["HD22"], 3, "ho1"), (["HD22_MPOS6"], ["HD22"]))  # one slot left, not six
        self.use()
        with self.assertRaisesRegex(ValueError, "at most 5"):
            store.set_store_settings("HD22", {"max_mpos": 6}, "ho1")
        store.set_store_settings("HD22", {"max_mpos": 3}, "ho1")  # lower is fine

    def test_add_terminals_race_rolls_back(self):
        self.use(("SELECT s.STORE_CODE, s.NEXT_SEQ", [{"STORE_CODE": "HD22", "NEXT_SEQ": 1, "MAX_MPOS": 5,
                                                       "ACTIVE": 0}], 0),
                 ("UPDATE V2RETAIL.BRONZE.MPOS_STORES s SET NEXT_SEQ", [], 0))
        with self.assertRaises(store.sf.StoreUnavailable):
            store.add_terminals(["HD22"], 1, "ho1")
        self.assertEqual(self.conn.sql()[-1], "ROLLBACK")
        self.assertEqual(self.events, [])

    def test_history_filters(self):
        row = {"EVENT_ID": 9, "EVENT_TIME": None, "ACTOR": "ho1", "LEVEL": "STORE", "TARGET": "HD22",
               "EVENT": "STORE_OFF", "OLD_VALUE": None, "NEW_VALUE": "OFF", "REASON": "Audit", "AFFECTED_COUNT": 1}
        self.use(("SELECT * FROM V2RETAIL.BRONZE.MPOS_DEVICE_HISTORY", [row], 0))
        out = store.load_history("hd22", limit=50)
        sql, params = self.conn.statements[-1]
        self.assertIn("TARGET = ? OR STARTSWITH(TARGET, ?)", sql)  # the store and its terminals
        self.assertEqual(params, ["HD22", "HD22_MPOS", 50])
        self.assertEqual(out[0]["event"], "STORE_OFF")
        store.load_history(terminal_id="hd22_mpos1")
        self.assertEqual(self.conn.statements[-1][1], ["HD22_MPOS1", 200])


if __name__ == "__main__":
    unittest.main()
