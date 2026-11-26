"""Ροή ημερολογίου (ics) και εβδομαδιαίος στόχος μελέτης."""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app import calfeed, notify, settings_store, studytime
from tests.conftest import csrf_from

ATH = ZoneInfo("Europe/Athens")


def _course(conn, goal=0, active=1, slug="aik", exam="2026-12-20"):
    cid = conn.execute("INSERT INTO course(name, slug, exam_date, weekly_goal_minutes, active) VALUES(?,?,?,?,?)",
                       (f"Μάθημα {slug}", slug, exam, goal, active)).lastrowid
    tid = conn.execute(
        """INSERT INTO task(course_id, date, topic, description, notes, status)
           VALUES(?, '2026-11-26', 'Α, Β; Γ', ?, 'μυστική σημείωση', 'Ναι')""", (cid, "γραμμή\n" + "ω" * 120)).lastrowid
    conn.commit()
    return cid, tid


def _session(conn, cid, started_utc, minutes):
    conn.execute("INSERT INTO study_session(course_id, started_at, ended_at, seconds) VALUES(?,?,?,?)",
                 (cid, started_utc, started_utc, minutes * 60))
    conn.commit()


def _row(conn, cid):
    return conn.execute("SELECT * FROM course WHERE id=?", (cid,)).fetchone()


# ---------------------------------------------------------------- ημερολόγιο

def test_feed_content_is_valid_ics(conn):
    _, tid = _course(conn)
    _course(conn, active=0, slug="off")
    text = calfeed.build(conn, datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc))
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    unfolded = text.replace("\r\n ", "")
    assert f"UID:task-{tid}@study.example.test" in unfolded
    assert "DTSTART;VALUE=DATE:20261126\r\nDTEND;VALUE=DATE:20261127" in unfolded
    assert "SUMMARY:✅ Μάθημα aik: Α\\, Β\\; Γ" in unfolded
    assert "γραμμή\\n" + "ω" * 120 in unfolded
    assert "DTSTART;VALUE=DATE:20261220" in unfolded
    assert "μυστική" not in unfolded and "Μάθημα off" not in unfolded
    assert unfolded.count("BEGIN:VEVENT") == 2


def test_feed_link_lifecycle(logged_in, conn):
    _course(conn)
    page = logged_in.get("/settings").text
    assert "/cal/" not in page
    token = csrf_from(page)

    def post(action):
        return logged_in.post("/settings/calendar", data={"action": action, "csrf": token}, follow_redirects=False)

    assert post("new").status_code == 303
    first = calfeed.current_token(conn)
    assert f"https://study.example.test/cal/{first}.ics" in logged_in.get("/settings").text
    logged_in.cookies.clear()   # οι εφαρμογές ημερολογίου δεν έχουν session
    r = logged_in.get(f"/cal/{first}.ics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    assert logged_in.head(f"/cal/{first}.ics").status_code == 200
    assert logged_in.get("/cal/wrong.ics").status_code == 404
    assert logged_in.get("/cal/%CE%B1.ics").status_code == 404

    second = calfeed.new_token(conn)
    conn.commit()
    assert logged_in.get(f"/cal/{first}.ics").status_code == 404
    assert logged_in.get(f"/cal/{second}.ics").status_code == 200
    calfeed.disable(conn)
    conn.commit()
    assert logged_in.get(f"/cal/{second}.ics").status_code == 404


def test_calendar_actions_need_login_and_csrf(client, conn):
    r = client.post("/settings/calendar", data={"action": "new"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert calfeed.current_token(conn) is None


def test_calendar_rejects_bad_csrf(logged_in, conn):
    assert logged_in.post("/settings/calendar", data={"action": "new", "csrf": "x"}).status_code == 403
    assert calfeed.current_token(conn) is None


# ---------------------------------------------------------------- εβδομαδιαίος στόχος

@pytest.mark.parametrize("text,minutes", [
    ("", 0), ("0", 0), ("8", 480), ("7,5", 450), (" 1.25 ", 75), ("100", 6000),
    ("-1", None), ("101", None), ("abc", None), ("nan", None), ("inf", None),
])
def test_parse_goal_hours(text, minutes):
    assert studytime.parse_goal_hours(text) == minutes


def test_goal_counts_local_monday_to_sunday(conn):
    cid, _ = _course(conn, goal=300)
    _session(conn, cid, "2026-11-22T21:59:00+00:00", 10)   # Κυριακή 23:59 Αθήνας: προηγούμενη εβδομάδα
    _session(conn, cid, "2026-11-22T22:30:00+00:00", 60)   # Δευτέρα 00:30 Αθήνας
    _session(conn, cid, "2026-11-26T10:00:00+00:00", 30)
    _session(conn, cid, "2026-11-30T10:00:00+00:00", 45)   # επόμενη εβδομάδα
    g = studytime.goal_progress(conn, _row(conn, cid), ATH, date(2026, 11, 26))
    assert (g.done, g.target, g.days_left, g.pct) == (90 * 60, 300 * 60, 4, 30)
    assert g.remaining == 210 * 60 and g.per_day == 53 * 60


def test_goal_reached_and_absent(conn):
    cid, _ = _course(conn, goal=30)
    _session(conn, cid, "2026-11-26T10:00:00+00:00", 90)
    g = studytime.goal_progress(conn, _row(conn, cid), ATH, date(2026, 11, 29))
    assert (g.pct, g.remaining, g.per_day, g.days_left) == (100, 0, 0, 1)
    other, _ = _course(conn, slug="no-goal")
    assert studytime.goal_progress(conn, _row(conn, other), ATH, date(2026, 11, 26)) is None


@pytest.mark.parametrize("day,expected", [
    (date(2026, 11, 29), "Στόχος εβδομάδας: 1 ώ από 2 ώ (50%)"),
    (date(2026, 11, 30), "Στόχος προηγούμενης εβδομάδας: 1 ώ από 2 ώ (50%)"),
])
def test_weekly_summary_mentions_goal(conn, day, expected):
    cid, _ = _course(conn, goal=120)
    _session(conn, cid, "2026-11-25T10:00:00+00:00", 60)
    msg = notify.weekly_message(conn, _row(conn, cid), settings_store.get_settings(conn), day)
    assert expected in msg["message"]


def test_goal_routes_and_pages(logged_in, conn):
    cid, _ = _course(conn)
    token = csrf_from(logged_in.get("/c/aik/settings").text)

    def save(goal):
        return logged_in.post("/c/aik/settings", data={"name": "Μάθημα", "active": "on", "weekly_goal": goal,
                                                        "csrf": token})

    assert "Στόχος εβδομάδας" not in logged_in.get("/").text
    save("7,5")
    assert _row(conn, cid)["weekly_goal_minutes"] == 450
    assert 'value="7.5"' in logged_in.get("/c/aik/settings").text
    assert "Στόχος εβδομάδας" in logged_in.get("/").text
    assert "Στόχος εβδομάδας" in logged_in.get("/c/aik/time").text
    assert "πρέπει να είναι ώρες" in save("πολλές").text
    assert _row(conn, cid)["weekly_goal_minutes"] == 450
    save("")
    assert _row(conn, cid)["weekly_goal_minutes"] == 0
