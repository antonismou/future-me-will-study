"""Ασφάλεια login πίσω από proxy, χρόνος μελέτης και λίστα «Δυσκολίες»."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app import services, studytime
from tests.conftest import PASSWORD, csrf_from

ATH = ZoneInfo("Europe/Athens")


# ---------------------------------------------------------------- login πίσω από reverse proxy

def _bad_login(client, token, xff):
    return client.post("/login", data={"username": "admin", "password": "wrong", "csrf": token},
                       headers={"X-Forwarded-For": xff})


def test_spoofed_forwarded_for_cannot_bypass_rate_limit(client):
    token = csrf_from(client.get("/login").text)
    # Ο επιτιθέμενος αλλάζει το αριστερό κομμάτι· ο NPM προσθέτει πάντα την πραγματική IP στο τέλος.
    for i in range(5):
        assert _bad_login(client, token, f"10.9.9.{i}, 203.0.113.7").status_code == 401
    r = client.post("/login", data={"username": "admin", "password": PASSWORD, "csrf": token},
                    headers={"X-Forwarded-For": "1.1.1.1, 203.0.113.7"})
    assert r.status_code == 429


def test_other_ip_not_affected_by_one_attacker(client):
    token = csrf_from(client.get("/login").text)
    for _ in range(5):
        _bad_login(client, token, "203.0.113.7")
    r = client.post("/login", data={"username": "admin", "password": PASSWORD, "csrf": token, "next": "/"},
                    headers={"X-Forwarded-For": "198.51.100.2"}, follow_redirects=False)
    assert r.status_code == 303


def test_global_lock_after_many_failures_from_many_ips(client):
    token = csrf_from(client.get("/login").text)
    for i in range(20):
        _bad_login(client, token, f"198.51.100.{i}")
    r = client.post("/login", data={"username": "admin", "password": PASSWORD, "csrf": token},
                    headers={"X-Forwarded-For": "192.0.2.50"})
    assert r.status_code == 429


@pytest.mark.parametrize("hops,xff,expected", [
    ("1", "6.6.6.6, 203.0.113.7", "203.0.113.7"),
    ("2", "6.6.6.6, 198.51.100.1, 203.0.113.7", "198.51.100.1"),
    ("0", "6.6.6.6", "testclient"),
    ("1", "", "testclient"),
])
def test_client_ip_hops(monkeypatch, hops, xff, expected):
    from starlette.requests import Request

    from app import auth
    from app.config import reset_config

    monkeypatch.setenv("TRUSTED_PROXY_HOPS", hops)
    reset_config()
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    req = Request({"type": "http", "headers": headers, "client": ("testclient", 50000)})
    assert auth.client_ip(req) == expected


# ---------------------------------------------------------------- χρόνος μελέτης

def _course_task(conn, d="2026-11-26", status="Όχι"):
    cid = conn.execute("INSERT INTO course(name, slug) VALUES('ΑΗΚ', 'aik')").lastrowid
    wid = conn.execute("INSERT INTO week(course_id, position, title) VALUES(?, 0, 'Εβδ.1')", (cid,)).lastrowid
    tid = conn.execute("INSERT INTO task(course_id, week_id, day_no, date, topic, status) VALUES(?,?,1,?,'L1',?)",
                       (cid, wid, d, status)).lastrowid
    tid2 = conn.execute("INSERT INTO task(course_id, week_id, day_no, date, topic) VALUES(?,?,2,'2026-11-27','L2')",
                        (cid, wid)).lastrowid
    conn.commit()
    return cid, tid, tid2


def test_timer_start_stop_and_status(conn):
    cid, tid, _ = _course_task(conn)
    t0 = datetime(2026, 11, 26, 18, 0, tzinfo=timezone.utc)
    studytime.start(conn, tid, t0)
    assert studytime.running(conn)["task_id"] == tid
    assert conn.execute("SELECT status FROM task WHERE id=?", (tid,)).fetchone()[0] == "Σε εξέλιξη"
    assert studytime.task_seconds(conn, [tid], t0 + timedelta(minutes=10))[tid] == 600  # μετράει όσο τρέχει
    assert studytime.stop(conn, t0 + timedelta(minutes=50)) == 3000
    assert studytime.running(conn) is None
    assert studytime.task_seconds(conn, [tid])[tid] == 3000


def test_starting_another_timer_stops_previous(conn):
    cid, tid, tid2 = _course_task(conn)
    t0 = datetime(2026, 11, 26, 18, 0, tzinfo=timezone.utc)
    studytime.start(conn, tid, t0)
    studytime.start(conn, tid2, t0 + timedelta(minutes=20))
    assert studytime.running(conn)["task_id"] == tid2
    assert studytime.task_seconds(conn, [tid])[tid] == 1200
    assert conn.execute("SELECT COUNT(*) FROM study_session WHERE ended_at IS NULL").fetchone()[0] == 1


def test_forgotten_timer_is_capped(conn):
    cid, tid, _ = _course_task(conn)
    t0 = datetime(2026, 11, 26, 18, 0, tzinfo=timezone.utc)
    studytime.start(conn, tid, t0)
    assert studytime.stop(conn, t0 + timedelta(days=2)) == 6 * 3600


def test_manual_time_and_validation(conn):
    cid, tid, _ = _course_task(conn)
    studytime.add_manual(conn, tid, 45)
    assert studytime.task_seconds(conn, [tid])[tid] == 45 * 60
    for bad in (0, -5, 721):
        with pytest.raises(ValueError):
            studytime.add_manual(conn, tid, bad)


def test_stats_group_by_local_day(conn):
    cid, tid, tid2 = _course_task(conn)
    now = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
    # 23:30 ώρα Ελλάδας στις 26/11 = 21:30 UTC -> μετράει στις 26/11, όχι στις 27/11
    studytime.start(conn, tid, datetime(2026, 11, 26, 21, 30, tzinfo=timezone.utc))
    studytime.stop(conn, datetime(2026, 11, 26, 22, 0, tzinfo=timezone.utc))
    studytime.add_manual(conn, tid2, 20, now=now)
    st = studytime.course_stats(conn, cid, ATH, days=14, now=now)
    days = dict(st.days)
    assert days[date(2026, 11, 26)] == 1800 and days[date(2026, 11, 27)] == 1200
    assert st.today == 1200 and st.week == 3000 and st.total == 3000
    assert st.by_week == [("Εβδ.1", 3000)]
    assert [t["id"] for t, _ in st.by_task] == [tid, tid2]


def test_fmt_duration():
    assert studytime.fmt_duration(0) == "0 λ"
    assert studytime.fmt_duration(45 * 60) == "45 λ"
    assert studytime.fmt_duration(3600) == "1 ώ"
    assert studytime.fmt_duration(3600 + 5 * 60) == "1 ώ 05 λ"


def test_timer_routes_and_pages(logged_in, conn):
    cid, tid, _ = _course_task(conn, d=services.local_today(conn).isoformat())
    page = logged_in.get("/c/aik").text
    token = csrf_from(page)
    assert "Χρονόμετρο" in page
    logged_in.post(f"/tasks/{tid}/timer/start", data={"csrf": token, "next": "/c/aik"})
    page = logged_in.get("/").text
    assert "runbar" in page and "Διακοπή" in page
    r = logged_in.post("/timer/stop", data={"csrf": token, "next": "/"})
    assert r.status_code == 200 and "runbar" not in r.text
    r = logged_in.post(f"/tasks/{tid}/time", data={"csrf": token, "minutes": "30", "next": "/c/aik"})
    assert "Προστέθηκαν 30 λ" in r.text
    r = logged_in.post(f"/tasks/{tid}/time", data={"csrf": token, "minutes": "abc", "next": "/c/aik"})
    assert "Δώσε λεπτά" in r.text
    t = logged_in.get("/c/aik/time")
    assert t.status_code == 200 and "Τελευταίες 14 ημέρες" in t.text and "30 λ" in t.text
    csv = logged_in.get("/c/aik/export.csv").content.decode("utf-8-sig")
    assert "Λεπτά μελέτης" in csv.splitlines()[0] and ",30" in csv
    assert logged_in.post(f"/tasks/{tid}/timer/start", data={"csrf": "bad"}).status_code == 403


# ---------------------------------------------------------------- Δυσκολίες

def test_review_list_flow(logged_in, conn):
    cid, tid, tid2 = _course_task(conn)
    page = logged_in.get("/c/aik/review").text
    assert "Δεν υπάρχουν δυσκολίες" in page
    token = csrf_from(page)
    logged_in.post(f"/tasks/{tid}/notes", data={"csrf": token, "notes": "πρόσημα στους βρόχους", "next": "/c/aik"})
    logged_in.post(f"/tasks/{tid2}/notes", data={"csrf": token, "notes": "", "hard": "on", "next": "/c/aik"})
    page = logged_in.get("/c/aik/review").text
    assert "πρόσημα στους βρόχους" in page and "L2" in page and "2 προς επανάληψη" in page
    logged_in.post(f"/tasks/{tid}/reviewed", data={"csrf": token, "reviewed": "1", "next": "/c/aik/review"})
    page = logged_in.get("/c/aik/review").text
    assert "1 προς επανάληψη" in page and "Επανεξετάστηκαν" in page
    logged_in.post(f"/tasks/{tid}/reviewed", data={"csrf": token, "reviewed": "0", "next": "/c/aik/review"})
    assert "2 προς επανάληψη" in logged_in.get("/c/aik/review").text
    # αφαίρεση «δύσκολο» χωρίς σημειώσεις -> φεύγει από τη λίστα
    logged_in.post(f"/tasks/{tid2}/notes", data={"csrf": token, "notes": "", "next": "/c/aik"})
    assert [r["id"] for r in services.review_items(conn, cid)] == [tid]


def test_migration_adds_columns_to_old_db(tmp_path, monkeypatch):
    import sqlite3

    from app.config import reset_config
    from app.db import init_db

    data = tmp_path / "old"
    data.mkdir()
    old = sqlite3.connect(data / "study.db")
    old.executescript("""CREATE TABLE course(id INTEGER PRIMARY KEY, name TEXT, slug TEXT UNIQUE);
        CREATE TABLE task(id INTEGER PRIMARY KEY, course_id INTEGER, date TEXT NOT NULL, notes TEXT DEFAULT '');""")
    old.commit()
    old.close()
    monkeypatch.setenv("DATA_DIR", str(data))
    reset_config()
    init_db()
    cols = {r[1] for r in sqlite3.connect(data / "study.db").execute("PRAGMA table_info(task)")}
    assert {"hard", "reviewed_at"} <= cols
