"""Υπογεγραμμένα tokens (HMAC-SHA256) για τα κουμπιά του ntfy και τα links αρχείων."""
from __future__ import annotations

import base64
import hashlib
import hmac
import time

from .config import get_config


def _key(purpose: str) -> bytes:
    return hmac.new(get_config().secret_key.encode(), f"token:{purpose}".encode(), hashlib.sha256).digest()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign(purpose: str, *parts: object, ttl_seconds: int, now: float | None = None) -> str:
    exp = int((now if now is not None else time.time()) + ttl_seconds)
    payload = "|".join([*(str(p) for p in parts), str(exp)]).encode()
    mac = hmac.new(_key(purpose), payload, hashlib.sha256).digest()[:18]
    return f"{_b64(payload)}.{_b64(mac)}"


def verify(token: str, purpose: str, now: float | None = None) -> list[str] | None:
    try:
        p64, m64 = token.split(".", 1)
        payload, mac = _unb64(p64), _unb64(m64)
    except (ValueError, base64.binascii.Error):
        return None
    expected = hmac.new(_key(purpose), payload, hashlib.sha256).digest()[:18]
    if not hmac.compare_digest(mac, expected):
        return None
    parts = payload.decode().split("|")
    try:
        exp = int(parts[-1])
    except ValueError:
        return None
    if exp < (now if now is not None else time.time()):
        return None
    return parts[:-1]
