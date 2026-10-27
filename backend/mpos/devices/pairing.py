"""Pairing codes and phone tokens.

A pairing code is shown on a portal screen (as a QR code and as 6 characters) and typed or scanned on
the phone, together with its terminal ID. It works once, for CODE_MINUTES, and MAX_ATTEMPTS wrong
tries cancel it. Pairing gives the phone a token it sends on every call from then on.

Only SHA-256 hashes of codes and tokens are stored; the values themselves are shown or sent once.
"""
import hashlib
import secrets
import threading
import time
from collections import deque

CODE_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L: easy to read out and type
CODE_LENGTH = 6
CODE_MINUTES = 15
MAX_ATTEMPTS = 5


def new_code():
    return "".join(secrets.choice(CODE_CHARS) for _ in range(CODE_LENGTH))


def code_digest(code):
    """Hash of a code as typed: case, spaces and dashes ignored ('k7p-2qx' = 'K7P2QX')."""
    clean = "".join(ch for ch in (code or "").upper() if ch.isalnum())
    return hashlib.sha256(clean.encode()).hexdigest()


def new_token():
    return secrets.token_urlsafe(32)


def token_digest(token):
    """Hash of a token exactly as sent (tokens are case-sensitive)."""
    return hashlib.sha256((token or "").strip().encode()).hexdigest()


def qr_payload(url, terminal_id, code):
    """What the portal's QR code holds: where to pair, which terminal, and the code."""
    return {"url": url, "terminal": terminal_id, "code": code}


class RateLimit:
    """At most `limit` tries per `seconds` from one IP address (pairing guesses). Memory only."""

    def __init__(self, limit=10, seconds=60):
        self.limit, self.seconds = limit, seconds
        self._hits = {}
        self._lock = threading.Lock()

    def allow(self, key, now=None):
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self.seconds:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True
