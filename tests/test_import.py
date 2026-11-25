"""Εισαγωγή xlsx/ics, αυτόματη σύνδεση αρχείων και εξαγωγή."""
import io
import json
import zipfile
from datetime import date

import openpyxl
import pytest

from app import exporter, importer, linker, services


def test_parse_real_tracker(xlsx_bytes):
    plan = importer.parse_xlsx(xlsx_bytes)
    assert [w.sheet for w in plan.weeks] == ["Εβδ.1 - Βασικά", "Εβδ.2 - Πυρήνας", "Εβδ.3 - Ισχύς-Τριφασικά",
                                             "Εβδ.4 - Τετράπολα & Εξετάσεις"]
    tasks = plan.tasks
    assert len(tasks) == 28
    assert [t.day_no for t in tasks] == list(range(1, 29))
    assert tasks[0].date == date(2026, 11, 26) and tasks[-1].date == date(2026, 12, 23)
    assert all((b.date - a.date).days == 1 for a, b in zip(tasks, tasks[1:]))
    assert tasks[0].topic == "Lecture 1 - Στοιχεία 2 ακροδεκτών"
    assert tasks[0].source == "Lecture 1.pdf"
    assert tasks[0].row == 5 and tasks[6].row == 11
    assert {t.status for t in tasks} == {"Όχι"}
    assert plan.weeks[1].summary.startswith("03/12 - 09/12")


def test_parse_ics(ics_bytes):
    m = importer.parse_ics(ics_bytes)
    assert len(m) == 28
    assert m[1]["notes_file"] == "L01_Stoixeia.pdf"
    assert m[6]["notes_file"] == "L05_PedioSyxnotitas.pdf"
    assert m[10]["date"] == date(2026, 12, 5)
    assert m[14]["notes_file"] is None and m[28]["notes_file"] is None


def _synthetic(rows, title="Test - Πλάνο", dash_text="02/01/2027 - 10/01/2027"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Dashboard"
    ws["A1"] = title
    ws["A2"] = dash_text
    w = wb.create_sheet("Μέρος Α")
    w["A1"] = "Μέρος Α"
    w.append([])
    w.append([])
    w.append(["Ημέρα", "Ημερομηνία", "Θέμα", "Εργασία", "Αρχείο / Πηγή", "Ολοκληρώθηκε", "Σημειώσεις"])
    for r in rows:
        w.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_synthetic_year_rollover_and_status():
    data = _synthetic([
        ["Day 1", "30/12 Τρ", "A", "a", "-", "Ναι", "ok"],
        ["Day 2", "02/01 Πα", "B", "b", "", "σε εξέλιξη", None],
        [None, None, None, None, None, None, None],
        ["Day 3", "03/01", "C", "c", "", "", ""],
    ], dash_text="30/12/2026 - 03/01/2027")
    tasks = importer.parse_xlsx(data).tasks
    assert [t.date for t in tasks] == [date(2026, 12, 30), date(2027, 1, 2), date(2027, 1, 3)]
    assert [t.status for t in tasks] == ["Ναι", "Σε εξέλιξη", "Όχι"]
    assert tasks[0].notes == "ok"


def test_parse_rejects_without_header():
    wb = openpyxl.Workbook()
    wb.active["A1"] = "τίποτα"
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(importer.ImportError_):
        importer.parse_xlsx(buf.getvalue())


def _course(conn, name="ΑΗΚ"):
    return conn.execute("INSERT INTO course(name, slug) VALUES(?, ?)", (name, services.slugify(name))).lastrowid


def _file(conn, cid, name, category="Ημερήσιες σημειώσεις", grp="", mime="application/pdf"):
    return conn.execute(
        "INSERT INTO file(course_id, category, grp, name, stored, mime, size, sha256) VALUES(?,?,?,?,?,?,?,?)",
        (cid, category, grp, name, f"x/{name}", mime, 1, name)).lastrowid


def test_apply_plan_and_linking(conn, xlsx_bytes, ics_bytes):
    cid = _course(conn)
    l01 = _file(conn, cid, "L01_Stoixeia.pdf")
    lec1 = _file(conn, cid, "Lecture 1 - Στοιχεία 2 ακροδεκτών.pdf", "Διαφάνειες")
    lec10 = _file(conn, cid, "Lecture 10 - Μαγνητικά συζευγμένα κυκλώματα και Μετασχηματιστές.pdf", "Διαφάνειες")
    jpg = _file(conn, cid, "20220317_091809.jpg", "Φροντιστήρια", "Διάλεξη 5 - Ασκήσεις φροντιστηρίου", "image/jpeg")
    old = _file(conn, cid, "ΑΗΚ_Ιούνιος 2022.pdf", "Παλιά θέματα")
    plan = importer.parse_xlsx(xlsx_bytes)
    stats = importer.apply_plan(conn, cid, plan, importer.parse_ics(ics_bytes))
    assert stats["created"] == 28 and stats["weeks"] == 4
    by_day = {t["day_no"]: t for t in services.tasks(conn, cid)}
    links = services.task_files(conn, [t["id"] for t in by_day.values()])
    day1 = {f["id"]: f["is_primary"] for f in links[by_day[1]["id"]]}
    assert day1 == {l01: 1, lec1: 0}  # όχι Lecture 10
    assert {f["id"] for f in links[by_day[15]["id"]]} == {lec10}
    assert {f["id"] for f in links[by_day[5]["id"]]} == {jpg}
    day21 = links[by_day[21]["id"]]
    assert [(f["id"], f["is_primary"]) for f in day21] == [(old, 1)]
    assert services.primary_file(conn, by_day[1]["id"])["id"] == l01


def test_reimport_merge_keeps_progress(conn, xlsx_bytes):
    cid = _course(conn)
    plan = importer.parse_xlsx(xlsx_bytes)
    importer.apply_plan(conn, cid, plan)
    t = services.tasks(conn, cid)[0]
    services.set_status(conn, t["id"], "Ναι")
    services.set_notes(conn, t["id"], "δύσκολο")
    services.postpone(conn, services.tasks(conn, cid)[1]["id"], date(2026, 12, 1))
    stats = importer.apply_plan(conn, cid, importer.parse_xlsx(xlsx_bytes))
    assert stats == {"created": 0, "updated": 28, "weeks": 0}
    rows = {r["day_no"]: r for r in services.tasks(conn, cid)}
    assert rows[1]["status"] == "Ναι" and rows[1]["notes"] == "δύσκολο"
    assert rows[2]["date"] == "2026-12-01" and rows[2]["original_date"] == "2026-11-27"
    importer.apply_plan(conn, cid, importer.parse_xlsx(xlsx_bytes), mode="replace")
    assert services.tasks(conn, cid)[0]["status"] == "Όχι"


def test_export_template_roundtrip_preserves_chart(conn, xlsx_bytes):
    cid = _course(conn)
    importer.apply_plan(conn, cid, importer.parse_xlsx(xlsx_bytes))
    rows = services.tasks(conn, cid)
    services.set_status(conn, rows[0]["id"], "Ναι")
    services.set_status(conn, rows[9]["id"], "Σε εξέλιξη")
    services.set_notes(conn, rows[9]["id"], "πρόσημα <Thevenin> & εξαρτημένες")
    services.postpone(conn, rows[27]["id"], date(2026, 12, 24))
    out = exporter.export_template(xlsx_bytes, services.tasks(conn, cid))
    z = zipfile.ZipFile(io.BytesIO(out))
    assert "xl/charts/chart1.xml" in z.namelist()
    assert z.testzip() is None
    again = importer.parse_xlsx(out)
    by_day = {t.day_no: t for t in again.tasks}
    assert by_day[1].status == "Ναι"
    assert by_day[10].status == "Σε εξέλιξη" and by_day[10].notes == "πρόσημα <Thevenin> & εξαρτημένες"
    assert by_day[2].status == "Όχι"
    assert by_day[28].date == date(2026, 12, 24)
    wb = openpyxl.load_workbook(io.BytesIO(out))
    assert wb["Dashboard"]["D5"].value.startswith("=COUNTIF(")  # formulas παραμένουν
    assert wb["Εβδ.2 - Πυρήνας"].data_validations.dataValidation  # dropdown παραμένει


def test_set_cell_inserts_missing_cell():
    xml = '<sheetData><row r="5"><c r="A5" s="1"><v>1</v></c><c r="H5" s="2"/></row></sheetData>'
    out = exporter.set_cell(xml, "F5", "Ναι")
    assert out.index('r="A5"') < out.index('r="F5"') < out.index('r="H5"')
    assert "<t xml:space=\"preserve\">Ναι</t>" in out
    assert exporter.set_cell(out, "F5", "") .count('r="F5"') == 1


def test_export_new_and_csv(conn, xlsx_bytes):
    cid = _course(conn)
    importer.apply_plan(conn, cid, importer.parse_xlsx(xlsx_bytes))
    conn.execute("INSERT INTO task(course_id, date, topic) VALUES(?, '2026-12-24', 'Extra')", (cid,))
    course = conn.execute("SELECT * FROM course WHERE id=?", (cid,)).fetchone()
    data = exporter.export_new(course, services.weeks(conn, cid), services.tasks(conn, cid))
    plan = importer.parse_xlsx(data, 2026)
    assert len(plan.tasks) == 29
    csv_text = exporter.export_csv(services.tasks(conn, cid), {}).decode("utf-8-sig")
    assert csv_text.count("\n") == 30 and "Extra" in csv_text


def test_split_source_and_norm():
    assert linker.split_source("Lecture 8.pdf + Thevenin_Methodologia_Askiseis.pdf") == [
        "Lecture 8.pdf", "Thevenin_Methodologia_Askiseis.pdf"]
    assert linker.split_source("Παλιά θέματα/AHK Ιούνιος 2025.pdf, 2023 (2).pdf") == ["AHK Ιούνιος 2025.pdf", "2023 (2).pdf"]
    assert linker.split_source("Διάλεξη 8 - Ασκήσεις φροντιστηρίου + Λύσεις/") == [
        "Διάλεξη 8 - Ασκήσεις φροντιστηρίου", "Λύσεις"]
    assert linker.split_source("-") == []
    assert linker.norm("ΑΗΚ_Σεπτέμβρης 2022.pdf") == linker.norm("αηκ σεπτεμβρησ 2022")


def test_hints_fallback_without_ics(xlsx_bytes):
    t = importer.parse_xlsx(xlsx_bytes).tasks[6]  # Day 7, Lecture 6
    assert importer.build_hints(t, None)[:2] == ["L06", "Lecture 6.pdf"]
    assert json.dumps(importer.build_hints(t, None), ensure_ascii=False)
