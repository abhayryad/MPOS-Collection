"""Password hashing, session tokens and role handling (no Snowflake needed)."""
import base64
import json
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from mpos.auth import passwords, roles, sessions
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


if __name__ == "__main__":
    unittest.main()
