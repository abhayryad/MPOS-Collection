"""mPOS pairing: codes, tokens, rate limit, and the Snowflake steps to add, issue and redeem (Snowflake faked)."""
import unittest
from unittest import mock

from mpos.devices import pairing, store


class PairingHelpersTest(unittest.TestCase):
    def test_code_shape(self):
        codes = {pairing.new_code() for _ in range(200)}
        self.assertGreater(len(codes), 190)  # random
        for code in codes:
            self.assertEqual(len(code), 6)
            self.assertTrue(set(code) <= set(pairing.CODE_CHARS))
            self.assertFalse(set(code) & set("0O1IL"))

    def test_code_digest_ignores_case_spaces_dashes(self):
        self.assertEqual(pairing.code_digest("k7p-2qx"), pairing.code_digest(" K7P 2QX "))
        self.assertNotEqual(pairing.code_digest("K7P2QX"), pairing.code_digest("K7P2QY"))
        self.assertNotIn("K7P2QX", pairing.code_digest("K7P2QX"))

    def test_token(self):
        token = pairing.new_token()
        self.assertGreaterEqual(len(token), 40)
        self.assertNotEqual(token, pairing.new_token())
        self.assertNotEqual(pairing.token_digest(token), pairing.token_digest(token.swapcase()))  # case matters
        self.assertEqual(len(pairing.token_digest(token)), 64)

    def test_rate_limit(self):
        limit = pairing.RateLimit(limit=3, seconds=60)
        self.assertEqual([limit.allow("10.0.0.5", now=t) for t in (0, 1, 2, 3)], [True, True, True, False])
        self.assertTrue(limit.allow("10.0.0.6", now=3))   # other IPs unaffected
        self.assertTrue(limit.allow("10.0.0.5", now=61))  # the window moved on


class ScriptedConnection:
    """Fake device connection. `answers` = [(SQL fragment, rows, rowcount)]: the first fragment found in a
    statement decides its result. Every statement is recorded."""

    def __init__(self, answers=()):
        self.answers, self.statements = list(answers), []

    def is_closed(self):
        return False

    def cursor(self, *_):
        conn = self

        class Cursor:
            rowcount, _rows = 0, []

            def execute(self, sql, params=None):
                conn.statements.append((sql, params))
                self._rows, self.rowcount = [], 1 if sql.startswith(("UPDATE", "INSERT")) else 0
                for fragment, rows, rowcount in conn.answers:
                    if fragment in sql:
                        self._rows, self.rowcount = rows, rowcount
                        break

            def fetchall(self):
                return self._rows

        return Cursor()

    def sql(self):
        return [s for s, _ in self.statements if not s.startswith(("CREATE", "MERGE INTO V2RETAIL.BRONZE.MPOS_GLOBAL"))]


class StorePairingTest(unittest.TestCase):
    def use(self, *answers):
        self.conn = ScriptedConnection(answers)
        self.events = []
        for p in (mock.patch.object(store, "_conn", self.conn), mock.patch.object(store, "_tables_ready", True),
                  mock.patch.object(store, "record", lambda event, *a, **kw: self.events.append((event, kw)))):
            p.start()
            self.addCleanup(p.stop)

    def live_code(self, code="K7P2QX", attempts=0, live=True, status="ACTIVE", old_serial=None):
        return ("SELECT STATUS, PAIR_CODE_HASH",
                [{"STATUS": status, "PAIR_CODE_HASH": pairing.code_digest(code), "PAIR_ATTEMPTS": attempts,
                  "CODE_LIVE": live, "DEVICE_SERIAL": old_serial, "TOKEN_HASH": "old" if old_serial else None}], 0)

    def redeem(self, code="K7P2QX"):
        return store.redeem_pair_code("hd22_mpos1", code, serial="SN-1", app_version="2.4.0", ip="10.0.0.5")

    def test_right_code_pairs_and_returns_token(self):
        self.use(self.live_code())
        token = self.redeem("k7p 2qx")
        update = next((s, p) for s, p in self.conn.statements if "SET TOKEN_HASH" in s)
        self.assertEqual(update[1][0], pairing.token_digest(token))  # only the hash is stored
        self.assertNotIn(token, str(self.conn.statements))
        self.assertEqual(update[1][-2:], ["HD22_MPOS1", pairing.code_digest("K7P2QX")])  # code must still be open
        self.assertTrue(any("CHANGE_SEQ + 1" in s for s in self.conn.sql()))  # running sites reload
        self.assertEqual(self.events, [("PAIRED", {"actor": "HD22_MPOS1", "target": "HD22_MPOS1", "old": None,
                                                   "new": "SN-1", "reason": None, "ip": "10.0.0.5"})])
        sql = self.conn.sql()
        self.assertLess(sql.index("BEGIN"), sql.index("COMMIT"))  # token and phone move in one transaction

    def test_new_phone_replaces_the_old_one(self):
        self.use(self.live_code(old_serial="SN-OLD"))
        self.redeem()
        self.assertEqual(self.events[0][1]["old"], "SN-OLD")
        self.assertEqual(self.events[0][1]["reason"], "replaced the phone paired before")

    def test_one_phone_one_terminal(self):
        self.use(self.live_code(), ("SELECT TERMINAL_ID FROM", [{"TERMINAL_ID": "HD22_MPOS2"}], 0))
        self.redeem()
        find = next(p for s, p in self.conn.statements if s.startswith("SELECT TERMINAL_ID FROM"))
        self.assertEqual(find, ["SN-1", "HD22_MPOS1"])  # this phone, on any other terminal
        sql, params = next((s, p) for s, p in self.conn.statements if "WHERE TERMINAL_ID IN" in s)
        self.assertIn("TOKEN_HASH = NULL", sql)
        self.assertEqual(params, ["HD22_MPOS1", "HD22_MPOS2"])
        self.assertEqual(self.events[-1][0], "UNPAIRED")
        self.assertEqual(self.events[-1][1]["target"], "HD22_MPOS2")
        self.assertEqual(self.events[-1][1]["reason"], "phone paired to HD22_MPOS1")

    def test_failed_pairing_rolls_back_the_move(self):
        self.use(self.live_code(), ("SET TOKEN_HASH = ?", [], 0))
        with self.assertRaises(store.PairingError):
            self.redeem()
        sql = self.conn.sql()
        self.assertEqual(sql[-1], "ROLLBACK")
        self.assertFalse(any(s.startswith("SELECT TERMINAL_ID FROM") for s in sql))
        self.assertEqual(self.events, [])

    def test_no_code_expired_or_retired(self):
        for answer in [("SELECT STATUS", [], 0), self.live_code(live=False), self.live_code(status="RETIRED"),
                       ("SELECT STATUS", [{"STATUS": "ACTIVE", "PAIR_CODE_HASH": None, "PAIR_ATTEMPTS": 0,
                                           "CODE_LIVE": None}], 0)]:
            with self.subTest(answer=answer):
                self.use(answer)
                with self.assertRaisesRegex(store.PairingError, "No open pairing code"):
                    self.redeem()

    def test_wrong_code_counts_tries_then_cancels(self):
        self.use(self.live_code(attempts=0))
        with self.assertRaisesRegex(store.PairingError, "4 tries left"):
            self.redeem("AAAAAA")
        sql, params = self.conn.statements[-1]
        self.assertEqual(params, [1, "HD22_MPOS1"])
        self.assertNotIn("PAIR_CODE_HASH = NULL", sql)
        self.assertEqual(self.events[-1][0], "PAIR_FAILED")

        self.use(self.live_code(attempts=4))
        with self.assertRaisesRegex(store.PairingError, "cancelled"):
            self.redeem("AAAAAA")
        self.assertIn("PAIR_CODE_HASH = NULL", self.conn.statements[-1][0])

    def test_code_used_twice_at_once(self):
        self.use(self.live_code(), ("SET TOKEN_HASH = ?", [], 0))
        with self.assertRaisesRegex(store.PairingError, "just been used"):
            self.redeem()

    def test_issue_code_leaves_the_current_phone_billing(self):
        self.use()
        code = store.issue_pair_code("HD22_MPOS1", actor="v25151")
        sql, params = next((s, p) for s, p in self.conn.statements if "SET PAIR_CODE_HASH" in s)
        self.assertNotIn("TOKEN_HASH", sql)  # the phone stops only when a new one pairs
        self.assertEqual(params[0], pairing.code_digest(code))
        self.assertEqual(self.events[-1][0], "PAIR_CODE_ISSUED")
        self.use(("SET PAIR_CODE_HASH", [], 0))
        with self.assertRaises(ValueError):
            store.issue_pair_code("HD22_MPOS9", actor="v25151")

    def test_unpair(self):
        self.use(("SELECT DEVICE_SERIAL", [{"DEVICE_SERIAL": "SN-1"}], 0))
        store.unpair_terminal("hd22_mpos1", actor="v25151", reason="phone sent for repair")
        sql, params = next((s, p) for s, p in self.conn.statements if s.startswith("UPDATE V2RETAIL.BRONZE.MPOS_TERMINALS"))
        for cleared in ("TOKEN_HASH = NULL", "DEVICE_SERIAL = NULL", "PAIR_CODE_HASH = NULL"):
            self.assertIn(cleared, sql)
        self.assertNotIn("STATUS = 'RETIRED'", sql)
        self.assertEqual(params, ["v25151", "HD22_MPOS1"])
        self.assertEqual(self.events[-1], ("UNPAIRED", {"actor": "v25151", "target": "HD22_MPOS1", "old": "SN-1",
                                                       "reason": "phone sent for repair", "ip": None}))
        self.use(("SELECT DEVICE_SERIAL", [], 0))
        with self.assertRaises(ValueError):
            store.unpair_terminal("HD22_MPOS9", actor="v25151")

    def test_retire(self):
        self.use()
        store.retire_terminal("HD22_MPOS1", actor="ho1", reason="phone returned")
        sql = next(s for s in self.conn.sql() if s.startswith("UPDATE V2RETAIL.BRONZE.MPOS_TERMINALS"))
        self.assertIn("STATUS = 'RETIRED'", sql)
        self.assertIn("TOKEN_HASH = NULL", sql)
        self.assertIn("AND STATUS = 'ACTIVE'", sql)  # retiring twice does nothing
        self.assertEqual(self.events[-1][0], "RETIRED")
        self.use(("STATUS = 'RETIRED'", [], 0))
        with self.assertRaises(ValueError):
            store.retire_terminal("HD22_MPOS1", actor="ho1")

    def test_add_terminal_takes_the_next_number(self):
        self.use(("SELECT NEXT_SEQ", [{"NEXT_SEQ": 3, "MAX_MPOS": 10}], 0), ("SELECT COUNT(*)", [{"N": 2}], 0))
        self.assertEqual(store.add_terminal(" hd22 ", actor="v25151"), "HD22_MPOS3")
        sql = self.conn.sql()
        self.assertEqual(sql[0], "BEGIN")
        self.assertEqual(sql.index("COMMIT"), len(sql) - 2)  # then CHANGE_SEQ bump
        insert = next(p for s, p in self.conn.statements if s.startswith("INSERT INTO V2RETAIL.BRONZE.MPOS_TERMINALS"))
        self.assertEqual(insert, ["HD22_MPOS3", "HD22", 3, "v25151"])
        guard = next(p for s, p in self.conn.statements if "SET NEXT_SEQ" in s)
        self.assertEqual(guard, [4, "v25151", "HD22", 3])
        new_store = next(p for s, p in self.conn.statements if s.startswith("MERGE INTO V2RETAIL.BRONZE.MPOS_STORES"))
        self.assertEqual(new_store, ["HD22", 5, "v25151"])  # a new store row gets the limit of 5
        self.assertEqual(self.events[-1][0], "TERMINAL_ADDED")

    def test_add_terminal_limit_and_race_roll_back(self):
        self.use(("SELECT NEXT_SEQ", [{"NEXT_SEQ": 11, "MAX_MPOS": 10}], 0), ("SELECT COUNT(*)", [{"N": 10}], 0))
        with self.assertRaises(store.LimitReached):
            store.add_terminal("HD22", actor="v25151")
        self.assertEqual(self.conn.sql()[-1], "ROLLBACK")

        self.use(("SELECT NEXT_SEQ", [{"NEXT_SEQ": 3, "MAX_MPOS": 10}], 0), ("SELECT COUNT(*)", [{"N": 2}], 0),
                 ("SET NEXT_SEQ", [], 0))
        with self.assertRaises(store.sf.StoreUnavailable):
            store.add_terminal("HD22", actor="v25151")
        self.assertEqual(self.conn.sql()[-1], "ROLLBACK")
        self.assertEqual(self.events, [])


if __name__ == "__main__":
    unittest.main()
