"""Εξέταση που δεν έχει ανακοινωθεί: εκτιμώμενη ημερομηνία που διορθώνεται αργότερα."""
from app import calfeed
from tests.conftest import csrf_from


def _row(conn):
    return conn.execute("SELECT slug, exam_date, exam_estimated FROM course").fetchone()


def _create(client, **data):
    token = csrf_from(client.get("/courses").text)
    return client.post("/courses", data={"name": "Physics", "csrf": token, **data})


def test_create_with_guess_then_correct_it(logged_in, conn):
    page = _create(logged_in, exam_date="2027-01-20", exam_estimated="on").text
    assert tuple(_row(conn)) == ("physics", "2027-01-20", 1)
    assert "εκτίμηση" in page
    assert "Εξέταση (εκτίμηση)" in calfeed.build(conn)
    settings = logged_in.get("/c/physics/settings").text
    assert 'name="exam_estimated" checked' in settings

    logged_in.post("/c/physics/settings", data={"name": "Physics", "exam_date": "2027-01-27",
                                                "csrf": csrf_from(settings)})
    assert tuple(_row(conn)) == ("physics", "2027-01-27", 0)
    assert "εκτίμηση" not in logged_in.get("/c/physics").text
    assert "(εκτίμηση)" not in calfeed.build(conn)


def test_guess_is_required_when_not_announced(logged_in, conn):
    r = _create(logged_in, exam_date="", exam_estimated="on")
    assert "βάλε μια εκτιμώμενη ημερομηνία" in r.text
    assert _row(conn) is None


def test_builder_keeps_the_flag(logged_in, conn):
    token = csrf_from(logged_in.get("/courses/builder").text)
    data = {"name": "Physics", "csrf": token, "start": "2099-01-01", "exam_date": "2099-02-01", "topics": "A\nB",
            "weekdays": [str(i) for i in range(7)], "revision_days": "1", "exam_estimated": "on"}
    assert 'name="exam_estimated" checked' in logged_in.post("/courses/builder", data=data).text
    logged_in.post("/courses/builder", data={**data, "action": "create"})
    assert _row(conn)["exam_estimated"] == 1
