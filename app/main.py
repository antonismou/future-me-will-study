from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import auth, calfeed, notify, services, storage, studytime, tokens
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


def _d(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


ICONS = {
    "home": "M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",
    "clock": "M3 12a9 9 0 1 0 18 0a9 9 0 1 0-18 0M12 7v5l3 2",
    "book": "M4 19.5V5a2 2 0 0 1 2-2h14v14H6.5A2.5 2.5 0 0 0 4 19.5a2.5 2.5 0 0 0 2.5 2.5H20v-5",
    "sliders": "M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1M13 6a2 2 0 1 0 4 0a2 2 0 1 0-4 0"
               "M7 12a2 2 0 1 0 4 0a2 2 0 1 0-4 0M15 18a2 2 0 1 0 4 0a2 2 0 1 0-4 0",
    "file": "M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8zM14 3v5h5M9 13h6M9 17h4",
    "image": "M4 4h16a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zM7.5 10a1.5 1.5 0 1 0 3 0"
             "a1.5 1.5 0 1 0-3 0M21 16l-5-5-10 9",
    "flame": "M12 3c1 3 5 5.5 5 10.5a5 5 0 0 1-10 0c0-2.5 1.4-4.2 2.6-5.2.4 2 1.4 3.2 2.4 3.2-.8-3 0-6 0-8.5z",
    "hourglass": "M6 3h12M6 21h12M8 3v3.5l4 5.5-4 5.5V21M16 3v3.5L12 12l4 5.5V21",
    "calendar": "M4 5h16a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zM3 10h18M8 3v4M16 3v4",
    "check": "M5 12.5l4.5 4.5L19 7.5",
    "plus": "M12 5v14M5 12h14",
    "upload": "M12 16V4M7 9l5-5 5 5M5 20h14",
    "download": "M12 4v12M7 11l5 5 5-5M5 20h14",
    "edit": "M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4",
    "logout": "M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 16l-4-4 4-4M6 12h10",
    "chevron": "M9 6l6 6-6 6",
    "alert": "M12 4 2.8 19.5a.6.6 0 0 0 .5.9h17.4a.6.6 0 0 0 .5-.9zM12 10v4M12 17.2v.3",
    "bell": "M6 16v-5a6 6 0 0 1 12 0v5l2 2H4zM10 21h4",
    "trash": "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13",
    "link": "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 1 0 5.7 5.7l1-1",
    "folder": "M3 6a1 1 0 0 1 1-1h5l2 2h9a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z",
    "arrow": "M5 12h14M13 6l6 6-6 6",
    "note": "M5 4h14v12l-4 4H5zM15 20v-4h4M8 9h8M8 13h5",
    "dots": "M5.5 12h.01M12 12h.01M18.5 12h.01",
    "external": "M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5",
}
GR_DAYS_LONG = ["Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Σάββατο", "Κυριακή"]
GR_MONTHS_GEN = ["Ιανουαρίου", "Φεβρουαρίου", "Μαρτίου", "Απριλίου", "Μαΐου", "Ιουνίου", "Ιουλίου", "Αυγούστου",
                 "Σεπτεμβρίου", "Οκτωβρίου", "Νοεμβρίου", "Δεκεμβρίου"]


def icon(name: str, cls: str = "") -> Markup:
    return Markup(f'<svg class="i {cls}" viewBox="0 0 24 24" aria-hidden="true"><path d="{ICONS[name]}"/></svg>')


templates.env.filters["gdate"] = fmt_date
templates.env.filters["size"] = fmt_size
templates.env.filters["dnum"] = lambda v: _d(v).day
templates.env.filters["wday"] = lambda v: GR_DAYS[_d(v).weekday()]
templates.env.filters["mon"] = lambda v: GR_MONTHS[_d(v).month - 1]
templates.env.globals["icon"] = icon
templates.env.filters["dur"] = studytime.fmt_duration
templates.env.filters["longdate"] = lambda v: f"{GR_DAYS_LONG[_d(v).weekday()]} {_d(v).day} {GR_MONTHS_GEN[_d(v).month - 1]}"
templates.env.filters["daymonth"] = lambda v: f"{_d(v).day} {GR_MONTHS_GEN[_d(v).month - 1]}"
templates.env.globals["status_label"] = {"Ναι": "Ολοκληρώθηκε", "Σε εξέλιξη": "Σε εξέλιξη", "Όχι": "Εκκρεμεί"}
templates.env.globals["csrf_token"] = auth.csrf_token
templates.env.globals["STATUSES"] = services.STATUSES
templates.env.globals["status_class"] = {"Ναι": "done", "Σε εξέλιξη": "progress", "Όχι": "todo"}


def render(request: Request, name: str, ctx: dict | None = None, status_code: int = 200) -> HTMLResponse:
    ctx = dict(ctx or {})
    ctx.setdefault("flash", request.session.pop("flash", None))
    ctx.setdefault("user", request.session.get("user"))
    ctx.setdefault("path", request.url.path)
    ctx.setdefault("task_secs", {})
    if ctx.get("user") and "overdue_count" not in ctx:
        conn = connect()
        try:
            ctx["overdue_count"] = len(services.overdue(conn, services.local_today(conn)))
            ctx["running"] = studytime.running(conn)
        finally:
            conn.close()
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

    @app.api_route("/cal/{token}.ics", methods=["GET", "HEAD"])
    def calendar_feed(token: str, conn=Depends(get_conn)):
        if not calfeed.check_token(conn, token):
            raise StarletteHTTPException(404, "Το ημερολόγιο δεν βρέθηκε.")
        return Response(calfeed.build(conn), media_type="text/calendar; charset=utf-8",
                        headers={"Cache-Control": "private, no-store",
                                 "Content-Disposition": 'inline; filename="study.ics"'})

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
