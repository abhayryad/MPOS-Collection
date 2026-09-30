"""Store-location rules: HO sees every store, other users only their own (no database needed)."""
import unittest

from fastapi import HTTPException

from mpos.auth import locations as L
from mpos.auth.store import User, admin_user


def user(*locations):
    return User(username="u", full_name="U", roles=["SALE_POSTING"], locations=L.clean(locations))


class CleanTest(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(L.clean([" hd22", "DH24", "hd22", ""]), ["DH24", "HD22"])
        self.assertEqual(L.clean(["HD22", "ho"]), ["HO"])  # HO wins over store codes
        self.assertEqual(L.parse("HD22,DH24"), ["DH24", "HD22"])
        self.assertEqual(L.parse(None), [])


class StoresForTest(unittest.TestCase):
    def test_ho_and_admin_see_everything(self):
        self.assertIsNone(L.stores_for(user("HO")))
        self.assertIsNone(L.stores_for(admin_user()))
        self.assertEqual(L.stores_for(user("HO"), "gur001"), ["GUR001"])

    def test_store_user_limited_to_own_stores(self):
        u = user("HD22", "DH24")
        self.assertEqual(L.stores_for(u), ["DH24", "HD22"])  # "All stores" = their stores
        self.assertEqual(L.stores_for(u, "hd22"), ["HD22"])
        with self.assertRaises(HTTPException) as e:
            L.stores_for(u, "GUR001")
        self.assertEqual(e.exception.status_code, 403)

    def test_no_location_means_no_data(self):
        with self.assertRaises(HTTPException) as e:
            L.stores_for(user())
        self.assertEqual(e.exception.status_code, 403)

    def test_filter_store_list(self):
        self.assertEqual(L.filter_stores(user("HD22"), ["DH24", "GUR001", "HD22"]), ["HD22"])
        self.assertEqual(L.filter_stores(user("HO"), ["DH24", "HD22"]), ["DH24", "HD22"])


if __name__ == "__main__":
    unittest.main()
