"""mPOS device control for the portal screens: who may do what, and what reaches Snowflake (endpoint
functions called directly; Snowflake faked)."""
import unittest
from unittest import mock

from fastapi import HTTPException
from starlette.requests import Request

from mpos.api import devices_api as api
from mpos.auth import roles as R
from mpos.auth.store import User
from mpos.devices import cache as C
from mpos.devices import pairing, store
from mpos.devices import state as S

HO = User("ho1", "Head Office", roles=[R.DEVICES], locations=["HO"])
APPROVER = User("v1", "HD22 approver", roles=[R.DEVICES], locations=["HD22"])
COLLEAGUE = User("v9", "HD22 cashier lead", roles=[R.DEVICES], locations=["HD22"])
OTHER_STORE = User("v2", "DH24 approver", roles=[R.DEVICES], locations=["DH24"])
NO_ROLE = User("r1", "Reports only", roles=[R.REPORTS], locations=["HO"])
MASTER = [{"code": c, "name": n, "zone": "", "region": "", "state": ""}
          for c, n in (("DH24", "DEHRADUN"), ("HD22", "HALDWANI"), ("KN01", "KANPUR"))]


def request():
    return Request({"type": "http", "client": ("10.0.0.5", 50000), "headers": [], "scheme": "http",
                    "server": ("portal", 8028), "path": "/", "root_path": "", "query_string": b""})


def terminal(tid, store_code, seq, **kw):
    return S.Terminal(tid, store_code, seq, token_hash=pairing.token_digest(f"t-{tid}"),
                      device_serial=f"SN-{tid}", **kw)


class DevicesApiTest(unittest.TestCase):
    def setUp(self):
        self.glob = S.Global()
        self.stores = {"HD22": S.Store("HD22"), "DH24": S.Store("DH24")}
        self.terminals = {"HD22_MPOS1": terminal("HD22_MPOS1", "HD22", 1),
                          "HD22_MPOS2": S.Terminal("HD22_MPOS2", "HD22", 2),  # not paired yet
                          "DH24_MPOS1": terminal("DH24_MPOS1", "DH24", 1)}
        patches = [mock.patch.object(C.store, "change_seq", lambda: 1),
                   mock.patch.object(C.store, "load_global", lambda: self.glob),
                   mock.patch.object(C.store, "load_stores", lambda: dict(self.stores)),
                   mock.patch.object(C.store, "load_terminals", lambda: dict(self.terminals)),
                   mock.patch.object(api, "store_master", lambda: MASTER)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.cache = C.Cache()
        self.cache.load()
        p = mock.patch.object(api.cache, "current", self.cache)
        p.start()
        self.addCleanup(p.stop)
        self.writes = {}
        for name in ("set_global", "set_stores", "set_terminal", "set_veto", "set_lock", "set_day",
                     "set_store_settings", "add_terminals", "issue_pair_code", "unpair_terminal",
                     "retire_terminal", "load_history"):
            p = mock.patch.object(api.store, name)
            self.writes[name] = p.start()
            self.addCleanup(p.stop)
        self.writes["issue_pair_code"].return_value = "K7P2QX"
        self.writes["add_terminals"].return_value = (["KN01_MPOS1"], [])
        self.writes["set_stores"].return_value = 2

    def refused(self, status, fn, *args, **kw):
        with self.assertRaises(HTTPException) as e:
            fn(*args, **kw)
        self.assertEqual(e.exception.status_code, status, e.exception.detail)
        return e.exception.detail

    def reload(self, **kw):
        for name, value in kw.items():
            setattr(self, name, value)
        self.cache.load()

    # ------------------------------------------------------------ role and scope

    def test_needs_the_devices_role(self):
        self.assertIs(api.devices_user(HO), HO)
        self.assertIn("mPOS Devices", self.refused(403, api.devices_user, NO_ROLE))

    def test_overview_ho_sees_every_store(self):
        out = api.overview(HO)
        self.assertTrue(out["is_ho"])
        self.assertEqual([s["code"] for s in out["stores"]], ["DH24", "HD22", "KN01"])
        hd22 = out["stores"][1]
        self.assertEqual((hd22["name"], hd22["terminals"], hd22["on"], hd22["not_paired"]), ("HALDWANI", 2, 1, 1))
        self.assertEqual(out["totals"]["terminals"], 3)
        self.assertEqual([(p["id"], p["state"], p["paired"]) for p in hd22["phones"]],
                         [("HD22_MPOS1", S.ON, True), ("HD22_MPOS2", S.OFF, False)])
        self.assertEqual(hd22["silent"], 1)  # paired, never checked in since the site started
        self.cache.check_in("HD22_MPOS1", bill_open=False)
        self.assertEqual(api.overview(HO)["stores"][1]["silent"], 0)

    def test_overview_store_user_sees_only_their_store(self):
        out = api.overview(APPROVER)
        self.assertFalse(out["is_ho"])
        self.assertEqual([s["code"] for s in out["stores"]], ["HD22"])
        self.refused(403, api.store_detail, "DH24", APPROVER)

    def test_store_detail(self):
        out = api.store_detail("hd22", APPROVER)
        self.assertEqual([t["terminal_id"] for t in out["terminal_list"]], ["HD22_MPOS1", "HD22_MPOS2"])
        self.assertEqual([t["state"] for t in out["terminal_list"]], [S.ON, S.OFF])
        self.assertEqual(out["terminal_list"][1]["message"], "Not paired to a phone")
        self.assertTrue(out["can_change"])

    # ------------------------------------------------------------ store approver actions

    def test_approver_switches_own_terminal(self):
        out = api.switch_terminal("hd22_mpos1", api.OnOff(on=False, version=1), request(), APPROVER)
        self.writes["set_terminal"].assert_called_once_with("HD22_MPOS1", False, "v1", reason=None, version=1,
                                                            ip="10.0.0.5")
        self.assertEqual(out["terminal_id"], "HD22_MPOS1")

    def test_approver_cannot_touch_another_store(self):
        self.refused(403, api.switch_terminal, "DH24_MPOS1", api.OnOff(on=False), request(), APPROVER)
        self.refused(403, api.pair_code, "DH24_MPOS1", request(), APPROVER)
        self.writes["set_terminal"].assert_not_called()

    def test_named_approver_only(self):
        self.reload(stores={**self.stores, "HD22": S.Store("HD22", approver="v1")})
        api.switch_terminal("HD22_MPOS1", api.OnOff(on=False), request(), APPROVER)
        detail = self.refused(403, api.switch_terminal, "HD22_MPOS1", api.OnOff(on=False), request(), COLLEAGUE)
        self.assertEqual(detail, "Only HD22's mPOS approver (v1) can change its devices")
        self.assertFalse(api.store_detail("HD22", COLLEAGUE)["can_change"])  # may still look
        api.switch_terminal("HD22_MPOS1", api.OnOff(on=True), request(), HO)  # HO always may

    def test_pair_code_with_qr(self):
        out = api.pair_code("HD22_MPOS2", request(), APPROVER)
        self.assertEqual(out["code"], "K7P2QX")
        self.assertEqual(out["qr"], {"url": "http://portal:8028", "terminal": "HD22_MPOS2", "code": "K7P2QX"})
        self.assertEqual(out["valid_minutes"], 15)

    def test_unpair_and_retire_wait_for_the_open_bill(self):
        self.cache.start_bill("HD22_MPOS1")
        detail = self.refused(409, api.unpair, "HD22_MPOS1", api.Reason(), request(), APPROVER)
        self.assertEqual(detail, "A bill is open on HD22_MPOS1 - unpair it once it is finished")
        self.refused(409, api.retire, "HD22_MPOS1", api.Reason(), request(), HO)
        self.cache.end_bill("HD22_MPOS1")
        api.unpair("HD22_MPOS1", api.Reason(reason="sent for repair"), request(), APPROVER)
        self.writes["unpair_terminal"].assert_called_once_with("HD22_MPOS1", "v1", reason="sent for repair",
                                                               ip="10.0.0.5")

    def test_day(self):
        api.day("hd22", api.Day(open=True), request(), APPROVER)
        self.writes["set_day"].assert_called_once_with("HD22", True, "v1", ip="10.0.0.5")
        self.writes["set_day"].side_effect = store.Conflict("HD22 does not use Open day")
        self.assertEqual(self.refused(409, api.day, "HD22", api.Day(open=True), request(), APPROVER),
                         "HD22 does not use Open day")

    def test_unknown_or_retired_terminal(self):
        self.refused(404, api.switch_terminal, "HD22_MPOS9", api.OnOff(on=True), request(), HO)
        self.reload(terminals={**self.terminals, "HD22_MPOS1": terminal("HD22_MPOS1", "HD22", 1, status="RETIRED")})
        self.refused(404, api.pair_code, "HD22_MPOS1", request(), HO)

    # ------------------------------------------------------------ HO only

    def test_ho_only_actions_refuse_store_users(self):
        cases = [(api.switch_global, (api.OnOff(on=False, reason="x"),)),
                 (api.switch_stores, (api.Stores(on=False, reason="x"),)),
                 (api.veto, ("HD22_MPOS1", api.Block(active=True, reason="x"))),
                 (api.lock, ("HD22_MPOS1", api.Block(active=True, reason="x"))),
                 (api.retire, ("HD22_MPOS1", api.Reason())),
                 (api.add, (api.Add(stores=["HD22"]),)),
                 (api.settings, ("HD22", api.Settings(max_mpos=3)))]
        for fn, args in cases:
            with self.subTest(fn=fn.__name__):
                self.assertTrue(self.refused(403, fn, *args, request(), APPROVER).startswith("Only HO can"))
        for name, m in self.writes.items():
            m.assert_not_called()

    def test_switching_off_needs_a_reason(self):
        for fn, args in [(api.switch_global, (api.OnOff(on=False),)),
                         (api.switch_stores, (api.Stores(on=False, stores=["HD22"]),)),
                         (api.veto, ("HD22_MPOS1", api.Block(active=True))),
                         (api.lock, ("HD22_MPOS1", api.Block(active=True, reason="  ")))]:
            with self.subTest(fn=fn.__name__):
                self.assertIn("reason", self.refused(400, fn, *args, request(), HO))
        api.switch_global(api.OnOff(on=True), request(), HO)  # switching on needs none
        api.veto("HD22_MPOS1", api.Block(active=False), request(), HO)

    def test_ho_switches(self):
        api.switch_global(api.OnOff(on=False, reason="Billing incident", version=3), request(), HO)
        self.writes["set_global"].assert_called_once_with(False, "Billing incident", "ho1", version=3, ip="10.0.0.5")
        out = api.switch_stores(api.Stores(on=False, reason="Audit", stores=["HD22", "DH24"]), request(), HO)
        self.assertEqual(out, {"stores_changed": 2})
        self.writes["set_stores"].assert_called_once_with(["HD22", "DH24"], False, "Audit", "ho1", version=None,
                                                          ip="10.0.0.5")
        api.lock("HD22_MPOS1", api.Block(active=True, reason="Stolen"), request(), HO)
        self.writes["set_lock"].assert_called_once_with("HD22_MPOS1", True, "Stolen", "ho1", version=None,
                                                        ip="10.0.0.5")

    def test_add_to_some_or_every_active_store(self):
        self.assertEqual(api.add(api.Add(stores=["kn01"], count=2), request(), HO),
                         {"added": ["KN01_MPOS1"], "at_limit": []})
        self.writes["add_terminals"].assert_called_with(["KN01"], 2, "ho1", ip="10.0.0.5")
        api.add(api.Add(), request(), HO)
        self.writes["add_terminals"].assert_called_with(["DH24", "HD22", "KN01"], 1, "ho1", ip="10.0.0.5")
        self.assertIn("ZZ99", self.refused(400, api.add, api.Add(stores=["ZZ99"]), request(), HO))
        with self.assertRaises(ValueError):
            api.Add(count=S.MAX_MPOS + 1)

    def test_settings(self):
        api.settings("hd22", api.Settings(approver=" V1 ", day_close_time="22:30", version=4), request(), HO)
        self.writes["set_store_settings"].assert_called_once_with(
            "HD22", {"approver": "v1", "day_close_time": "22:30"}, "ho1", version=4, ip="10.0.0.5")
        api.settings("HD22", api.Settings(approver=""), request(), HO)
        self.assertEqual(self.writes["set_store_settings"].call_args.args[1], {"approver": None})  # cleared
        self.refused(400, api.settings, "HD22", api.Settings(day_close_time="25:00"), request(), HO)

    # ------------------------------------------------------------ errors and preview

    def test_snowflake_errors_become_answers(self):
        for error, status in [(store.Conflict("changed by someone else"), 409),
                              (store.sf.StoreUnavailable("down"), 503), (ValueError("No active terminal"), 404)]:
            with self.subTest(status=status):
                self.writes["set_terminal"].side_effect = error
                self.refused(status, api.switch_terminal, "HD22_MPOS1", api.OnOff(on=True), request(), HO)

    def test_writes_wait_until_the_switches_are_loaded(self):
        self.cache.seq = None
        self.refused(503, api.switch_terminal, "HD22_MPOS1", api.OnOff(on=True), request(), HO)

    def test_preview(self):
        self.cache.start_bill("HD22_MPOS1")
        self.assertEqual(api.preview(api.Preview(level="global"), HO), {"phones": 2, "billing_now": 1, "stores": 2})
        self.assertEqual(api.preview(api.Preview(level="stores", targets=["dh24"]), HO),
                         {"phones": 1, "billing_now": 0, "stores": 1})
        self.assertEqual(api.preview(api.Preview(level="global"), APPROVER)["phones"], 1)  # own store only

    def test_history_scope(self):
        api.history(store_code="hd22", user=APPROVER)
        self.writes["load_history"].assert_called_with("HD22", None, 200)
        api.history(terminal="HD22_MPOS1", user=APPROVER)
        self.writes["load_history"].assert_called_with("HD22", "HD22_MPOS1", 200)
        self.refused(403, api.history, store_code="DH24", user=APPROVER)
        self.refused(400, api.history, user=APPROVER)  # store users must pick their store
        api.history(user=HO)
        self.writes["load_history"].assert_called_with(None, None, 200)


if __name__ == "__main__":
    unittest.main()
