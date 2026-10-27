"""mPOS device cache: answers from memory, reloads when Snowflake's CHANGE_SEQ moves (Snowflake is faked)."""
import unittest
from datetime import datetime
from unittest import mock

from mpos.devices import cache as C
from mpos.devices import state as S
from mpos.devices.store import sf

TOKEN1, TOKEN2 = "a" * 64, "b" * 64


class FakeSnowflake:
    """What devices/store.py would read from Snowflake. Change the fields, then bump seq."""

    def __init__(self):
        self.seq = 1
        self.glob = S.Global()
        self.stores = {"HD22": S.Store("HD22")}
        self.terminals = {
            "HD22_MPOS1": S.Terminal("HD22_MPOS1", "HD22", 1, token_hash=TOKEN1),
            "HD22_MPOS2": S.Terminal("HD22_MPOS2", "HD22", 2),  # not paired
        }
        self.reads = 0

    def patches(self):
        def counted(value):
            def read():
                self.reads += 1
                return value()
            return read
        return [mock.patch.object(C.store, "change_seq", lambda: self.seq),
                mock.patch.object(C.store, "load_global", counted(lambda: self.glob)),
                mock.patch.object(C.store, "load_stores", lambda: dict(self.stores)),
                mock.patch.object(C.store, "load_terminals", lambda: dict(self.terminals))]


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.sf = FakeSnowflake()
        for p in self.sf.patches():
            p.start()
            self.addCleanup(p.stop)
        self.cache = C.Cache()
        self.cache.load()

    def change(self, **kw):
        """Simulate an admin change in Snowflake (from this site or another copy)."""
        for name, value in kw.items():
            setattr(self.sf, name, value)
        self.sf.seq += 1

    def test_token_finds_only_active_paired_terminals(self):
        self.assertEqual(self.cache.terminal_for_token(TOKEN1).terminal_id, "HD22_MPOS1")
        self.assertIsNone(self.cache.terminal_for_token(TOKEN2))
        self.assertIsNone(self.cache.terminal_for_token(None))
        retired = S.Terminal("HD22_MPOS1", "HD22", 1, token_hash=TOKEN1, status="RETIRED")
        self.change(terminals={"HD22_MPOS1": retired})
        self.cache.refresh_if_changed()
        self.assertIsNone(self.cache.terminal_for_token(TOKEN1))

    def test_start_bill_only_when_on(self):
        allowed, st = self.cache.start_bill("HD22_MPOS1")
        self.assertTrue(allowed)
        self.assertEqual(st.key, S.ON)
        self.assertTrue(self.cache.live["HD22_MPOS1"].bill_open)

        self.cache.end_bill("HD22_MPOS1")
        self.change(stores={"HD22": S.Store("HD22", ho_enabled=False, ho_reason="Renovation")})
        self.cache.refresh_if_changed()
        allowed, st = self.cache.start_bill("HD22_MPOS1")
        self.assertFalse(allowed)
        self.assertEqual(st, S.State(S.OFF, S.STORE_OFF_BY_HO, "Renovation"))
        self.assertFalse(self.cache.live["HD22_MPOS1"].bill_open)

    def test_open_bill_drains_then_stops(self):
        self.cache.start_bill("HD22_MPOS1")
        self.change(glob=S.Global(enabled=False, reason="Billing incident"))
        self.cache.refresh_if_changed()
        self.assertEqual(self.cache.state_of("HD22_MPOS1").key, S.DRAINING)  # bill open survived the reload
        before, after = self.cache.end_bill("HD22_MPOS1")
        self.assertEqual((before.key, after.key), (S.DRAINING, S.OFF))

    def test_heartbeat_records_the_phone(self):
        at = datetime(2026, 10, 27, 14, 22)
        st = self.cache.check_in("HD22_MPOS1", bill_open=True, serial="SN-1", app_version="2.4.0",
                                 ip="10.0.0.5", now=at)
        self.assertEqual(st.key, S.ON)
        live = self.cache.live["HD22_MPOS1"]
        self.assertEqual((live.bill_open, live.last_seen, live.serial, live.app_version), (True, at, "SN-1", "2.4.0"))

    def test_reloads_only_when_seq_moves(self):
        reads = self.sf.reads
        self.assertFalse(self.cache.refresh_if_changed())
        self.assertEqual(self.sf.reads, reads)  # nothing re-read while nothing changed
        self.change()
        self.assertTrue(self.cache.refresh_if_changed())
        self.assertEqual(self.sf.reads, reads + 1)

    def test_repair_moves_the_token(self):
        repaired = S.Terminal("HD22_MPOS1", "HD22", 1, token_hash=TOKEN2)
        self.change(terminals={**self.sf.terminals, "HD22_MPOS1": repaired})
        self.cache.refresh_if_changed()
        self.assertIsNone(self.cache.terminal_for_token(TOKEN1))  # old phone shut out
        self.assertEqual(self.cache.terminal_for_token(TOKEN2).terminal_id, "HD22_MPOS1")

    def test_terminal_without_store_row_uses_defaults(self):
        self.change(stores={})
        self.cache.refresh_if_changed()
        self.assertEqual(self.cache.state_of("HD22_MPOS1").key, S.ON)

    def test_snowflake_down_keeps_the_copy(self):
        self.change(glob=S.Global(enabled=False))
        with mock.patch.object(C.store, "change_seq", side_effect=sf.StoreUnavailable("down")), \
                self.assertLogs(C.log, "ERROR"):
            self.cache.tick()
        self.assertEqual(self.cache.state_of("HD22_MPOS1").key, S.ON)  # still answering from the old copy


if __name__ == "__main__":
    unittest.main()
