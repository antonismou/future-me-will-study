from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import auth, notify, services, storage, tokens
from .config import get_config
from .db import connect, get_conn, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
BASE = Path(__file__).parent
GR_DAYS = ["Δευ", "Τρι", "Τετ", "Πέμ", "Παρ", "Σάβ", "Κυρ"]
GR_MONTHS = ["Ιαν", "Φεβ", "Μαρ", "Απρ", "Μαΐ", "Ιουν", "Ιουλ", "Αυγ", "Σεπ", "Οκτ", "Νοε", "Δεκ"]

templates = Jinja2Templates(directory=str(BASE / "templates"))


def fmt_date(v) -> str:
    if not v:
        return ""
    d = v if isinstance(v, date) else date.fromisoformat(str(v)[:10])
    return f"{GR_DAYS[d.weekday()]} {d.day} {GR_MONTHS[d.month - 1]}"


def fmt_size(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB" if n >= 1024 * 1024 else f"{max(1, n // 1024)} KB"


templates.env.filters["gdate"] = fmt_date
templates.env.filters["size"] = fmt_size
templates.env.globals["csrf_token"] = auth.csrf_token
templates.env.globals["STATUSES"] = services.STATUSES
templates.env.globals["status_class"] = {"Ναι": "done", "Σε εξέλιξη": "progress", "Όχι": "todo"}


def render(request: Request, name: str, ctx: dict | None = None, status_code: int = 200) -> HTMLResponse:
    ctx = dict(ctx or {})
    ctx.setdefault("flash", request.session.pop("flash", None))
    ctx.setdefault("user", request.session.get("user"))
    ctx.setdefault("path", request.url.path)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def flash(request: Request, msg: str, kind: str = "ok") -> None:
    request.session["flash"] = {"msg": msg, "kind": kind}


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_config()
    init_db()
    thread = None
    if cfg.scheduler_enabled:
        from .scheduler import SchedulerThread

        thread = SchedulerThread()
        thread.start()
    yield
    if thread:
        thread.stop()


def create_app() -> FastAPI:
    cfg = get_config()
    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SessionMiddleware, secret_key=cfg.secret_key, session_cookie="study_session",
                       max_age=30 * 24 * 3600, same_site="lax", https_only=cfg.cookie_secure)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "frame-src 'self'; object-src 'self'; frame-ancestors 'self'; form-action 'self'")
        return resp

    app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

    @app.exception_handler(auth.LoginRequired)
    async def _login_required(request: Request, exc):
        nxt = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/login?next={nxt}", status_code=303)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith("/api/") or request.headers.get("accept", "").startswith("application/json"):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return render(request, "error.html", {"code": exc.status_code, "detail": exc.detail},
                      status_code=exc.status_code)

    # ------------------------------------------------------------ δημόσια endpoints

    @app.get("/healthz")
    def healthz():
        conn = connect()
        try:
            conn.execute("SELECT 1")
            row = conn.execute("SELECT value FROM meta WHERE key='last_tick'").fetchone()
        finally:
            conn.close()
        if cfg.scheduler_enabled and row:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(row["value"])
            if age > timedelta(minutes=5):
                return JSONResponse({"ok": False, "scheduler": "stalled"}, status_code=503)
        return {"ok": True, "last_tick": row["value"] if row else None}

    @app.get("/login")
    def login_form(request: Request, next: str = "/"):
        return render(request, "login.html", {"next": auth.safe_next(next)})

    @app.post("/login")
    def login(request: Request, username: str = Form(""), password: str = Form(""), csrf: str = Form(""),
              next: str = Form("/")):
        auth.check_csrf(request, csrf)
        ip = auth.client_ip(request)
        if auth.is_rate_limited(ip):
            return render(request, "login.html", {"next": next, "error": "Πολλές αποτυχημένες προσπάθειες. "
                                                  "Δοκίμασε ξανά σε 15 λεπτά."}, status_code=429)
        if not auth.check_credentials(username, password):
            auth.record_fail(ip)
            return render(request, "login.html", {"next": next, "error": "Λάθος στοιχεία."}, status_code=401)
        auth.reset_fails(ip)
        request.session.clear()
        request.session["user"] = cfg.admin_user
        auth.csrf_token(request)
        return RedirectResponse(auth.safe_next(next), status_code=303)

    @app.post("/logout")
    def logout(request: Request, csrf: str = Form("")):
        auth.check_csrf(request, csrf)
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    def _resolve_action(token: str):
        parts = tokens.verify(token, "act")
        if not parts or len(parts) != 3 or parts[1] not in notify.ACTIONS:
            raise StarletteHTTPException(403, "Μη έγκυρο ή ληγμένο link.")
        return int(parts[0]), parts[1], parts[2]

    @app.get("/a/{token}")
    def action_page(request: Request, token: str, conn=Depends(get_conn)):
        task_id, action, _ = _resolve_action(token)
        task = conn.execute("SELECT * FROM task WHERE id=?", (task_id,)).fetchone()
        if not task:
            raise StarletteHTTPException(404, "Η εργασία δεν υπάρχει πια.")
        return render(request, "action.html", {"task": task, "action": action, "token": token})

    @app.post("/a/{token}")
    def action(request: Request, token: str, conn=Depends(get_conn)):
        task_id, action, issued_date = _resolve_action(token)
        task = conn.execute("SELECT * FROM task WHERE id=?", (task_id,)).fetchone()
        if not task:
            raise StarletteHTTPException(404, "Η εργασία δεν υπάρχει πια.")
        if action == "postpone":
            # Ιδεμποτές: αναβάλλει μόνο αν η εργασία είναι ακόμα στην ημερομηνία της ειδοποίησης.
            if task["date"] == issued_date and task["status"] != "Ναι":
                new = services.default_postpone_date(date.fromisoformat(task["date"]), services.local_today(conn))
                services.postpone(conn, task_id, new)
            result = "postponed"
        else:
            services.set_status(conn, task_id, notify.ACTIONS[action])
            result = notify.ACTIONS[action]
        if "text/html" in request.headers.get("accept", ""):
            return render(request, "action.html", {"task": conn.execute("SELECT * FROM task WHERE id=?", (task_id,))
                                                   .fetchone(), "action": action, "token": token, "result": result})
        return {"ok": True, "task": task_id, "result": result}

    @app.get("/f/{token}")
    def signed_file(token: str, conn=Depends(get_conn)):
        parts = tokens.verify(token, "file")
        if not parts:
            raise StarletteHTTPException(403, "Το link έληξε. Άνοιξε το αρχείο από το dashboard.")
        row = conn.execute("SELECT * FROM file WHERE id=?", (int(parts[0]),)).fetchone()
        return _serve(row)

    def _serve(row):
        if not row:
            raise StarletteHTTPException(404, "Το αρχείο δεν βρέθηκε.")
        try:
            path = storage.file_path(row)
        except FileNotFoundError:
            raise StarletteHTTPException(404, "Το αρχείο δεν βρέθηκε.")
        if not path.exists():
            raise StarletteHTTPException(404, "Το αρχείο λείπει από τον δίσκο.")
        return FileResponse(path, media_type=row["mime"], filename=row["name"], content_disposition_type="inline",
                            headers={"Cache-Control": "private, max-age=3600"})

    app.state.serve_file = _serve

    from .web import router

    app.include_router(router)
    return app


def __getattr__(name: str):  # lazy: `uvicorn app.main:app`
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)
