"""Password hashing, session tokens and role handling (no Snowflake needed)."""
import base64
import json
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from mpos.api import admin
from mpos.auth import locations, passwords, roles, sessions, store
from mpos.config import settings


class PasswordTest(unittest.TestCase):
    def test_hash_verifies_and_is_salted(self):
        h1, h2 = passwords.hash_password("Secret123"), passwords.hash_password("Secret123")
        self.assertNotEqual(h1, h2)  # random salt
        self.assertNotIn("Secret123", h1)
        self.assertTrue(passwords.verify_password("Secret123", h1))
        self.assertFalse(passwords.verify_password("secret123", h1))
        self.assertFalse(passwords.verify_password("Secret123", "garbage"))

    def test_strength(self):
        self.assertIsNotNone(passwords.check_strength("short1"))
        self.assertIsNotNone(passwords.check_strength("12345678"))
        self.assertIsNotNone(passwords.check_strength("abcdefgh"))
        self.assertIsNone(passwords.check_strength("abcd1234"))


class SessionTest(unittest.TestCase):
    def setUp(self):
        test_settings = SimpleNamespace(secret_key="test-key", session_hours=settings.session_hours)
        patcher = mock.patch.object(sessions, "settings", test_settings)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_uses_patched_key(self):
        token = sessions.make_token("v25151")
        with mock.patch.object(sessions, "settings", SimpleNamespace(secret_key="other-key", session_hours=8)):
            self.assertIsNone(sessions.read_token(token))  # a different key can't read it

    def test_round_trip(self):
        self.assertEqual(sessions.read_token(sessions.make_token("v25151")), "v25151")

    def test_tampered_or_garbage_rejected(self):
        token = sessions.make_token("v25151")
        body, sig = token.rsplit(".", 1)
        forged = base64.urlsafe_b64encode(json.dumps({"u": "admin", "exp": time.time() + 999}).encode()).decode()
        self.assertIsNone(sessions.read_token(forged + "." + sig))
        self.assertIsNone(sessions.read_token(body + "." + "0" * 64))
        self.assertIsNone(sessions.read_token(""))
        self.assertIsNone(sessions.read_token("not-a-token"))

    def test_expired_rejected(self):
        with mock.patch.object(sessions.time, "time", return_value=time.time() - 10 * 3600):
            token = sessions.make_token("v25151")
        self.assertIsNone(sessions.read_token(token))


class RolesTest(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(roles.clean([" reports", "SALE_POSTING", "ADMIN", "bogus", "REPORTS"]),
                         ["SALE_POSTING", "REPORTS"])  # ADMIN can't be assigned; order fixed

    def test_admin_user_row(self):
        row = {"USERNAME": "v1", "FULL_NAME": "A", "ROLES": "", "LOCATIONS": "HD22", "IS_ADMIN": True,
               "IS_ACTIVE": True, "MUST_CHANGE_PASSWORD": False, "CREATED_AT": None, "CREATED_BY": None,
               "UPDATED_AT": None, "UPDATED_BY": None, "LAST_LOGIN_AT": None}
        admin = store._user(row)
        self.assertTrue(admin.is_admin and not admin.is_builtin)
        self.assertTrue(admin.can(roles.REPORTS))
        self.assertIsNone(locations.allowed_stores(admin))  # every store
        plain = store._user({**row, "IS_ADMIN": False})
        self.assertFalse(plain.is_admin or plain.can(roles.REPORTS))
        self.assertTrue(store.admin_user().is_builtin)



class AdminScopeTest(unittest.TestCase):
    def user(self, locations, is_admin=False):
        return store.User(username="u", full_name="U", locations=locations, is_admin=is_admin)

    def test_superadmin_manages_everyone(self):
        root = store.admin_user()
        self.assertIsNone(admin.scope(root))
        self.assertTrue(admin.can_manage(root, self.user(["HO"], is_admin=True)))

    def test_ho_admin_manages_normal_users_only(self):
        ho = self.user(["HO"], is_admin=True)
        self.assertIsNone(admin.scope(ho))
        self.assertTrue(admin.can_manage(ho, self.user(["HO"])))
        self.assertFalse(admin.can_manage(ho, self.user(["HD22"], is_admin=True)))

    def test_store_admin_manages_only_users_wholly_in_their_stores(self):
        a = self.user(["DH24", "HD22"], is_admin=True)
        self.assertEqual(admin.scope(a), {"DH24", "HD22"})
        self.assertTrue(admin.can_manage(a, self.user(["HD22"])))
        self.assertFalse(admin.can_manage(a, self.user(["HD22", "XX01"])))
        self.assertFalse(admin.can_manage(a, self.user(["HO"])))
        self.assertFalse(admin.can_manage(a, self.user([])))


if __name__ == "__main__":
    unittest.main()


class StoreReconnectTest(unittest.TestCase):
    """An expired Snowflake login (e.g. overnight) reconnects instead of failing every request."""

    def _conn(self, error=None):
        cur = mock.Mock()
        cur.execute.side_effect = error
        cur.fetchall.return_value = [{"N": 1}]
        return mock.Mock(is_closed=mock.Mock(return_value=False), cursor=mock.Mock(return_value=cur))

    def _run_with(self, stale, fresh):
        with mock.patch.object(store, "_conn", stale), mock.patch.object(store, "_tables_ready", True), \
                mock.patch.object(store, "_connect", return_value=fresh) as connect:
            return store._run("SELECT 1"), connect

    def test_expired_token_reconnects(self):
        expired = store.snowflake.connector.errors.ProgrammingError(msg="Authentication token has expired", errno=390114)
        rows, connect = self._run_with(self._conn(expired), self._conn())
        self.assertEqual(rows, [{"N": 1}])
        connect.assert_called_once()

    def test_bad_sql_is_not_retried(self):
        bad = store.snowflake.connector.errors.ProgrammingError(msg="syntax error", errno=1003)
        with self.assertRaises(store.snowflake.connector.errors.ProgrammingError):
            self._run_with(self._conn(bad), self._conn())
