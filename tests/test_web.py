"""Login, CSRF, tokens των κουμπιών ntfy, upload και ροή εισαγωγής από το UI."""
import re

from app import notify, services, tokens
from tests.conftest import FIXTURES, PASSWORD, csrf_from


def test_healthz(client):
    assert client.get("/healthz").json()["ok"] is True


def test_dashboard_requires_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert client.get("/settings", follow_redirects=False).status_code == 303
    assert client.get("/files/1", follow_redirects=False).status_code == 303


def test_login_wrong_password_and_rate_limit(client):
    token = csrf_from(client.get("/login").text)
    for _ in range(5):
        r = client.post("/login", data={"username": "admin", "password": "wrong", "csrf": token})
        assert r.status_code == 401
    r = client.post("/login", data={"username": "admin", "password": PASSWORD, "csrf": token})
    assert r.status_code == 429


def test_login_requires_csrf(client):
    client.get("/login")
    r = client.post("/login", data={"username": "admin", "password": PASSWORD, "csrf": "nope"})
    assert r.status_code == 403


def _import_course(c):
    page = c.get("/courses").text
    with open(FIXTURES / "AHK_Study_Tracker.xlsx", "rb") as x, open(FIXTURES / "AHK_Diavasma.ics", "rb") as i:
        r = c.post("/courses", data={"name": "ΑΗΚ", "csrf": csrf_from(page), "exam_date": "2027-01-20"},
                   files={"xlsx": ("t.xlsx", x), "ics": ("p.ics", i)}, follow_redirects=False)
    assert r.status_code == 303
    return r.headers["location"]


def test_import_upload_and_plan(logged_in):
    loc = _import_course(logged_in)
    assert loc == "/c/aik"
    page = logged_in.get(loc).text
    assert "Lecture 7 - Μέθοδος βρόχων" in page and "0 από 28" in page
    files_page = logged_in.get("/c/aik/files").text
    pdf = b"%PDF-1.4\n%test\n"
    r = logged_in.post("/c/aik/files", data={"csrf": csrf_from(files_page), "category": "Ημερήσιες σημειώσεις"},
                       files=[("files", ("L01_Stoixeia.pdf", pdf, "application/pdf")),
                              ("files", ("evil.exe", b"MZ", "application/octet-stream")),
                              ("files", ("fake.pdf", b"not a pdf", "application/pdf"))])
    assert r.status_code == 200
    assert "Ανέβηκαν 1 αρχεία" in r.text and "evil.exe" in r.text and "fake.pdf" in r.text
    assert "1 νέες συνδέσεις" in r.text
    fid = re.search(r'href="/files/(\d+)"', r.text).group(1)
    got = logged_in.get(f"/files/{fid}")
    assert got.status_code == 200 and got.content == pdf and got.headers["content-type"] == "application/pdf"
    csv = logged_in.get("/c/aik/export.csv")
    assert csv.status_code == 200 and "Lecture 1" in csv.content.decode("utf-8-sig")
    x = logged_in.get("/c/aik/export.xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK"


def test_status_change_needs_csrf(logged_in, conn):
    _import_course(logged_in)
    tid = conn.execute("SELECT id FROM task WHERE day_no=1").fetchone()["id"]
    assert logged_in.post(f"/tasks/{tid}/status", data={"status": "Ναι", "csrf": "bad"}).status_code == 403
    page = logged_in.get("/c/aik").text
    r = logged_in.post(f"/tasks/{tid}/status", data={"status": "Ναι", "csrf": csrf_from(page), "next": "/c/aik"},
                       follow_redirects=False)
    assert r.status_code == 303
    assert conn.execute("SELECT status FROM task WHERE id=?", (tid,)).fetchone()["status"] == "Ναι"


def test_ntfy_action_tokens(client, conn):
    cid = conn.execute("INSERT INTO course(name, slug) VALUES('X', 'x')").lastrowid
    tid = conn.execute("INSERT INTO task(course_id, date, topic) VALUES(?, '2026-11-26', 'T')", (cid,)).lastrowid
    conn.commit()
    task = conn.execute("SELECT * FROM task WHERE id=?", (tid,)).fetchone()
    done_url = notify.action_url(task, "done").replace("https://study.example.test", "")
    r = client.post(done_url)
    assert r.status_code == 200 and r.json()["result"] == "Ναι"
    assert conn.execute("SELECT status FROM task WHERE id=?", (tid,)).fetchone()["status"] == "Ναι"
    # αλλοιωμένο ή ληγμένο token
    assert client.post(done_url[:-3] + "AAA").status_code == 403
    expired = tokens.sign("act", tid, "done", task["date"], ttl_seconds=-10)
    assert client.post(f"/a/{expired}").status_code == 403
    # token αρχείου δεν δουλεύει ως token ενέργειας
    assert client.post(f"/a/{tokens.sign('file', tid, ttl_seconds=60)}").status_code == 403


def test_postpone_action_is_idempotent(client, conn):
    cid = conn.execute("INSERT INTO course(name, slug) VALUES('X', 'x')").lastrowid
    today = services.local_today(conn).isoformat()
    tid = conn.execute("INSERT INTO task(course_id, date, topic) VALUES(?, ?, 'T')", (cid, today)).lastrowid
    conn.commit()
    url = notify.action_url(conn.execute("SELECT * FROM task WHERE id=?", (tid,)).fetchone(), "postpone")
    url = url.replace("https://study.example.test", "")
    client.post(url)
    first = conn.execute("SELECT date, original_date FROM task WHERE id=?", (tid,)).fetchone()
    client.post(url)
    second = conn.execute("SELECT date FROM task WHERE id=?", (tid,)).fetchone()
    assert first["date"] > today and first["original_date"] == today and second["date"] == first["date"]


def test_settings_save_and_validation(logged_in, conn):
    page = logged_in.get("/settings").text
    form = {"csrf": csrf_from(page), "timezone": "Europe/Athens", "weekdays": ["0", "1", "2"],
            "morning_enabled": "on", "morning_time": "07:15", "evening_enabled": "on", "evening_count": "3",
            "evening_time_0": "19:00", "evening_time_1": "21:00", "evening_time_2": "23:00", "evening_time_3": "23:30",
            "weekly_day": "6", "weekly_time": "20:00", "quiet_start": "23:30", "quiet_end": "07:00",
            "grace_minutes": "45", "priority": "3", "evening_priority": "5", "ntfy_url": "http://ntfy",
            "ntfy_topic": "study-ahk", "ntfy_token": "tk_secret"}
    r = logged_in.post("/settings", data=form)
    assert "αποθηκεύτηκαν" in r.text
    from app.settings_store import get_settings

    s = get_settings(conn)
    assert s["morning_time"] == "07:15" and s["evening_times"] == ["19:00", "21:00", "23:00"]
    assert s["weekdays"] == [0, 1, 2] and s["weekly_enabled"] is False and s["ntfy_token"] == "tk_secret"
    assert "tk_secret" not in logged_in.get("/settings").text  # το token δεν εμφανίζεται ποτέ
    bad = dict(form, morning_time="25:00", ntfy_topic="κακό topic", csrf=csrf_from(r.text))
    r = logged_in.post("/settings", data=bad)
    assert "Μη έγκυρη ώρα" in r.text and "topic" in r.text
    assert get_settings(conn)["morning_time"] == "07:15"
