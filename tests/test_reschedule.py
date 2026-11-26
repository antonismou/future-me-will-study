"""Πρόταση αναπρογραμματισμού: κανόνες, αποδοχή και αναίρεση."""
import random
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import reschedule, services, settings_store
from tests.conftest import csrf_from

ATH = ZoneInfo("Europe/Athens")
MON = date(2026, 11, 30)
NOW = datetime(2026, 11, 30, 10, 0, tzinfo=timezone.utc)


def _course(conn, name, exam_in, offsets, efforts=None, start=MON):
    """Μάθημα με εργασίες στις ημέρες start+offset (μεσαία = 60 λεπτά)."""
    exam = (start + timedelta(days=exam_in)).isoformat() if exam_in is not None else None
    cid = conn.execute("INSERT INTO course(name, slug, exam_date) VALUES(?,?,?)", (name, name, exam)).lastrowid
    ids = [conn.execute("INSERT INTO task(course_id, day_no, date, topic, effort) VALUES(?,?,?,?,?)",
                        (cid, i, (start + timedelta(days=o)).isoformat(), f"{name}{i}",
                         efforts[i] if efforts else 2)).lastrowid for i, o in enumerate(offsets)]
    conn.commit()
    return ids


def _propose(conn, minutes=60):
    week = minutes if isinstance(minutes, list) else [minutes] * 7
    return reschedule.propose(conn, week, ATH, NOW)


def _moves(p):
    return {m.task["topic"]: (m.new - MON).days for m in p.moves}


def test_plan_that_fits_is_left_alone(conn):
    _course(conn, "a", 10, [0, 1, 5])
    p = _propose(conn)
    assert p.moves == [] and p.unplaced == []


def test_nothing_without_hours(conn):
    _course(conn, "a", 10, [-3])
    assert _propose(conn, 0).moves == []


def test_overdue_moves_forward_and_pushes_only_what_it_must(conn):
    _course(conn, "a", 10, [-2, 0, 1, 5])
    assert _moves(_propose(conn)) == {"a0": 0, "a1": 1, "a2": 2}


def test_overfull_day_spills_over(conn):
    _course(conn, "a", 10, [1, 1, 1])
    assert _moves(_propose(conn)) == {"a1": 2, "a2": 3}


def test_pulls_earlier_when_the_end_would_miss_the_exam(conn):
    _course(conn, "a", 4, [3, 3, 3])       # τρεις εργασίες την παραμονή
    assert _moves(_propose(conn)) == {"a0": 1, "a1": 2}


def test_nearest_exam_gets_the_hours_first(conn):
    _course(conn, "far", 20, [0])
    _course(conn, "near", 3, [0])
    assert _moves(_propose(conn)) == {"far0": 1}


def test_days_without_hours_are_skipped(conn):
    _course(conn, "a", 10, [0, 1])
    assert _moves(_propose(conn, [0, 60, 60, 60, 60, 60, 60])) == {"a0": 1, "a1": 2}


def test_unplaced_when_there_is_not_enough_time(conn):
    _course(conn, "a", 2, [0, 0, 0, 0])
    p = _propose(conn)
    assert _moves(p) == {"a1": 1}
    assert [t["topic"] for _, t in p.unplaced] == ["a2", "a3"]


def test_exam_today_or_past(conn):
    _course(conn, "today", 0, [-1])
    _course(conn, "past", -1, [-2])
    p = _propose(conn)
    assert p.moves == [] and [t["topic"] for _, t in p.unplaced] == ["today0"]


def test_oversized_task_gets_a_big_day_of_its_own(conn):
    _course(conn, "a", 10, [0, 0], efforts=[3, 1])          # 90 και 30 λεπτά
    assert _moves(_propose(conn, [30, 60, 60, 60, 60, 60, 60])) == {"a0": 1, "a1": 2}


def test_time_studied_today_reduces_today(conn):
    ids = _course(conn, "a", 10, [0, -1])
    cid = conn.execute("SELECT course_id FROM task WHERE id=?", (ids[0],)).fetchone()[0]
    conn.execute("INSERT INTO study_session(course_id, started_at, ended_at, seconds) VALUES(?,?,?,3600)",
                 (cid, "2026-11-30T08:00:00+00:00", "x"))
    assert _moves(_propose(conn)) == {"a1": 1, "a0": 2}


def test_done_tasks_and_inactive_courses_are_ignored(conn):
    ids = _course(conn, "a", 10, [-1])
    conn.execute("UPDATE task SET status='Ναι' WHERE id=?", (ids[0],))
    _course(conn, "off", 10, [-1])
    conn.execute("UPDATE course SET active=0 WHERE name='off'")
    assert _propose(conn).moves == []


def test_random_plans_respect_all_rules(conn):
    rng = random.Random(7)
    for round_no in range(150):
        conn.execute("DELETE FROM course")
        week = [rng.choice([0, 45, 60, 120, 180]) for _ in range(7)]
        if not any(week):
            continue
        for c in range(rng.randint(1, 3)):
            offsets = sorted(rng.randint(-5, 15) for _ in range(rng.randint(1, 12)))
            _course(conn, f"c{c}-", rng.choice([None, 1, 5, 12, 30]), offsets,
                    efforts=[rng.choice([0.5, 1, 2, 3]) for _ in offsets])
        p = _propose(conn, week)
        new = {m.task["id"]: m.new for m in p.moves}
        left_out = {t["id"] for _, t in p.unplaced}
        load = {}
        for course in services.courses(conn):
            previous = MON
            for t in services.tasks(conn, course["id"]):
                if t["id"] in left_out:
                    continue
                d = new.get(t["id"], date.fromisoformat(t["date"]))
                assert d >= previous, (round_no, "σειρά")
                assert not course["exam_date"] or d < date.fromisoformat(course["exam_date"])
                previous = d
                load.setdefault(d, []).append(int(60 * t["effort"] / 2))
        for d, sizes in load.items():
            cap = week[d.weekday()]
            assert cap > 0
            assert sum(sizes) <= cap or (len(sizes) == 1 and sizes[0] > max(week) == cap), (round_no, d, sizes, week)
        # η ίδια πρόταση, αφού εφαρμοστεί, δεν ζητά νέες αλλαγές όταν χωράνε όλα
        if not p.unplaced:
            reschedule.apply(conn, p.moves)
            assert _propose(conn, week).moves == [], round_no


def test_accept_selected_then_undo(logged_in, conn):
    today = services.local_today(conn)
    ids = _course(conn, "a", 20, [-2, -1], start=today)
    token = csrf_from(logged_in.get("/balance").text)
    assert "Προτείνεται αλλαγή" not in logged_in.get("/").text
    settings_store.save_settings(conn, {"study_minutes": [600] * 7})
    conn.commit()
    assert "Προτείνεται αλλαγή σε 2" in logged_in.get("/").text

    row = lambda i: tuple(conn.execute("SELECT date, original_date FROM task WHERE id=?", (i,)).fetchone())
    before = [row(i) for i in ids]
    page = logged_in.get("/balance/reschedule").text
    key = f"{ids[0]}:{before[0][0]}:{today.isoformat()}"
    assert key in page and [row(i) for i in ids] == before       # η προβολή δεν αλλάζει τίποτα

    def accept(*keys):
        return logged_in.post("/balance/reschedule", data={"csrf": token, "move": list(keys)})

    assert "Δεν διάλεξες" in accept().text
    assert "Η πρόταση άλλαξε" in accept(key, f"{ids[1]}:{before[1][0]}:2099-01-01").text
    assert [row(i) for i in ids] == before

    assert "Άλλαξε 1 ημερομηνία" in accept(key).text
    assert row(ids[0]) == (today.isoformat(), before[0][0]) and row(ids[1]) == before[1]
    assert "Η πρόταση άλλαξε" in accept(key).text               # διπλή υποβολή

    r = logged_in.post("/balance/reschedule/undo", data={"csrf": token})
    assert "επανήλθε 1" in r.text and [row(i) for i in ids] == before
    assert reschedule.undo_count(conn) == 0


def test_undo_keeps_later_manual_changes(conn):
    ids = _course(conn, "a", 10, [-1, -1])
    reschedule.apply(conn, _propose(conn, 600).moves)
    services.postpone(conn, ids[0], MON + timedelta(days=5))
    assert reschedule.undo(conn) == 1
    dates = [conn.execute("SELECT date FROM task WHERE id=?", (i,)).fetchone()[0] for i in ids]
    assert dates == [(MON + timedelta(days=5)).isoformat(), (MON - timedelta(days=1)).isoformat()]


def test_accept_needs_csrf(logged_in):
    assert logged_in.post("/balance/reschedule", data={"csrf": "x", "move": "1:a:b"}).status_code == 403
    assert logged_in.post("/balance/reschedule/undo", data={"csrf": "x"}).status_code == 403
