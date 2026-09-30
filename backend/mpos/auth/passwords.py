"""Password hashing with scrypt (Python standard library). Only hashes are stored."""
import base64
import hashlib
import hmac
import os

N, R, P = 2 ** 14, 8, 1
MIN_LENGTH = 8


def _b64(b):
    return base64.b64encode(b).decode()


def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=32)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(digest)}"


def verify_password(password, stored):
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt),
                                n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def check_strength(password):
    """Error message for a password that is too weak, or None."""
    if len(password) < MIN_LENGTH:
        return f"Password must be at least {MIN_LENGTH} characters"
    if password.isdigit() or password.isalpha():
        return "Password must mix letters with numbers or symbols"
    return None
