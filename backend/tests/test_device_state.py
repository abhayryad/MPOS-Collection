"""mPOS device control: the state rules and the Snowflake table code (Snowflake is faked)."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from mpos.config import settings
from mpos.devices import state as S
from mpos.devices import store


def terminal(**kw):
    return S.Terminal(terminal_id="HD22_MPOS1", store_code="HD22", seq=1, token_hash="ab" * 32, **kw)


class StateTest(unittest.TestCase):
    def check(self, t=None, st=None, g=None, bill_open=False):
        return S.terminal_state(t or terminal(), st or S.Store("HD22"), g or S.Global(), bill_open)

    def test_every_level_on_bills(self):
        self.assertEqual(self.check(), S.State(S.ON))
        self.assertEqual(self.check(bill_open=True), S.State(S.ON))
        self.assertEqual(self.check().text(), "Enabled")

    def test_each_level_off_stops_it_with_its_reason(self):
        cases = [
            (dict(g=S.Global(enabled=False, reason="Billing incident")), S.ALL_MPOS_OFF, "Billing incident"),
            (dict(st=S.Store("HD22", ho_enabled=False, ho_reason="Renovation")), S.STORE_OFF_BY_HO, "Renovation"),
            (dict(st=S.Store("HD22", day_control=True, day_open=False)), S.DAY_CLOSED, ""),
            (dict(t=terminal(ho_veto=True, veto_reason="Audit check")), S.VETOED, "Audit check"),
            (dict(t=terminal(store_enabled=False)), S.OFF_BY_STORE, ""),
        ]
        for kw, reason, detail in cases:
            with self.subTest(reason=reason):
                self.assertEqual(self.check(**kw), S.State(S.OFF, reason, detail))
                # a bill in progress may finish: same reason, DRAINING
                self.assertEqual(self.check(bill_open=True, **kw), S.State(S.DRAINING, reason, detail))

    def test_first_level_off_is_the_reason(self):
        everything_off = self.check(
            t=terminal(ho_veto=True, store_enabled=False),
            st=S.Store("HD22", ho_enabled=False, day_control=True), g=S.Global(enabled=False))
        self.assertEqual(everything_off.reason, S.ALL_MPOS_OFF)
        store_and_veto = self.check(t=terminal(ho_veto=True), st=S.Store("HD22", ho_enabled=False))
        self.assertEqual(store_and_veto.reason, S.STORE_OFF_BY_HO)
        veto_and_store_switch = self.check(t=terminal(ho_veto=True, store_enabled=False))
        self.assertEqual(veto_and_store_switch.reason, S.VETOED)

    def test_veto_beats_the_store_switch(self):
        self.assertEqual(self.check(t=terminal(ho_veto=True, store_enabled=True)).key, S.OFF)

    def test_day_ignored_unless_the_store_uses_it(self):
        self.assertEqual(self.check(st=S.Store("HD22", day_control=False, day_open=False)).key, S.ON)
        self.assertEqual(self.check(st=S.Store("HD22", day_control=True, day_open=True)).key, S.ON)

    def test_lock_retired_unpaired_stop_even_mid_bill(self):
        locked = self.check(t=terminal(immediate_lock=True, lock_reason="Phone stolen"), bill_open=True)
        self.assertEqual(locked, S.State(S.OFF, S.LOCKED, "Phone stolen"))
        self.assertEqual(self.check(t=terminal(status="RETIRED"), bill_open=True), S.State(S.OFF, S.RETIRED))
        unpaired = S.Terminal(terminal_id="HD22_MPOS2", store_code="HD22", seq=2)
        self.assertFalse(unpaired.paired)
        self.assertEqual(self.check(t=unpaired, bill_open=True), S.State(S.OFF, S.NOT_PAIRED))

    def test_text(self):
        self.assertEqual(S.State(S.OFF, S.STORE_OFF_BY_HO, "Renovation").text(), "Store disabled by HO: Renovation")
        self.assertEqual(S.State(S.DRAINING, S.OFF_BY_STORE).text(), "Disabled by store")

    def test_terminal_id(self):
        self.assertEqual(S.terminal_id(" hd22 ", 3), "HD22_MPOS3")


class StoreRowsTest(unittest.TestCase):
    def test_terminal_row(self):
        row = {"TERMINAL_ID": "HD22_MPOS1", "STORE_CODE": "HD22", "SEQ": 1, "STATUS": "ACTIVE",
               "STORE_ENABLED": True, "HO_VETO": False, "VETO_REASON": None, "IMMEDIATE_LOCK": False,
               "LOCK_REASON": None, "TOKEN_HASH": None, "DEVICE_SERIAL": None, "APP_VERSION": None,
               "PAIRED_AT": None, "LAST_SEEN_AT": None, "VERSION": 2}
        t = store._terminal(row)
        self.assertFalse(t.paired)
        self.assertEqual((t.veto_reason, t.version), ("", 2))
        self.assertTrue(store._terminal({**row, "TOKEN_HASH": "ab" * 32}).paired)

    def test_store_and_global_rows(self):
        st = store._store({"STORE_CODE": "HD22", "HO_ENABLED": False, "HO_REASON": "Renovation",
                           "DAY_CONTROL": True, "DAY_OPEN": False, "DAY_CLOSE_TIME": "23:00",
                           "APPROVER": "v25151", "MAX_MPOS": 10, "NEXT_SEQ": 3, "VERSION": 1})
        self.assertEqual((st.ho_enabled, st.ho_reason, st.next_seq), (False, "Renovation", 3))
        g = store._global({"ENABLED": True, "REASON": None, "VERSION": 4})
        self.assertEqual((g.enabled, g.reason, g.version), (True, "", 4))


class FakeConnection:
    """Stands in for the device Snowflake connection: records every statement, returns `rows`."""

    def __init__(self, rows=()):
        self.statements, self.rows = [], list(rows)

    def is_closed(self):
        return False

    def cursor(self, *_):
        conn = self

        class Cursor:
            rowcount = 0

            def execute(self, sql, params=None):
                conn.statements.append((sql, params))

            def executemany(self, sql, rows):
                conn.statements.append((sql, rows))

            def fetchall(self):
                return conn.rows

        return Cursor()


class StoreSnowflakeTest(unittest.TestCase):
    def setUp(self):
        self.conn = FakeConnection()
        for name, value in (("_tables_ready", False), ("_conn", self.conn)):
            patcher = mock.patch.object(store, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_tables_created_once_on_the_device_connection(self):
        with mock.patch.object(store.sf, "_run") as shared:
            store.load_stores()
            store.load_terminals()
        shared.assert_not_called()  # never the login connection
        sql = [s for s, _ in self.conn.statements]
        ddl = [s for s in sql if s.startswith(("CREATE", "MERGE"))]
        for table in ("MPOS_GLOBAL", "MPOS_STORES", "MPOS_TERMINALS", "MPOS_DEVICE_HISTORY",
                      "MPOS_DEVICE_HEARTBEATS"):
            self.assertEqual(sum(f"BRONZE.{table} " in s for s in ddl if s.startswith("CREATE")), 1, table)
        self.assertEqual(len(sql) - len(ddl), 2)  # DDL ran once, then the two SELECTs
        self.assertTrue(all(s.startswith(("CREATE", "MERGE", "SELECT")) for s in sql))  # nothing split mid-statement

    def test_global_defaults_when_row_missing(self):
        self.assertEqual(store.load_global(), S.Global())

    def test_change_seq(self):
        self.assertEqual(store.change_seq(), 0)  # no row yet
        self.conn.rows = [{"CHANGE_SEQ": 7}]
        self.assertEqual(store.change_seq(), 7)
        store.bump_change()
        self.assertIn("CHANGE_SEQ = CHANGE_SEQ + 1", self.conn.statements[-1][0])

    def test_many_rows_go_in_one_statement(self):
        store._run(f"INSERT INTO {store.HEARTBEATS} (TERMINAL_ID) VALUES (?)", [("HD22_MPOS1",), ("HD22_MPOS2",)],
                   fetch=False, many=True)
        _, rows = self.conn.statements[-1]
        self.assertEqual(rows, [["HD22_MPOS1"], ["HD22_MPOS2"]])

    def test_record_writes_history(self):
        store.record("VETO_ON", "TERMINAL", actor="v25151", target="HD22_MPOS1", old="off", new="on",
                     reason="Audit check")
        sql, params = self.conn.statements[-1]
        self.assertIn("INSERT INTO", sql)
        self.assertEqual(params, ["v25151", "TERMINAL", "HD22_MPOS1", "VETO_ON", "off", "on", "Audit check", 1, None])
        with self.assertRaises(ValueError):
            store.record("X", "REGION", actor="v25151")

    def test_record_falls_back_to_file(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(store, "settings", SimpleNamespace(sql_dir=settings.sql_dir, output_dir=Path(tmp))), \
                mock.patch.object(store, "_conn", None), \
                mock.patch.object(store.sf, "_connect", side_effect=store.sf.StoreUnavailable("down")):
            store.record("GLOBAL_OFF", "GLOBAL", actor="admin", reason="Billing incident", affected=912)
            lines = (Path(tmp) / "device_events_fallback.jsonl").read_text(encoding="utf-8").splitlines()
        event = json.loads(lines[0])
        self.assertEqual((event["event"], event["affected"], event["error"]), ("GLOBAL_OFF", 912, "down"))


if __name__ == "__main__":
    unittest.main()
