"""Signed session tokens for the login cookie (HMAC-SHA256 with MPOS_SECRET_KEY)."""
import base64
import hashlib
import hmac
import json
import time

from ..config import settings

COOKIE = "mpos_session"


def _sign(payload: bytes) -> str:
    if not settings.secret_key:
        raise RuntimeError("MPOS_SECRET_KEY is not set in .env")
    return hmac.new(settings.secret_key.encode(), payload, hashlib.sha256).hexdigest()


def make_token(username):
    payload = json.dumps({"u": username, "exp": int(time.time()) + settings.session_hours * 3600},
                         separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode() + "." + _sign(payload)


def read_token(token):
    """Username from a valid, unexpired token, else None."""
    try:
        body, sig = token.rsplit(".", 1)
        payload = base64.urlsafe_b64decode(body.encode())
        if not hmac.compare_digest(sig, _sign(payload)):
            return None
        data = json.loads(payload)
        return data["u"] if data["exp"] > time.time() else None
    except (ValueError, KeyError, TypeError):
        return None
