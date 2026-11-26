"""Δημιουργός πλάνου από θέματα και ισορροπία μαθημάτων."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app import balance, builder, services, settings_store
from tests.conftest import csrf_from

ATH = ZoneInfo("Europe/Athens")
MON = date(2026, 11, 30)
NOW = datetime(2026, 11, 30, 10, 0, tzinfo=timezone.utc)
ALL = set(range(7))
H = 3600


def _topics(*sizes):
    return [builder.Topic(f"T{i}", s) for i, s in enumerate(sizes, 1)]


def _build(topics, days=4, **kw):
    args = {"weekdays": ALL, "days_off": set(), "revision_days": 0, **kw}
    return builder.build(topics, MON, MON + timedelta(days=days), **args)


# ---------------------------------------------------------------- δημιουργός

def test_parse_topics_sizes():
    got = builder.parse_topics("Α | μικρό\n\nΒ|ΜΕΓΑΛΟ\nΓ\nΔ | s\nΕ |\nΖ | η | Θ | 3")
    assert [(t.title, t.size) for t in got] == [("Α", 1), ("Β", 3), ("Γ", 2), ("Δ", 1), ("Ε", 2), ("Ζ | η | Θ", 3)]


@pytest.mark.parametrize("text,msg", [("", "τουλάχιστον"), ("Α | τεράστιο", "Γραμμή 1"), ("| μικρό", "τίτλος"),
                                      ("x\n" * 201, "Έως 200")])
def test_parse_topics_errors(text, msg):
    with pytest.raises(builder.PlanError, match=msg):
        builder.parse_topics(text)


def test_parse_dates():
    assert builder.parse_dates("25/12/2026, 2027-01-01\n") == {date(2026, 12, 25), date(2027, 1, 1)}
    for bad in ("25/12", "31/02/2026", "αύριο", "1/1/26"):
        with pytest.raises(builder.PlanError):
            builder.parse_dates(bad)


def test_example_split_over_four_days():
    plan = _build(_topics(1, 2, 3, 2))
    days = {}
    for i in plan.items:
        days.setdefault(i.day_no, []).append((i.topic, i.effort))
    assert days == {1: [("T1", 1), ("T2 (μέρος 1/2)", 1)],
                    2: [("T2 (μέρος 2/2)", 1), ("T3 (μέρος 1/2)", 1)],
                    3: [("T3 (μέρος 2/2)", 2)],
                    4: [("T4", 2)]}


@pytest.mark.parametrize("sizes,days", [((1,), 1), ((3, 3, 3), 2), ((1, 2, 3), 20), ((2,) * 30, 7), ((3, 1), 5)])
def test_every_day_used_order_kept_effort_preserved(sizes, days):
    plan = _build(_topics(*sizes), days=days)
    assert {i.day_no for i in plan.items} == set(range(1, days + 1))
    order = [int(i.topic.split()[0][1:]) for i in plan.items]
    assert order == sorted(order) and set(order) == set(range(1, len(sizes) + 1))
    for n, size in enumerate(sizes, 1):
        assert sum(i.effort for i, o in zip(plan.items, order) if o == n) == pytest.approx(
            size, abs=0 if sum(sizes) >= days else sum(sizes) / days)
    assert sum(i.effort for i in plan.items) == pytest.approx(sum(sizes))


def test_weekdays_days_off_and_revision():
    plan = builder.build(_topics(2, 2), MON, MON + timedelta(days=14), {0, 2}, {MON + timedelta(days=2)}, 1)
    assert [(i.date, i.revision) for i in plan.items] == [
        (MON, False), (MON + timedelta(days=7), False), (MON + timedelta(days=9), True)]
    assert [title.split(" (")[0] for title, _ in plan.weeks()] == ["Εβδομάδα 1", "Εβδομάδα 2"]


@pytest.mark.parametrize("kw", [{"days": 0}, {"days": 800}, {"weekdays": set()}, {"revision_days": 4},
                                {"revision_days": -1}])
def test_build_errors(kw):
    with pytest.raises(builder.PlanError):
        _build(_topics(2), **kw)


def test_builder_preview_then_create(logged_in, conn):
    page = logged_in.get("/courses/builder")
    today = services.local_today(conn)
    data = {"csrf": csrf_from(page.text), "name": "ΛΣ", "start": today.isoformat(),
            "exam_date": (today + timedelta(days=10)).isoformat(), "topics": "Μνήμη | μεγάλο\nΑρχεία",
            "weekdays": [str(d) for d in range(7)], "revision_days": "2", "color": "#123456", "days_off": ""}
    r = logged_in.post("/courses/builder", data={**data, "action": "preview"})
    assert r.status_code == 200 and "Μνήμη (μέρος 1/" in r.text and 'value="create"' in r.text
    assert conn.execute("SELECT COUNT(*) FROM course").fetchone()[0] == 0

    bad = logged_in.post("/courses/builder", data={**data, "topics": "Α | ???", "action": "create"})
    assert bad.status_code == 400 and "άγνωστο μέγεθος" in bad.text and "Α | ???" in bad.text
    past = logged_in.post("/courses/builder", data={**data, "start": "2000-01-01", "action": "create"})
    assert past.status_code == 400

    r = logged_in.post("/courses/builder", data={**data, "action": "create"}, follow_redirects=False)
    assert r.status_code == 303
    course = conn.execute("SELECT * FROM course").fetchone()
    assert r.headers["location"] == f"/c/{course['slug']}"
    tasks = services.tasks(conn, course["id"])
    assert sum(t["effort"] for t in tasks if t["topic"] != "Επανάληψη") == pytest.approx(5)
    assert sum(t["topic"] == "Επανάληψη" for t in tasks) == 2 and all(t["week_id"] for t in tasks)
    assert "Μνήμη" in logged_in.get(r.headers["location"]).text


# ---------------------------------------------------------------- ισορροπία

def _course(conn, name, exam_in, efforts, minutes=60):
    exam = (MON + timedelta(days=exam_in)).isoformat() if exam_in is not None else None
    cid = conn.execute("INSERT INTO course(name, slug, exam_date, medium_minutes) VALUES(?,?,?,?)",
                       (name, name, exam, minutes)).lastrowid
    ids = [conn.execute("INSERT INTO task(course_id, date, topic, effort) VALUES(?,?,?,?)",
                        (cid, MON.isoformat(), "t", e)).lastrowid for e in efforts]
    conn.commit()
    return cid, ids


def _advise(conn, hours=3):
    return balance.advise(conn, [hours * 60] * 7, ATH, NOW)


def test_pace_follows_remaining_work_and_exam(conn):
    _course(conn, "far", 10, [None] * 10)       # 10 ώρες σε 10 ημέρες
    _course(conn, "near", 4, [2, 2, 3, 1])      # 4 ώρες σε 4 ημέρες
    a = _advise(conn)
    assert [(p.course["name"], p.remaining, p.suggested) for p in a.courses] == [
        ("near", 4 * H, H), ("far", 10 * H, H)]
    assert a.suggested == 2 * H and a.shortfall is None


def test_overload_serves_nearest_exam_and_reports_shortfall(conn):
    _course(conn, "near", 2, [2] * 5)           # 5 ώρες σε 2 ημέρες των 2 ωρών
    _course(conn, "far", 10, [2] * 5)
    a = _advise(conn, hours=2)
    assert [p.suggested for p in a.courses] == [2 * H, 0]
    assert (a.shortfall.course_name, a.shortfall.missing) == ("near", H)


def test_studied_time_and_finished_tasks(conn):
    cid, (t1, t2, t3) = _course(conn, "a", 4, [2, 2, 2])
    add = ("INSERT INTO study_session(course_id, task_id, started_at, ended_at, seconds) VALUES(?,?,?,?,?)")
    conn.execute(add, (cid, t1, "2026-11-28T10:00:00+00:00", "x", 30 * 60))   # χθες: αφαιρείται
    conn.execute(add, (cid, t1, "2026-11-30T08:00:00+00:00", "x", 20 * 60))   # σήμερα: ο στόχος μένει σταθερός
    conn.execute("UPDATE task SET status='Ναι', completed_at='2026-11-20T10:00:00' WHERE id=?", (t2,))
    conn.execute("UPDATE task SET status='Ναι', completed_at='2026-11-30T09:00:00' WHERE id=?", (t3,))
    conn.commit()
    p = _advise(conn).courses[0]
    assert p.remaining == 30 * 60 + H and p.studied == 20 * 60


def test_many_small_parts_are_not_inflated(conn):
    _course(conn, "parts", 10, [0.25] * 8)      # 8 μέρη, μαζί όσο μία μεσαία εργασία
    assert _advise(conn).courses[0].remaining == H


def test_skips_finished_and_past_courses_and_handles_no_exam(conn):
    _course(conn, "past", -1, [2])
    _course(conn, "empty", 5, [])
    _course(conn, "noexam", None, [2], minutes=90)
    a = _advise(conn)
    assert [(p.course["name"], p.deadline, p.suggested) for p in a.courses] == [
        ("noexam", MON + timedelta(days=1), 90 * 60)]


def test_exam_today_and_day_off(conn):
    _course(conn, "today", 0, [2])
    assert _advise(conn).shortfall.missing == H
    conn.execute("UPDATE course SET exam_date='2026-12-05'")
    a = balance.advise(conn, [0, 60, 60, 60, 60, 60, 60], ATH, NOW)
    assert a.today_capacity == 0 and a.suggested == 0 and a.shortfall is None


def test_balance_pages_and_task_size(logged_in, conn):
    today = services.local_today(conn)
    cid, (tid, part) = _course(conn, "demo", (today - MON).days + 10, [None, 0.5])
    assert "Μοίρασμα χρόνου" not in logged_in.get("/").text
    token = csrf_from(logged_in.get("/balance").text)
    hours = {f"hours_{i}": "1,5" for i in range(7)}
    logged_in.post("/balance", data={**hours, "csrf": token})
    assert settings_store.get_settings(conn)["study_minutes"] == [90] * 7
    assert "Μοίρασμα χρόνου" in logged_in.get("/").text
    assert "πρέπει" not in logged_in.get("/balance").text
    assert "γράψε ώρες" in logged_in.post("/balance", data={**hours, "hours_2": "30", "csrf": token}).text
    assert settings_store.get_settings(conn)["study_minutes"] == [90] * 7

    # οι γενικές ρυθμίσεις δεν πειράζουν τις διαθέσιμες ώρες
    assert "study_minutes" not in settings_store.parse_form({}, lambda k: [])[0]

    base = {"csrf": token, "topic": "t", "date": today.isoformat(), "status": "Όχι"}
    effort = lambda i: conn.execute("SELECT effort FROM task WHERE id=?", (i,)).fetchone()[0]
    logged_in.post(f"/tasks/{tid}/edit", data={**base, "size": "3"})
    assert effort(tid) == 3
    assert "Μέρος θέματος" in logged_in.get(f"/tasks/{part}/edit").text
    logged_in.post(f"/tasks/{part}/edit", data={**base, "size": ""})
    assert effort(part) == 0.5

    logged_in.post("/c/demo/settings", data={"name": "demo", "active": "on", "medium_minutes": "45", "csrf": token})
    assert conn.execute("SELECT medium_minutes FROM course").fetchone()[0] == 45
    logged_in.post("/c/demo/settings", data={"name": "demo", "active": "on", "medium_minutes": "2", "csrf": token})
    assert conn.execute("SELECT medium_minutes FROM course").fetchone()[0] == 45
