"""mPOS phone API: token check, pair, bill start/end, heartbeat (endpoint functions called directly;
Snowflake faked)."""
import asyncio
import unittest
from datetime import datetime, timedelta
from unittest import mock

from fastapi import BackgroundTasks, HTTPException
from starlette.requests import Request

from mpos.api import device_api as api
from mpos.devices import cache as C
from mpos.devices import health, pairing, store
from mpos.devices import state as S

TOKEN = "phone-token-1"


def request(ip="10.0.0.5"):
    return Request({"type": "http", "client": (ip, 50000), "headers": []})


def run(coro):
    return asyncio.run(coro)


class PhoneApiTest(unittest.TestCase):
    def setUp(self):
        self.glob = S.Global()
        self.stores = {"HD22": S.Store("HD22")}
        self.terminals = {"HD22_MPOS1": S.Terminal("HD22_MPOS1", "HD22", 1, token_hash=pairing.token_digest(TOKEN),
                                                   device_serial="SN-1")}
        self.seq = 1
        patches = [mock.patch.object(C.store, "change_seq", lambda: self.seq),
                   mock.patch.object(C.store, "load_global", lambda: self.glob),
                   mock.patch.object(C.store, "load_stores", lambda: dict(self.stores)),
                   mock.patch.object(C.store, "load_terminals", lambda: dict(self.terminals))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.cache = C.Cache()
        self.cache.load()
        self.beats = health.Buffer()
        for p in (mock.patch.object(api.cache, "current", self.cache), mock.patch.object(api.health, "buffer", self.beats)):
            p.start()
            self.addCleanup(p.stop)
        self.t = self.cache.terminals["HD22_MPOS1"]

    def change(self, **kw):
        for name, value in kw.items():
            setattr(self, name, value)
        self.seq += 1
        self.cache.refresh_if_changed()
        self.t = self.cache.terminals["HD22_MPOS1"]

    # ------------------------------------------------------------ token

    def test_token_check(self):
        self.assertEqual(run(api.device(f"Bearer {TOKEN}")).terminal_id, "HD22_MPOS1")
        self.assertEqual(run(api.device(f"bearer {TOKEN}")).terminal_id, "HD22_MPOS1")
        for header in ("", TOKEN, "Bearer ", "Bearer wrong-token", f"Basic {TOKEN}"):
            with self.subTest(header=header), self.assertRaises(HTTPException) as e:
                run(api.device(header))
            self.assertEqual(e.exception.status_code, 401)

    def test_not_loaded_yet_is_503_not_unpaired(self):
        self.cache.seq = None
        with self.assertRaises(HTTPException) as e:
            run(api.device(f"Bearer {TOKEN}"))
        self.assertEqual(e.exception.status_code, 503)

    # ------------------------------------------------------------ bills

    def test_bill_start(self):
        out = run(api.bill_start(api.BillStart(bill_ref="R1"), self.t))
        self.assertEqual((out["allowed"], out["state"], out["message"]), (True, S.ON, "Enabled"))
        run(api.bill_end(api.BillEnd(), request(), BackgroundTasks(), self.t))

        self.change(stores={"HD22": S.Store("HD22", ho_enabled=False, ho_reason="Renovation")})
        out = run(api.bill_start(api.BillStart(), self.t))
        self.assertEqual((out["allowed"], out["state"], out["reason"]), (False, S.OFF, S.STORE_OFF_BY_HO))
        self.assertEqual(out["message"], "Store disabled by HO: Renovation")
        self.assertEqual(out["poll_seconds"], api.POLL_SECONDS)

    def test_drain_finishes_on_bill_end_and_is_logged(self):
        run(api.bill_start(api.BillStart(), self.t))
        self.change(glob=S.Global(enabled=False, reason="Billing incident"))
        self.assertEqual(run(api.bill_start(api.BillStart(), self.t))["state"], S.DRAINING)  # no second bill
        tasks = BackgroundTasks()
        out = run(api.bill_end(api.BillEnd(outcome="paid"), request(), tasks, self.t))
        self.assertEqual(out["state"], S.OFF)
        self.assertEqual([t.args[0] for t in tasks.tasks], ["DRAIN_DONE"])
        self.assertEqual(tasks.tasks[0].kwargs["reason"], "bill paid")

    def test_bill_end_without_drain_logs_nothing(self):
        tasks = BackgroundTasks()
        run(api.bill_end(api.BillEnd(outcome="cancelled"), request(), tasks, self.t))
        self.assertEqual(tasks.tasks, [])

    def test_outcome_validated(self):
        with self.assertRaises(ValueError):
            api.BillEnd(outcome="refunded")

    # ------------------------------------------------------------ heartbeat

    def test_heartbeat(self):
        tasks = BackgroundTasks()
        out = run(api.heartbeat(api.Heartbeat(bill_open=True, serial="SN-1", app_version="2.4.0"),
                                request(), tasks, self.t))
        self.assertEqual(out["state"], S.ON)
        self.assertEqual(tasks.tasks, [])
        live = self.cache.live["HD22_MPOS1"]
        self.assertEqual((live.bill_open, live.app_version, live.ip), (True, "2.4.0", "10.0.0.5"))
        run(api.heartbeat(api.Heartbeat(bill_open=False, serial="SN-1", app_version="2.4.0", network="wifi",
                                        battery_pct=81), request(), BackgroundTasks(), self.t))
        first, second = self.beats.take()  # both check-ins wait to go to Snowflake
        self.assertEqual(first[:2] + first[3:], ("HD22_MPOS1", "HD22", True, S.ON, "2.4.0", "SN-1", "10.0.0.5", None, None))
        self.assertEqual(second[-2:], ("wifi", 81))
        self.assertEqual(second[2], live.last_seen)  # same moment in memory and in Snowflake
        self.assertLessEqual(first[2], second[2])

    def test_heartbeat_row_records_lock_given(self):
        run(api.heartbeat(api.Heartbeat(bill_open=False, serial="SN-999", app_version="2.4.0"),
                          request(), BackgroundTasks(), self.t))
        self.assertEqual(self.beats.take()[0][4], S.OFF)  # STATE_GIVEN = what the phone was told

    def test_stop_flushes_waiting_check_ins(self):
        stops = [mock.Mock(), mock.Mock()]
        with mock.patch.object(api.health, "flush") as flush:
            api.stop(stops)
        for event in stops:
            event.set.assert_called_once()
        flush.assert_called_once_with(self.beats)

    def test_token_from_another_phone_locks_at_once(self):
        tasks = BackgroundTasks()
        out = run(api.heartbeat(api.Heartbeat(bill_open=True, serial="SN-999", app_version="2.4.0"),
                                request(), tasks, self.t))
        self.assertEqual((out["state"], out["reason"]), (S.OFF, S.LOCKED))  # even with a bill open
        self.assertEqual([t.func for t in tasks.tasks], [api._save_lock])
        again = BackgroundTasks()
        run(api.heartbeat(api.Heartbeat(bill_open=False, serial="SN-999", app_version="2.4.0"),
                          request(), again, self.t))
        self.assertEqual(again.tasks, [])  # saved once, not on every heartbeat

    def test_save_lock_never_raises(self):
        with mock.patch.object(api.store, "lock_terminal", side_effect=store.sf.StoreUnavailable("down")), \
                self.assertLogs(api.log, "ERROR"):
            run(api._save_lock("HD22_MPOS1", "reason", "10.0.0.5"))

    # ------------------------------------------------------------ pairing

    def pair(self, ip="10.0.0.9"):
        return api.pair(api.Pair(terminal="hd22_mpos1", code="K7P2QX", serial="SN-1", app_version="2.4.0"),
                        request(ip))

    def test_pair_returns_token_and_reloads(self):
        with mock.patch.object(api.store, "redeem_pair_code", return_value="new-token") as redeem, \
                mock.patch.object(self.cache, "refresh_if_changed") as refresh:
            out = self.pair()
        self.assertEqual((out["token"], out["terminal_id"], out["state"]), ("new-token", "HD22_MPOS1", S.ON))
        self.assertEqual(redeem.call_args.kwargs["ip"], "10.0.0.9")
        refresh.assert_called_once()

    def test_pair_refusals(self):
        cases = [(store.PairingError("Wrong code - 4 tries left"), 400), (store.sf.StoreUnavailable("down"), 503)]
        for error, status in cases:
            with self.subTest(status=status), mock.patch.object(api.store, "redeem_pair_code", side_effect=error), \
                    self.assertRaises(HTTPException) as e:
                self.pair()
            self.assertEqual(e.exception.status_code, status)
            self.assertEqual(e.exception.detail, str(error) if status == 400 else
                             "Pairing is unavailable right now - try again shortly")

    def test_pair_waits_for_an_open_bill(self):
        run(api.bill_start(api.BillStart(), self.t))
        run(api.heartbeat(api.Heartbeat(bill_open=True, serial="SN-1", app_version="2.4.0"),
                          request(), BackgroundTasks(), self.t))
        with mock.patch.object(api.store, "redeem_pair_code") as redeem, self.assertRaises(HTTPException) as e:
            self.pair()
        self.assertEqual(e.exception.status_code, 409)
        self.assertEqual(e.exception.detail, "A bill is open on HD22_MPOS1 - finish it, then pair this phone")
        redeem.assert_not_called()  # the code is not used up

        run(api.bill_end(api.BillEnd(), request(), BackgroundTasks(), self.t))
        with mock.patch.object(api.store, "redeem_pair_code", return_value="new-token"):
            self.assertEqual(self.pair()["token"], "new-token")

    def test_phone_billing_on_another_terminal_cannot_pair(self):
        self.terminals["HD22_MPOS2"] = S.Terminal("HD22_MPOS2", "HD22", 2, token_hash=pairing.token_digest("t2"),
                                                  device_serial="SN-7")
        self.change()
        self.cache.start_bill("HD22_MPOS2")
        with self.assertRaises(HTTPException) as e:
            api.pair(api.Pair(terminal="HD22_MPOS1", code="K7P2QX", serial="SN-7", app_version="2.4.0"), request())
        self.assertEqual(e.exception.status_code, 409)
        self.assertIn("HD22_MPOS2", e.exception.detail)

    def test_silent_phone_does_not_block_pairing(self):
        self.cache.start_bill("HD22_MPOS1", now=datetime.now() - timedelta(seconds=C.GONE_SECONDS + 1))
        self.assertIsNone(self.cache.billing_phone("HD22_MPOS1", "SN-2"))  # lost mid-bill: re-pair allowed

    def test_pair_rate_limited(self):
        with mock.patch.object(api, "_pair_limit", pairing.RateLimit(limit=1, seconds=60)), \
                mock.patch.object(api.store, "redeem_pair_code", side_effect=store.PairingError("Wrong code")):
            with self.assertRaises(HTTPException):
                self.pair()
            with self.assertRaises(HTTPException) as e:
                self.pair()
        self.assertEqual(e.exception.status_code, 429)


if __name__ == "__main__":
    unittest.main()
