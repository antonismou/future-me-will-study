"""Σελίδες του dashboard (απαιτούν login)."""
from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta
from zoneinfo import available_timezones

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from starlette.exceptions import HTTPException

from . import auth, exporter, importer, linker, notify, services, storage
from .config import get_config
from .db import get_conn
from .settings_store import WEEKDAYS, effective_token, get_settings, parse_form, save_settings

router = APIRouter(dependencies=[Depends(auth.require_user)])


def _render(request, name, ctx=None, status_code=200):
    from .main import render

    return render(request, name, ctx, status_code)


def _flash(request, msg, kind="ok"):
    from .main import flash

    flash(request, msg, kind)


def _back(request: Request, nxt: str | None, default: str = "/") -> RedirectResponse:
    return RedirectResponse(auth.safe_next(nxt, default), status_code=303)


def _course(conn, slug: str) -> sqlite3.Row:
    c = services.course_by_slug(conn, slug)
    if not c:
        raise HTTPException(404, "Το μάθημα δεν βρέθηκε.")
    return c


def _task(conn, task_id: int) -> sqlite3.Row:
    t = conn.execute("SELECT * FROM task WHERE id=?", (task_id,)).fetchone()
    if not t:
        raise HTTPException(404, "Η εργασία δεν βρέθηκε.")
    return t


def _parse_date(v: str, field: str = "ημερομηνία") -> date:
    try:
        return date.fromisoformat(v.strip())
    except (ValueError, AttributeError):
        raise HTTPException(400, f"Μη έγκυρη {field}: {v!r}")


# ---------------------------------------------------------------- Σήμερα / καθυστερήσεις

@router.get("/")
def today_page(request: Request, conn=Depends(get_conn)):
    today = services.local_today(conn)
    cards = []
    for c in services.courses(conn, active_only=True):
        all_tasks = services.tasks(conn, c["id"])
        todays = [t for t in all_tasks if t["date"] == today.isoformat()]
        upcoming = next((t for t in all_tasks if t["date"] > today.isoformat()), None)
        cards.append({
            "course": c, "tasks": todays, "files": services.task_files(conn, [t["id"] for t in todays]),
            "progress": services.progress(all_tasks), "overdue": len(services.overdue(conn, today, c["id"])),
            "streak": services.streak_from(all_tasks, today), "days_left": services.days_left(c["exam_date"], today),
            "upcoming": upcoming,
        })
    return _render(request, "today.html", {"cards": cards, "today": today,
                                           "tomorrow": today + timedelta(days=1)})


@router.get("/overdue")
def overdue_page(request: Request, conn=Depends(get_conn)):
    today = services.local_today(conn)
    items = services.overdue(conn, today)
    return _render(request, "overdue.html", {"items": items, "files": services.task_files(conn, [t["id"] for t in items]),
                                             "today": today, "tomorrow": today + timedelta(days=1)})


# ---------------------------------------------------------------- ενέργειες εργασιών

@router.post("/tasks/{task_id}/status")
def task_status(request: Request, task_id: int, status: str = Form(...), csrf: str = Form(""), next: str = Form("/"),
                conn=Depends(get_conn)):
    auth.check_csrf(request, csrf)
    _task(conn, task_id)
    if not services.set_status(conn, task_id, status):
        raise HTTPException(400, "Μη έγκυρη κατάσταση.")
    return _back(request, next)


@router.post("/tasks/{task_id}/notes")
def task_notes(request: Request, task_id: int, notes: str = Form(""), csrf: str = Form(""), next: str = Form("/"),
               conn=Depends(get_conn)):
    auth.check_csrf(request, csrf)
    _task(conn, task_id)
    services.set_notes(conn, task_id, notes)
    _flash(request, "Οι σημειώσεις αποθηκεύτηκαν.")
    return _back(request, next)


@router.post("/tasks/{task_id}/postpone")
def task_postpone(request: Request, task_id: int, new_date: str = Form(""), csrf: str = Form(""),
                  next: str = Form("/"), conn=Depends(get_conn)):
    auth.check_csrf(request, csrf)
    t = _task(conn, task_id)
    today = services.local_today(conn)
    d = _parse_date(new_date) if new_date else services.default_postpone_date(date.fromisoformat(t["date"]), today)
    services.postpone(conn, task_id, d)
    _flash(request, f"Η εργασία μεταφέρθηκε στις {d.strftime('%d/%m')}.")
    return _back(request, next)


@router.get("/tasks/{task_id}/edit")
def task_edit_form(request: Request, task_id: int, conn=Depends(get_conn)):
    t = _task(conn, task_id)
    c = conn.execute("SELECT * FROM course WHERE id=?", (t["course_id"],)).fetchone()
    return _task_form(request, conn, c, t)


def _task_form(request, conn, course, task):
    files = conn.execute("SELECT * FROM file WHERE course_id=? ORDER BY category, grp, name", (course["id"],)).fetchall()
    linked = {}
    if task:
        linked = {r["file_id"]: r["is_primary"] for r in
                  conn.execute("SELECT file_id, is_primary FROM task_file WHERE task_id=?", (task["id"],))}
    return _render(request, "task_form.html", {"course": course, "task": task, "weeks": services.weeks(conn, course["id"]),
                                               "files": files, "linked": linked})


@router.get("/c/{slug}/tasks/new")
def task_new_form(request: Request, slug: str, conn=Depends(get_conn)):
    return _task_form(request, conn, _course(conn, slug), None)


async def _save_task(request: Request, conn, course, task_id: int | None):
    form = await request.form()
    auth.check_csrf(request, form.get("csrf"))
    d = _parse_date(str(form.get("date", "")))
    week_id = form.get("week_id") or None
    if week_id is not None:
        week_id = int(week_id)
        if not conn.execute("SELECT 1 FROM week WHERE id=? AND course_id=?", (week_id, course["id"])).fetchone():
            week_id = None
    day_no = str(form.get("day_no", "")).strip()
    day = int(day_no) if day_no.isdigit() else None
    status = form.get("status", "Όχι")
    if status not in services.STATUSES:
        status = "Όχι"
    vals = (week_id, day, d.isoformat(), str(form.get("topic", ""))[:300], str(form.get("description", ""))[:2000],
            str(form.get("source", ""))[:500], str(form.get("notes", ""))[:5000])
    if task_id is None:
        task_id = conn.execute(
            """INSERT INTO task(course_id, week_id, day_no, date, topic, description, source, notes, status)
               VALUES(?,?,?,?,?,?,?,?,?)""", (course["id"], *vals, status)).lastrowid
    else:
        conn.execute("UPDATE task SET week_id=?, day_no=?, date=?, topic=?, description=?, source=?, notes=? WHERE id=?",
                     (*vals, task_id))
    services.set_status(conn, task_id, status)
    file_ids = {int(x) for x in form.getlist("files") if str(x).isdigit()}
    primary = form.get("primary_file")
    primary = int(primary) if primary and str(primary).isdigit() else None
    valid = {r["id"] for r in conn.execute("SELECT id FROM file WHERE course_id=?", (course["id"],))}
    file_ids &= valid
    if primary in valid:
        file_ids.add(primary)
    conn.execute("DELETE FROM task_file WHERE task_id=?", (task_id,))
    for fid in file_ids:
        conn.execute("INSERT INTO task_file(task_id, file_id, is_primary) VALUES(?,?,?)", (task_id, fid, int(fid == primary)))
    _flash(request, "Η εργασία αποθηκεύτηκε.")
    return RedirectResponse(f"/c/{course['slug']}#task-{task_id}", status_code=303)


@router.post("/c/{slug}/tasks/new")
async def task_create(request: Request, slug: str, conn=Depends(get_conn)):
    return await _save_task(request, conn, _course(conn, slug), None)


@router.post("/tasks/{task_id}/edit")
async def task_update(request: Request, task_id: int, conn=Depends(get_conn)):
    t = _task(conn, task_id)
    c = conn.execute("SELECT * FROM course WHERE id=?", (t["course_id"],)).fetchone()
    return await _save_task(request, conn, c, task_id)


@router.post("/tasks/{task_id}/delete")
def task_delete(request: Request, task_id: int, csrf: str = Form(""), conn=Depends(get_conn)):
    auth.check_csrf(request, csrf)
    t = _task(conn, task_id)
    c = conn.execute("SELECT slug FROM course WHERE id=?", (t["course_id"],)).fetchone()
    conn.execute("DELETE FROM task WHERE id=?", (task_id,))
    _flash(request, "Η εργασία διαγράφηκε.")
    return RedirectResponse(f"/c/{c['slug']}", status_code=303)


# ---------------------------------------------------------------- Μαθήματα / πλάνο

@router.get("/courses")
def courses_page(request: Request, conn=Depends(get_conn)):
    today = services.local_today(conn)
    rows = []
    for c in services.courses(conn):
        rows.append({"course": c, "progress": services.progress(services.tasks(conn, c["id"])),
                     "days_left": services.days_left(c["exam_date"], today),
                     "files": conn.execute("SELECT COUNT(*) FROM file WHERE course_id=?", (c["id"],)).fetchone()[0]})
    return _render(request, "courses.html", {"rows": rows})


def _save_template(course_id: int, data: bytes) -> str:
    path = get_config().templates_dir / f"course_{course_id}.xlsx"
    path.write_bytes(data)
    return path.name


async def _read_upload(up: UploadFile | None, max_mb: int = 10) -> bytes | None:
    if up is None or not up.filename:
        return None
    data = await up.read(max_mb * 1024 * 1024 + 1)
    if len(data) > max_mb * 1024 * 1024:
        raise HTTPException(413, f"Το αρχείο {up.filename} είναι μεγαλύτερο από {max_mb} MB.")
    return data


@router.post("/courses")
async def course_create(request: Request, name: str = Form(""), exam_date: str = Form(""), color: str = Form("#2563eb"),
                        csrf: str = Form(""), xlsx: UploadFile | None = File(None), ics: UploadFile | None = File(None),
                        year: str = Form(""), conn=Depends(get_conn)):
    auth.check_csrf(request, csrf)
    xdata = await _read_upload(xlsx)
    idata = await _read_upload(ics, 2)
    plan = None
    if xdata:
        try:
            plan = importer.parse_xlsx(xdata, int(year) if year.isdigit() else None)
        except importer.ImportError_ as e:
            _flash(request, str(e), "error")
            return RedirectResponse("/courses", status_code=303)
    name = name.strip() or (plan.title if plan else "")
    if not name:
        _flash(request, "Δώσε όνομα μαθήματος.", "error")
        return RedirectResponse("/courses", status_code=303)
    if exam_date:
        _parse_date(exam_date, "ημερομηνία εξέτασης")
    if not re.match(r"^#[0-9a-fA-F]{6}$", color):
        color = "#2563eb"
    cid = conn.execute("INSERT INTO course(name, slug, exam_date, color) VALUES(?,?,?,?)",
                       (name[:120], services.unique_slug(conn, name), exam_date or None, color)).lastrowid
    msg = f"Το μάθημα «{name}» δημιουργήθηκε."
    if plan:
        ics_map = importer.parse_ics(idata) if idata else None
        stats = importer.apply_plan(conn, cid, plan, ics_map)
        conn.execute("UPDATE course SET template_path=? WHERE id=?", (_save_template(cid, xdata), cid))
        msg += f" Εισήχθησαν {stats['created']} εργασίες σε {len(plan.weeks)} εβδομάδες."
    _flash(request, msg)
    slug = conn.execute("SELECT slug FROM course WHERE id=?", (cid,)).fetchone()["slug"]
    return RedirectResponse(f"/c/{slug}", status_code=303)


@router.get("/c/{slug}")
def plan_page(request: Request, slug: str, conn=Depends(get_conn)):
    c = _course(conn, slug)
    today = services.local_today(conn)
    wp = services.week_progress(conn, c["id"])
    all_tasks = [t for w in wp for t in w["tasks"]]
    return _render(request, "plan.html", {
        "course": c, "weeks": wp, "total": services.progress(all_tasks), "today": today,
        "files": services.task_files(conn, [t["id"] for t in all_tasks]), "tomorrow": today + timedelta(days=1),
        "streak": services.streak_from(all_tasks, today), "days_left": services.days_left(c["exam_date"], today)})


@router.post("/c/{slug}/weeks")
def week_add(request: Request, slug: str, title: str = Form(...), summary: str = Form(""), csrf: str = Form(""),
             conn=Depends(get_conn)):
    auth.check_csrf(request, csrf)
    c = _course(conn, slug)
    pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM week WHERE course_id=?", (c["id"],)).fetchone()[0]
    conn.execute("INSERT INTO week(course_id, position, title, summary) VALUES(?,?,?,?)",
                 (c["id"], pos, title.strip()[:120] or "Εβδομάδα", summary.strip()[:500]))
    return RedirectResponse(f"/c/{slug}", status_code=303)


@router.post("/weeks/{week_id}/delete")
def week_delete(request: Request, week_id: int, csrf: str = Form(""), conn=Depends(get_conn)):
    auth.check_csrf(request, csrf)
    w = conn.execute("SELECT w.*, c.slug FROM week w JOIN course c ON c.id=w.course_id WHERE w.id=?", (week_id,)).fetchone()
    if not w:
        raise HTTPException(404, "Η εβδομάδα δεν βρέθηκε.")
    conn.execute("DELETE FROM week WHERE id=?", (week_id,))
    _flash(request, "Η εβδομάδα διαγράφηκε (οι εργασίες της έμειναν χωρίς εβδομάδα).")
    return RedirectResponse(f"/c/{w['slug']}", status_code=303)


@router.get("/c/{slug}/settings")
def course_settings(request: Request, slug: str, conn=Depends(get_conn)):
    return _render(request, "course_settings.html", {"course": _course(conn, slug)})


@router.post("/c/{slug}/settings")
def course_settings_save(request: Request, slug: str, name: str = Form(...), exam_date: str = Form(""),
                         color: str = Form("#2563eb"), active: str = Form(""), notify_on: str = Form(""),
                         csrf: str = Form(""), conn=Depends(get_conn)):
    auth.check_csrf(request, csrf)
    c = _course(conn, slug)
    if exam_date:
        _parse_date(exam_date, "ημερομηνία εξέτασης")
    if not re.match(r"^#[0-9a-fA-F]{6}$", color):
        color = c["color"]
    conn.execute("UPDATE course SET name=?, exam_date=?, color=?, active=?, notify=? WHERE id=?",
                 (name.strip()[:120] or c["name"], exam_date or None, color, int(active == "on"),
                  int(notify_on == "on"), c["id"]))
    _flash(request, "Το μάθημα ενημερώθηκε.")
    return RedirectResponse(f"/c/{slug}/settings", status_code=303)


@router.post("/c/{slug}/import")
async def course_import(request: Request, slug: str, xlsx: UploadFile = File(...), ics: UploadFile | None = File(None),
                        mode: str = Form("merge"), year: str = Form(""), csrf: str = Form(""), conn=Depends(get_conn)):
    auth.check_csrf(request, csrf)
    c = _course(conn, slug)
    xdata = await _read_upload(xlsx)
    idata = await _read_upload(ics, 2)
    if not xdata:
        _flash(request, "Διάλεξε αρχείο xlsx.", "error")
        return RedirectResponse(f"/c/{slug}/settings", status_code=303)
    try:
        plan = importer.parse_xlsx(xdata, int(year) if year.isdigit() else None)
    except importer.ImportError_ as e:
        _flash(request, str(e), "error")
        return RedirectResponse(f"/c/{slug}/settings", status_code=303)
    stats = importer.apply_plan(conn, c["id"], plan, importer.parse_ics(idata) if idata else None,
                                "replace" if mode == "replace" else "merge")
    conn.execute("UPDATE course SET template_path=? WHERE id=?", (_save_template(c["id"], xdata), c["id"]))
    _flash(request, f"Εισαγωγή: {stats['created']} νέες, {stats['updated']} ενημερωμένες εργασίες.")
    return RedirectResponse(f"/c/{slug}", status_code=303)


def _download(data: bytes, filename: str, mime: str) -> Response:
    from urllib.parse import quote

    return Response(data, media_type=mime, headers={
        "Content-Disposition": f"attachment; filename=\"export.{filename.rsplit('.', 1)[-1]}\"; "
                               f"filename*=UTF-8''{quote(filename)}"})


@router.get("/c/{slug}/export.xlsx")
def export_xlsx(slug: str, kind: str = "auto", conn=Depends(get_conn)):
    c = _course(conn, slug)
    rows = services.tasks(conn, c["id"])
    tpl = get_config().templates_dir / c["template_path"] if c["template_path"] else None
    stamp = services.local_today(conn).strftime("%Y%m%d")
    xmime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if kind != "new" and tpl and tpl.exists():
        return _download(exporter.export_template(tpl.read_bytes(), rows), f"{c['slug']}_tracker_{stamp}.xlsx", xmime)
    return _download(exporter.export_new(c, services.weeks(conn, c["id"]), rows), f"{c['slug']}_{stamp}.xlsx", xmime)


@router.get("/c/{slug}/export.csv")
def export_csv(slug: str, conn=Depends(get_conn)):
    c = _course(conn, slug)
    titles = {w["id"]: w["title"] for w in services.weeks(conn, c["id"])}
    stamp = services.local_today(conn).strftime("%Y%m%d")
    return _download(exporter.export_csv(services.tasks(conn, c["id"]), titles), f"{c['slug']}_{stamp}.csv",
                     "text/csv; charset=utf-8")


@router.post("/c/{slug}/delete")
def course_delete(request: Request, slug: str, confirm: str = Form(""), csrf: str = Form(""), conn=Depends(get_conn)):
    auth.check_csrf(request, csrf)
    c = _course(conn, slug)
    if confirm.strip() != c["name"]:
        _flash(request, "Για διαγραφή γράψε ακριβώς το όνομα του μαθήματος.", "error")
        return RedirectResponse(f"/c/{slug}/settings", status_code=303)
    for f in conn.execute("SELECT * FROM file WHERE course_id=?", (c["id"],)).fetchall():
        storage.delete_file(conn, f)
    if c["template_path"]:
        (get_config().templates_dir / c["template_path"]).unlink(missing_ok=True)
    conn.execute("DELETE FROM course WHERE id=?", (c["id"],))
    _flash(request, f"Το μάθημα «{c['name']}» διαγράφηκε.")
    return RedirectResponse("/courses", status_code=303)


# ---------------------------------------------------------------- Αρχεία

@router.get("/c/{slug}/files")
def files_page(request: Request, slug: str, conn=Depends(get_conn)):
    c = _course(conn, slug)
    rows = conn.execute("SELECT * FROM file WHERE course_id=? ORDER BY category, grp, name", (c["id"],)).fetchall()
    tree: dict[str, dict[str, list]] = {}
    for r in rows:
        tree.setdefault(r["category"], {}).setdefault(r["grp"], []).append(r)
    ordered = [(cat, tree[cat]) for cat in storage.CATEGORIES if cat in tree]
    return _render(request, "files.html", {"course": c, "tree": ordered, "categories": storage.CATEGORIES,
                                           "max_mb": get_config().max_upload_mb, "count": len(rows),
                                           "total": sum(r["size"] for r in rows)})


@router.post("/c/{slug}/files")
async def files_upload(request: Request, slug: str, conn=Depends(get_conn)):
    c = _course(conn, slug)
    form = await request.form()
    auth.check_csrf(request, form.get("csrf"))
    category = str(form.get("category", "Άλλο"))
    grp = str(form.get("grp", ""))
    ok, dup, errors = 0, 0, []
    for up in form.getlist("files"):
        if not getattr(up, "filename", None):
            continue
        try:
            _, new = await storage.save_upload(conn, c["id"], up, category, grp)
            ok += new
            dup += not new
        except storage.UploadRejected as e:
            errors.append(str(e))
    linked = linker.relink_course(conn, c["id"])
    parts = [f"Ανέβηκαν {ok} αρχεία"]
    if dup:
        parts.append(f"{dup} υπήρχαν ήδη")
    if linked:
        parts.append(f"{linked} νέες συνδέσεις με εργασίες")
    _flash(request, ", ".join(parts) + "." + (" Απορρίφθηκαν: " + "; ".join(errors) if errors else ""),
           "error" if errors else "ok")
    return RedirectResponse(f"/c/{slug}/files", status_code=303)


@router.post("/c/{slug}/relink")
def files_relink(request: Request, slug: str, csrf: str = Form(""), conn=Depends(get_conn)):
    auth.check_csrf(request, csrf)
    c = _course(conn, slug)
    n = linker.relink_course(conn, c["id"])
    _flash(request, f"{n} νέες συνδέσεις αρχείων με εργασίες.")
    return RedirectResponse(f"/c/{slug}/files", status_code=303)


@router.post("/files/{file_id}/edit")
def file_edit(request: Request, file_id: int, category: str = Form(...), grp: str = Form(""), csrf: str = Form(""),
              conn=Depends(get_conn)):
    auth.check_csrf(request, csrf)
    f = conn.execute("SELECT f.*, c.slug FROM file f JOIN course c ON c.id=f.course_id WHERE f.id=?", (file_id,)).fetchone()
    if not f:
        raise HTTPException(404, "Το αρχείο δεν βρέθηκε.")
    conn.execute("UPDATE file SET category=?, grp=? WHERE id=?",
                 (category if category in storage.CATEGORIES else "Άλλο", grp.strip()[:120], file_id))
    return RedirectResponse(f"/c/{f['slug']}/files", status_code=303)


@router.post("/files/{file_id}/delete")
def file_delete(request: Request, file_id: int, csrf: str = Form(""), conn=Depends(get_conn)):
    auth.check_csrf(request, csrf)
    f = conn.execute("SELECT f.*, c.slug FROM file f JOIN course c ON c.id=f.course_id WHERE f.id=?", (file_id,)).fetchone()
    if not f:
        raise HTTPException(404, "Το αρχείο δεν βρέθηκε.")
    storage.delete_file(conn, f)
    _flash(request, f"Διαγράφηκε το {f['name']}.")
    return RedirectResponse(f"/c/{f['slug']}/files", status_code=303)


@router.get("/files/{file_id}")
def file_get(request: Request, file_id: int, conn=Depends(get_conn)):
    row = conn.execute("SELECT * FROM file WHERE id=?", (file_id,)).fetchone()
    return request.app.state.serve_file(row)


# ---------------------------------------------------------------- Ρυθμίσεις

@router.get("/settings")
def settings_page(request: Request, conn=Depends(get_conn)):
    s = get_settings(conn)
    log = conn.execute("""SELECT l.*, c.name AS course_name FROM notification_log l LEFT JOIN course c ON c.id=l.course_id
                          ORDER BY l.created_at DESC LIMIT 25""").fetchall()
    tzs = sorted(z for z in available_timezones() if "/" in z and not z.startswith(("Etc/", "SystemV/")))
    return _render(request, "settings.html", {
        "s": s, "weekdays": WEEKDAYS, "log": log, "timezones": tzs, "has_token": bool(effective_token(s)),
        "token_from_env": not s.get("ntfy_token") and bool(get_config().ntfy_token_default),
        "evening_slots": list(range(4)), "base_url": get_config().base_url})


@router.post("/settings")
async def settings_save(request: Request, conn=Depends(get_conn)):
    form = await request.form()
    auth.check_csrf(request, form.get("csrf"))
    values, errors = parse_form(dict(form), form.getlist)
    if errors:
        _flash(request, " · ".join(errors), "error")
        return RedirectResponse("/settings", status_code=303)
    save_settings(conn, values)
    _flash(request, "Οι ρυθμίσεις αποθηκεύτηκαν και ισχύουν αμέσως.")
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/test")
def settings_test(request: Request, csrf: str = Form(""), conn=Depends(get_conn)):
    auth.check_csrf(request, csrf)
    s = get_settings(conn)
    try:
        notify.publish(s, notify.test_message(conn, s))
        _flash(request, f"Στάλθηκε δοκιμαστική ειδοποίηση στο topic «{s['ntfy_topic']}».")
    except Exception as e:  # noqa: BLE001
        _flash(request, f"Αποτυχία αποστολής: {e}", "error")
    return RedirectResponse("/settings", status_code=303)
