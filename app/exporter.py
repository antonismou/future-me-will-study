"""Εξαγωγή προόδου: (α) πίσω στο αρχικό xlsx (διατηρεί formulas, γράφημα, μορφοποίηση), (β) νέο xlsx, (γ) CSV."""
from __future__ import annotations

import csv
import io
import re
import sqlite3
import zipfile
from datetime import date
from html import escape, unescape

import openpyxl
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

GR_DAYS = ["Δε", "Τρ", "Τε", "Πε", "Πα", "Σα", "Κυ"]


def short_date(d: date) -> str:
    return f"{d.day:02d}/{d.month:02d} {GR_DAYS[d.weekday()]}"


# ---------------------------------------------------------------- template (XML)

def _sheet_paths(z: zipfile.ZipFile) -> dict[str, str]:
    wb = z.read("xl/workbook.xml").decode("utf-8")
    rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    targets = {}
    for m in re.finditer(r"<Relationship\b[^>]*>", rels):
        tag = m.group(0)
        rid = re.search(r'\bId="([^"]+)"', tag)
        tgt = re.search(r'\bTarget="([^"]+)"', tag)
        if rid and tgt:
            t = tgt.group(1).lstrip("/")
            targets[rid.group(1)] = t if t.startswith("xl/") else "xl/" + t
    out = {}
    for m in re.finditer(r"<sheet\b[^>]*>", wb):
        tag = m.group(0)
        name = re.search(r'\bname="([^"]*)"', tag)
        rid = re.search(r'\br:id="([^"]+)"', tag)
        if name and rid and rid.group(1) in targets:
            out[unescape(name.group(1))] = targets[rid.group(1)]
    return out


def _col_num(ref: str) -> int:
    return column_index_from_string(re.match(r"[A-Z]+", ref).group())


def set_cell(xml: str, ref: str, text: str) -> str:
    """Γράφει inline string στο κελί ref, κρατώντας το style (s=...)."""
    row_num = re.search(r"\d+", ref).group()
    cell_re = re.compile(rf'<c r="{ref}"(?P<attrs>(?:\s[^>]*?)?)(?:/>|>(?P<body>.*?)</c>)', re.S)
    m = cell_re.search(xml)
    attrs = re.sub(r'\st="[^"]*"', "", m.group("attrs")) if m else ""
    new = (f'<c r="{ref}"{attrs} t="inlineStr"><is><t xml:space="preserve">{escape(text, quote=False)}</t></is></c>'
           if text else f'<c r="{ref}"{attrs}/>')
    if m:
        return xml[:m.start()] + new + xml[m.end():]
    row_re = re.compile(rf'<row r="{row_num}"(?P<attrs>(?:\s[^>]*?)?)(?:/>|>(?P<body>.*?)</row>)', re.S)
    rm = row_re.search(xml)
    if not rm:
        return xml  # η γραμμή δεν υπάρχει στο template — παράλειψη
    body = rm.group("body") or ""
    target = _col_num(ref)
    insert_at = len(body)
    for cm in re.finditer(r'<c r="([A-Z]+)\d+"', body):
        if _col_num(cm.group(1)) > target:
            insert_at = cm.start()
            break
    body = body[:insert_at] + new + body[insert_at:]
    return xml[:rm.start()] + f'<row r="{row_num}"{rm.group("attrs")}>{body}</row>' + xml[rm.end():]


def export_template(template: bytes, tasks: list[sqlite3.Row]) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(template))
    paths = _sheet_paths(zin)
    updates: dict[str, dict[str, str]] = {}
    for t in tasks:
        if not t["src_sheet"] or not t["src_row"] or t["src_sheet"] not in paths:
            continue
        cells = updates.setdefault(paths[t["src_sheet"]], {})
        r = t["src_row"]
        cells[f"F{r}"] = t["status"]
        cells[f"G{r}"] = t["notes"] or ""
        if t["original_date"]:
            cells[f"B{r}"] = short_date(date.fromisoformat(t["date"]))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename in updates:
                xml = data.decode("utf-8")
                for ref, text in updates[item.filename].items():
                    xml = set_cell(xml, ref, text)
                data = xml.encode("utf-8")
            elif item.filename == "xl/workbook.xml":
                xml = data.decode("utf-8")
                if "<calcPr" in xml and "fullCalcOnLoad" not in xml:
                    xml = xml.replace("<calcPr", '<calcPr fullCalcOnLoad="1"', 1)
                elif "<calcPr" not in xml:
                    xml = xml.replace("</workbook>", '<calcPr fullCalcOnLoad="1"/></workbook>')
                data = xml.encode("utf-8")
            elif item.filename == "xl/calcChain.xml":
                pass
            zout.writestr(item, data)
    return out.getvalue()


# ---------------------------------------------------------------- νέο xlsx

def export_new(course: sqlite3.Row, weeks: list[sqlite3.Row], tasks: list[sqlite3.Row]) -> bytes:
    wb = openpyxl.Workbook()
    dash = wb.active
    dash.title = "Dashboard"
    dash["A1"] = f"{course['name']} - Πλάνο διαβάσματος"
    dash["A1"].font = Font(bold=True, size=14)
    for i, h in enumerate(["Εβδομάδα", "Σύνολο εργασιών", "Ολοκληρωμένες", "% Πρόοδος"], 1):
        dash.cell(3, i, h).font = Font(bold=True)
    groups: list[tuple[str, list]] = []
    by_week = {w["id"]: (w["title"], []) for w in weeks}
    loose: list = []
    for t in tasks:
        (by_week[t["week_id"]][1] if t["week_id"] in by_week else loose).append(t)
    groups = list(by_week.values()) + ([("Χωρίς εβδομάδα", loose)] if loose else [])
    head_fill = PatternFill("solid", fgColor="1E3A8A")
    used: set[str] = {"Dashboard"}
    drow = 4
    for title, items in groups:
        name = re.sub(r"[\[\]:*?/\\]", "-", title)[:31] or "Εβδομάδα"
        base, n = name, 2
        while name in used:
            name = f"{base[:28]}-{n}"
            n += 1
        used.add(name)
        ws = wb.create_sheet(name)
        ws["A1"] = title
        ws["A1"].font = Font(bold=True, size=13)
        headers = ["Ημέρα", "Ημερομηνία", "Θέμα / Διάλεξη", "Εργασία", "Αρχείο / Πηγή", "Ολοκληρώθηκε", "Σημειώσεις"]
        for i, h in enumerate(headers, 1):
            c = ws.cell(4, i, h)
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = head_fill
        for i, w in enumerate([9, 12, 36, 44, 34, 15, 40], 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        dv = DataValidation(type="list", formula1='"Όχι,Σε εξέλιξη,Ναι"', allow_blank=True)
        ws.add_data_validation(dv)
        last = 4
        for r, t in enumerate(items, 5):
            d = date.fromisoformat(t["date"])
            ws.append([f"Day {t['day_no']}" if t["day_no"] else "", short_date(d), t["topic"], t["description"],
                       t["source"], t["status"], t["notes"]])
            dv.add(f"F{r}")
            last = r
        q = name.replace("'", "''")
        dash.cell(drow, 1, title)
        dash.cell(drow, 2, f"=COUNTA('{q}'!F5:F{max(last, 5)})")
        dash.cell(drow, 3, f"=COUNTIF('{q}'!F5:F{max(last, 5)},\"Ναι\")")
        dash.cell(drow, 4, f"=IF(B{drow}=0,0,C{drow}/B{drow})").number_format = "0%"
        drow += 1
    dash.cell(drow, 1, "ΣΥΝΟΛΟ").font = Font(bold=True)
    dash.cell(drow, 2, f"=SUM(B4:B{drow - 1})")
    dash.cell(drow, 3, f"=SUM(C4:C{drow - 1})")
    dash.cell(drow, 4, f"=IF(B{drow}=0,0,C{drow}/B{drow})").number_format = "0%"
    dash.column_dimensions["A"].width = 34
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- CSV

def export_csv(tasks: list[sqlite3.Row], week_titles: dict[int, str], seconds: dict[int, int] | None = None) -> bytes:
    seconds = seconds or {}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Εβδομάδα", "Ημέρα", "Ημερομηνία", "Αρχική ημερομηνία", "Θέμα", "Εργασία", "Πηγή",
                "Ολοκληρώθηκε", "Σημειώσεις", "Δύσκολο", "Ολοκληρώθηκε στις", "Λεπτά μελέτης"])
    for t in tasks:
        w.writerow([week_titles.get(t["week_id"], ""), t["day_no"] or "", t["date"], t["original_date"] or "",
                    t["topic"], t["description"], t["source"], t["status"], t["notes"],
                    "Ναι" if t["hard"] else "", t["completed_at"] or "", seconds.get(t["id"], 0) // 60])
    return buf.getvalue().encode("utf-8-sig")
