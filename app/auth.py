"""Login ενός χρήστη (bcrypt από .env), CSRF και rate limiting."""
from __future__ import annotations

import hmac
import secrets
import threading
import time
from collections import defaultdict, deque

import bcrypt
from fastapi import HTTPException, Request

from .config import get_config

MAX_FAILS = 5
WINDOW = 15 * 60
_fails: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()


class LoginRequired(Exception):
    pass


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def is_rate_limited(ip: str) -> bool:
    now = time.time()
    with _lock:
        q = _fails[ip]
        while q and q[0] < now - WINDOW:
            q.popleft()
        return len(q) >= MAX_FAILS


def record_fail(ip: str) -> None:
    with _lock:
        _fails[ip].append(time.time())


def reset_fails(ip: str) -> None:
    with _lock:
        _fails.pop(ip, None)


def check_credentials(username: str, password: str) -> bool:
    cfg = get_config()
    user_ok = hmac.compare_digest(username.strip().encode(), cfg.admin_user.encode())
    try:
        pw_ok = bcrypt.checkpw(password.encode()[:72], cfg.admin_password_hash.encode())
    except ValueError:
        pw_ok = False
    return user_ok and pw_ok


def require_user(request: Request) -> str:
    user = request.session.get("user")
    if not user:
        raise LoginRequired()
    return user


def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        request.session["csrf"] = tok
    return tok


def check_csrf(request: Request, token: str | None) -> None:
    expected = request.session.get("csrf")
    if not expected or not token or not hmac.compare_digest(expected, token):
        raise HTTPException(status_code=403, detail="Μη έγκυρο CSRF token — ανανέωσε τη σελίδα.")


def safe_next(url: str | None, default: str = "/") -> str:
    if url and url.startswith("/") and not url.startswith("//") and "\\" not in url:
        return url
    return default
