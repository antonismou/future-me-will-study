"""Ποια ειδοποίηση στέλνεται πότε."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.scheduler import CourseSnap, TaskSnap, in_quiet, parse_hm, plan_due, run_tick

ATH = ZoneInfo("Europe/Athens")


def S(**over):
    s = {
        "timezone": "Europe/Athens", "weekdays": [0, 1, 2, 3, 4, 5, 6],
        "morning_enabled": True, "morning_time": "08:00",
        "evening_enabled": True, "evening_times": ["20:00", "22:30"],
        "weekly_enabled": True, "weekly_day": 6, "weekly_time": "20:00",
        "overdue_enabled": True, "countdown_enabled": True, "streak_enabled": True,
        "quiet_enabled": False, "quiet_start": "23:30", "quiet_end": "07:00", "grace_minutes": 60,
    }
    s.update(over)
    return s


def at(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ATH)


D = date(2026, 11, 26)  # Πέμπτη, Day 1


def course(status="Όχι", d=D, extra=()):
    return [CourseSnap(1, True, [TaskSnap(10, d, status), *extra])]


def kinds(due):
    return sorted((x.kind, x.key.split(":")[0]) for x in due)


def test_nothing_before_morning():
    assert plan_due(at(2026, 11, 26, 7, 59), S(), course(), set()) == []


def test_morning_at_time():
    due = plan_due(at(2026, 11, 26, 8, 0), S(), course(), set())
    assert [(x.kind, x.task_id) for x in due] == [("morning", 10)]
    assert due[0].key == "morning:1:2026-11-26:10"


def test_morning_not_twice():
    first = plan_due(at(2026, 11, 26, 8, 0), S(), course(), set())
    again = plan_due(at(2026, 11, 26, 8, 1), S(), course(), {d.key for d in first})
    assert again == []


def test_restart_catch_up_within_grace():
    # Ο server ήταν κάτω 07:50–08:40: η πρωινή στέλνεται όταν ξανασηκωθεί.
    assert [x.kind for x in plan_due(at(2026, 11, 26, 8, 40), S(), course(), set())] == ["morning"]


def test_missed_outside_grace_is_skipped():
    assert plan_due(at(2026, 11, 26, 9, 1), S(), course(), set()) == []
    assert plan_due(at(2026, 11, 26, 9, 1), S(grace_minutes=120), course(), set())[0].kind == "morning"


def test_evening_only_if_not_done():
    assert [x.key.split(":")[0] for x in plan_due(at(2026, 11, 26, 20, 5), S(), course("Όχι"), set())] == ["evening0"]
    assert [x.key.split(":")[0] for x in plan_due(at(2026, 11, 26, 22, 30), S(), course("Σε εξέλιξη"), set())] == ["evening1"]
    assert plan_due(at(2026, 11, 26, 20, 5), S(), course("Ναι"), set()) == []


def test_morning_skipped_if_already_done_unless_overdue():
    assert plan_due(at(2026, 11, 26, 8, 0), S(), course("Ναι"), set()) == []
    late = TaskSnap(9, D - timedelta(days=1), "Όχι")
    due = plan_due(at(2026, 11, 26, 8, 0), S(), course("Ναι", extra=[late]), set())
    assert [(x.kind, x.task_id) for x in due] == [("morning", None)]
    assert plan_due(at(2026, 11, 26, 8, 0), S(overdue_enabled=False), course("Ναι", extra=[late]), set()) == []


def test_disabled_types():
    assert plan_due(at(2026, 11, 26, 8, 0), S(morning_enabled=False), course(), set()) == []
    assert plan_due(at(2026, 11, 26, 20, 0), S(evening_enabled=False), course(), set()) == []
    assert plan_due(at(2026, 11, 26, 20, 0), S(evening_times=[]), course(), set()) == []


def test_weekday_filter():
    # 26/11/2026 είναι Πέμπτη (3)
    assert plan_due(at(2026, 11, 26, 8, 0), S(weekdays=[0, 1, 2, 4, 5, 6]), course(), set()) == []


def test_course_notify_off():
    c = [CourseSnap(1, False, [TaskSnap(10, D, "Όχι")])]
    assert plan_due(at(2026, 11, 26, 8, 0), S(), c, set()) == []


def test_quiet_hours_mark_skip():
    due = plan_due(at(2026, 11, 26, 8, 0), S(quiet_enabled=True, quiet_start="07:30", quiet_end="09:00"), course(), set())
    assert [(x.kind, x.skip_reason) for x in due] == [("morning", "quiet")]


def test_quiet_window_wraps_midnight():
    st, en = parse_hm("23:00"), parse_hm("07:00")
    assert in_quiet(parse_hm("23:30"), st, en) and in_quiet(parse_hm("06:59"), st, en)
    assert not in_quiet(parse_hm("07:00"), st, en) and not in_quiet(parse_hm("12:00"), st, en)


def test_evening_near_midnight_caught_after_midnight():
    s = S(evening_times=["23:45"])
    due = plan_due(at(2026, 11, 27, 0, 15), s, course(), set())  # 30' μετά, άλλη ημερομηνία
    assert [(x.kind, x.local_date) for x in due] == [("evening", D)]


def test_weekly_summary_on_sunday():
    sunday = date(2026, 11, 29)
    c = course(d=sunday)
    due = plan_due(at(2026, 11, 29, 20, 0), S(evening_times=[]), c, set())
    assert [x.kind for x in due] == ["weekly"]
    assert plan_due(at(2026, 11, 28, 20, 0), S(evening_times=[]), course(d=date(2026, 11, 28)), set()) == []


def test_postponed_task_moves_notifications():
    # Μετά την αναβολή η εργασία είναι στις 27/11: καμία βραδινή στις 26/11, πρωινή στις 27/11.
    c = course(d=D + timedelta(days=1))
    assert plan_due(at(2026, 11, 26, 20, 0), S(), c, set()) == []
    assert [x.task_id for x in plan_due(at(2026, 11, 27, 8, 0), S(), c, set())] == [10]


def test_timezone_is_respected():
    # 08:00 Αθήνα = 06:00 UTC τον Νοέμβριο
    utc_now = datetime(2026, 11, 26, 6, 0, tzinfo=timezone.utc)
    assert [x.kind for x in plan_due(utc_now, S(), course(), set())] == ["morning"]
    assert plan_due(utc_now - timedelta(minutes=1), S(), course(), set()) == []
    # ίδια στιγμή, άλλη ζώνη: στο Λονδίνο είναι 06:00 -> τίποτα
    assert plan_due(utc_now, S(timezone="Europe/London"), course(), set()) == []


def test_dst_spring_forward():
    # 29/03/2026 Αθήνα γίνεται UTC+3: 08:00 τοπική = 05:00 UTC
    d = date(2026, 3, 29)
    c = course(d=d)
    assert plan_due(datetime(2026, 3, 29, 5, 0, tzinfo=timezone.utc), S(), c, set())[0].kind == "morning"
    assert plan_due(datetime(2026, 3, 29, 4, 59, tzinfo=timezone.utc), S(), c, set()) == []


def test_dst_fall_back_ambiguous_hour_sends_once():
    # 25/10/2026: η 03:30 υπάρχει δύο φορές. Στέλνεται μόνο μία.
    d = date(2026, 10, 25)
    s = S(morning_time="03:30", evening_enabled=False, weekly_enabled=False)
    c = course(d=d)
    sent: set[str] = set()
    start = datetime(2026, 10, 24, 23, 0, tzinfo=timezone.utc)
    hits = 0
    for minute in range(0, 4 * 60):
        due = plan_due(start + timedelta(minutes=minute), s, c, sent)
        hits += len(due)
        sent |= {x.key for x in due}
    assert hits == 1


# ---------------------------------------------------------------- run_tick με βάση

def _seed(conn, status="Όχι"):
    cid = conn.execute("INSERT INTO course(name, slug) VALUES('ΑΗΚ', 'ahk')").lastrowid
    tid = conn.execute("INSERT INTO task(course_id, day_no, date, topic, status) VALUES(?, 1, ?, 'Lecture 1', ?)",
                       (cid, D.isoformat(), status)).lastrowid
    conn.commit()
    return cid, tid


def test_run_tick_sends_once_and_survives_restart(conn):
    _seed(conn)
    sent = []
    now = at(2026, 11, 26, 8, 0).astimezone(timezone.utc)
    assert run_tick(conn, now, lambda s, m: sent.append(m)) == ["morning:1:2026-11-26:1"]
    run_tick(conn, now + timedelta(seconds=30), lambda s, m: sent.append(m))
    # «restart»: νέα σύνδεση, ίδια βάση
    from app.db import connect

    conn2 = connect()
    run_tick(conn2, now + timedelta(minutes=2), lambda s, m: sent.append(m))
    conn2.close()
    assert len(sent) == 1
    msg = sent[0]
    assert "Lecture 1" in msg["title"]
    assert [a["label"] for a in msg["actions"]] == ["✅ Έγινε", "⏳ Σε εξέλιξη", "📅 Αναβολή"]
    assert all(a["url"].startswith("https://study.example.test/a/") for a in msg["actions"])


def test_run_tick_retries_failed_then_gives_up(conn):
    _seed(conn)
    calls = []

    def boom(s, m):
        calls.append(1)
        raise RuntimeError("ntfy down")

    now = at(2026, 11, 26, 8, 0).astimezone(timezone.utc)
    for i in range(6):
        run_tick(conn, now + timedelta(minutes=i), boom)
    assert len(calls) == 3
    row = conn.execute("SELECT status, attempts FROM notification_log").fetchone()
    assert (row["status"], row["attempts"]) == ("failed", 3)


def test_run_tick_quiet_logged_not_sent(conn):
    _seed(conn)
    from app.settings_store import save_settings

    save_settings(conn, {"quiet_enabled": True, "quiet_start": "07:00", "quiet_end": "09:00"})
    conn.commit()
    sent = []
    run_tick(conn, at(2026, 11, 26, 8, 0).astimezone(timezone.utc), lambda s, m: sent.append(m))
    assert sent == []
    assert conn.execute("SELECT status FROM notification_log").fetchone()["status"] == "skipped"


def test_evening_message_includes_streak(conn):
    cid, tid = _seed(conn)
    conn.execute("INSERT INTO task(course_id, date, topic, status) VALUES(?, ?, 'prev', 'Ναι')",
                 (cid, (D - timedelta(days=1)).isoformat()))
    conn.commit()
    sent = []
    run_tick(conn, at(2026, 11, 26, 20, 0).astimezone(timezone.utc), lambda s, m: sent.append(m))
    assert len(sent) == 1 and "Streak: 1" in sent[0]["message"] and sent[0]["priority"] == 4


@pytest.mark.parametrize("statuses,expected", [
    (["Ναι", "Ναι", "Ναι"], 3), (["Όχι", "Ναι", "Ναι"], 2), (["Ναι", "Όχι", "Ναι"], 1),
    (["Ναι", "Ναι", "Όχι"], 2),  # η σημερινή που δεν έχει γίνει ακόμα δεν σπάει το streak
])
def test_streak(statuses, expected):
    from app.services import streak_from

    rows = [{"date": D - timedelta(days=2 - i), "status": s} for i, s in enumerate(statuses)]  # προχθές, χθες, σήμερα
    assert streak_from(rows, D) == expected
