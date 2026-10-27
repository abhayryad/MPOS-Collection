"""mPOS phone check-ins: buffered in memory, written to Snowflake in chunks (Snowflake faked)."""
import unittest
from datetime import datetime, timedelta
from unittest import mock

from mpos.devices import health, store

T0 = datetime(2026, 10, 27, 14, 0)


def beat(i, terminal="HD22_MPOS1"):
    return (terminal, "HD22", T0 + timedelta(seconds=i), False, "ON", "2.4.0", "SN-1", "10.0.0.5", None, None)


class BufferTest(unittest.TestCase):
    def test_take_empties_and_keeps_order(self):
        buf = health.Buffer()
        for i in range(3):
            buf.add(beat(i))
        self.assertEqual([r[2] for r in buf.take()], [T0, T0 + timedelta(seconds=1), T0 + timedelta(seconds=2)])
        self.assertEqual(len(buf), 0)

    def test_put_back_goes_in_front(self):
        buf = health.Buffer()
        buf.add(beat(5))
        buf.put_back([beat(1), beat(2)])
        self.assertEqual([r[2].second for r in buf.take()], [1, 2, 5])

    def test_cap_drops_oldest(self):
        buf = health.Buffer(max_rows=3)
        for i in range(5):
            buf.add(beat(i))
        self.assertEqual((len(buf), buf.dropped), (3, 2))
        self.assertEqual([r[2].second for r in buf.take()], [2, 3, 4])
        buf.add(beat(9))
        buf.put_back([beat(0), beat(1), beat(2), beat(3)])
        self.assertEqual([r[2].second for r in buf.take()], [2, 3, 9])  # the oldest go first, newest kept


class FlushTest(unittest.TestCase):
    def setUp(self):
        self.buf = health.Buffer()
        self.inserted, self.since = [], []
        for name, fn in (("insert_heartbeats", lambda rows: self.inserted.append(list(rows))),
                         ("update_last_seen", lambda since: self.since.append(since))):
            p = mock.patch.object(health.store, name, side_effect=fn)
            p.start()
            self.addCleanup(p.stop)

    def test_nothing_waiting(self):
        self.assertEqual(health.flush(self.buf), 0)
        self.assertEqual((self.inserted, self.since), ([], []))

    def test_writes_in_chunks_then_last_seen(self):
        with mock.patch.object(health, "CHUNK_ROWS", 2):
            for i in range(5):
                self.buf.add(beat(i))
            self.assertEqual(health.flush(self.buf), 5)
        self.assertEqual([len(c) for c in self.inserted], [2, 2, 1])
        self.assertEqual(self.since, [T0])  # earliest check-in written
        self.assertEqual(len(self.buf), 0)

    def test_failed_chunk_and_the_rest_wait_for_next_flush(self):
        calls = []

        def insert(rows):
            calls.append(len(rows))
            if len(calls) == 2:
                raise store.sf.StoreUnavailable("down")
        health.store.insert_heartbeats.side_effect = insert
        with mock.patch.object(health, "CHUNK_ROWS", 2), self.assertLogs(health.log, "ERROR"):
            for i in range(5):
                self.buf.add(beat(i))
            self.assertEqual(health.flush(self.buf), 2)
        self.assertEqual([r[2].second for r in self.buf.take()], [2, 3, 4])  # first chunk not written twice
        self.assertEqual(self.since, [T0])

    def test_snowflake_down_writes_nothing_and_keeps_all(self):
        health.store.insert_heartbeats.side_effect = store.sf.StoreUnavailable("down")
        self.buf.add(beat(0))
        with self.assertLogs(health.log, "ERROR"):
            self.assertEqual(health.flush(self.buf), 0)
        self.assertEqual(len(self.buf), 1)
        self.assertEqual(self.since, [])


class StoreHeartbeatSqlTest(unittest.TestCase):
    def test_insert_and_last_seen_sql(self):
        with mock.patch.object(store, "_run") as run:
            store.insert_heartbeats([beat(0), beat(1)])
            store.insert_heartbeats([])
            store.update_last_seen(T0)
        insert, last_seen = run.call_args_list  # the empty insert sent nothing
        self.assertIn("MPOS_DEVICE_HEARTBEATS (TERMINAL_ID, STORE_CODE, SEEN_AT", insert.args[0])
        self.assertEqual(insert.args[0].count("?"), len(store.HEARTBEAT_COLUMNS))
        self.assertEqual(insert.kwargs, {"fetch": False, "many": True})
        self.assertIn("SET LAST_SEEN_AT = h.SEEN", last_seen.args[0])
        self.assertNotIn("VERSION", last_seen.args[0])  # not a switch change
        self.assertEqual(last_seen.args[1], [T0])


if __name__ == "__main__":
    unittest.main()
