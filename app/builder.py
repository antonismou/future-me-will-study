"""Δημιουργός πλάνου: από λίστα θεμάτων με μέγεθος (μικρό/μεσαίο/μεγάλο) σε εργασίες ανά ημέρα.

Κάθε μέγεθος έχει μονάδες (1/2/3). Οι μονάδες μοιράζονται ομοιόμορφα και με τη σειρά στις ημέρες μελέτης:
ένα μεγάλο θέμα μπορεί να σπάσει σε μέρη, και πολλά μικρά να πέσουν στην ίδια ημέρα.
"""
from __future__ import annotations

import sqlite3
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta

SMALL, MEDIUM, LARGE = 1, 2, 3
SIZE_LABELS = {SMALL: "μικρό", MEDIUM: "μεσαίο", LARGE: "μεγάλο"}
MAX_TOPICS = 200
MAX_SPAN_DAYS = 730
MAX_REVISION_DAYS = 60
REVISION = "Επανάληψη"


class PlanError(ValueError):
    pass


def _plain(text: str) -> str:
    """Πεζά, χωρίς τόνους."""
    s = unicodedata.normalize("NFKD", text.strip().lower())
    return "".join(c for c in s if not unicodedata.combining(c))


_SIZES = {
    **dict.fromkeys(("1", "s", "small", "μικρο", "μικρη", "μικρος"), SMALL),
    **dict.fromkeys(("2", "m", "medium", "μεσαιο", "μεσαια", "μεσαιος", "μετριο"), MEDIUM),
    **dict.fromkeys(("3", "l", "large", "μεγαλο", "μεγαλη", "μεγαλος"), LARGE),
}


@dataclass
class Topic:
    title: str
    size: int


@dataclass
class Item:
    date: date
    day_no: int
    topic: str
    effort: float          # μονάδες που αντιστοιχούν σε αυτή την εργασία
    revision: bool = False


@dataclass
class Plan:
    items: list[Item] = field(default_factory=list)
    study_days: int = 0
    revision_days: int = 0

    @property
    def max_per_day(self) -> int:
        counts: dict[date, int] = {}
        for i in self.items:
            counts[i.date] = counts.get(i.date, 0) + 1
        return max(counts.values(), default=0)

    def weeks(self) -> list[tuple[str, list[Item]]]:
        """Εργασίες ανά ημερολογιακή εβδομάδα (Δευτέρα–Κυριακή)."""
        grouped: dict[date, list[Item]] = {}
        for i in self.items:
            grouped.setdefault(i.date - timedelta(days=i.date.weekday()), []).append(i)
        out = []
        for n, monday in enumerate(sorted(grouped), 1):
            items = grouped[monday]
            out.append((f"Εβδομάδα {n} ({items[0].date:%d/%m} – {items[-1].date:%d/%m})", items))
        return out


def parse_topics(text: str) -> list[Topic]:
    """Μία γραμμή ανά θέμα: «Τίτλος» ή «Τίτλος | μέγεθος». Χωρίς μέγεθος θεωρείται μεσαίο."""
    topics: list[Topic] = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        size = MEDIUM
        if "|" in line:
            line, _, word = line.rpartition("|")
            line, word = line.strip(), word.strip()
            if word:
                if _plain(word) not in _SIZES:
                    raise PlanError(f"Γραμμή {n}: άγνωστο μέγεθος «{word}». Γράψε μικρό, μεσαίο ή μεγάλο.")
                size = _SIZES[_plain(word)]
        if not line:
            raise PlanError(f"Γραμμή {n}: λείπει ο τίτλος του θέματος.")
        topics.append(Topic(line[:280], size))
    if not topics:
        raise PlanError("Γράψε τουλάχιστον ένα θέμα.")
    if len(topics) > MAX_TOPICS:
        raise PlanError(f"Έως {MAX_TOPICS} θέματα.")
    return topics


def parse_dates(text: str) -> set[date]:
    """Ημέρες χωρίς διάβασμα: ΗΗ/ΜΜ/ΕΕΕΕ ή ΕΕΕΕ-ΜΜ-ΗΗ, χωρισμένες με κενό, κόμμα ή νέα γραμμή."""
    out: set[date] = set()
    for word in text.replace(",", " ").split():
        try:
            if "/" in word:
                d, m, y = word.split("/")
                if len(y) != 4:
                    raise ValueError
                out.add(date(int(y), int(m), int(d)))
            else:
                out.add(date.fromisoformat(word))
        except ValueError:
            raise PlanError(f"Μη έγκυρη ημερομηνία «{word}». Γράψε π.χ. 25/12/2026.")
    return out


def build(topics: list[Topic], start: date, exam: date, weekdays: set[int], days_off: set[date],
          revision_days: int) -> Plan:
    """Μοιράζει τα θέματα στις ημέρες από `start` έως την παραμονή της εξέτασης."""
    if exam <= start:
        raise PlanError("Η εξέταση πρέπει να είναι μετά την ημέρα έναρξης.")
    if (exam - start).days > MAX_SPAN_DAYS:
        raise PlanError("Η εξέταση απέχει πάνω από δύο χρόνια.")
    if not 0 <= revision_days <= MAX_REVISION_DAYS:
        raise PlanError(f"Οι ημέρες επανάληψης πρέπει να είναι από 0 έως {MAX_REVISION_DAYS}.")
    if not weekdays:
        raise PlanError("Διάλεξε τουλάχιστον μία ημέρα της εβδομάδας.")
    days = [d for i in range((exam - start).days)
            if (d := start + timedelta(days=i)).weekday() in weekdays and d not in days_off]
    if len(days) <= revision_days:
        raise PlanError(f"Υπάρχουν μόνο {len(days)} διαθέσιμες ημέρες πριν την εξέταση, με {revision_days} για "
                        "επανάληψη δεν μένει ημέρα για τα θέματα. Μείωσε την επανάληψη ή πρόσθεσε ημέρες.")
    study = days[:len(days) - revision_days]
    n, total = len(study), sum(t.size for t in topics)

    # Ακέραια αριθμητική: κάθε μονάδα (ή, αν οι ημέρες είναι περισσότερες, κάθε ημέρα) πάει σε μία θέση.
    owner = [t for t, topic in enumerate(topics) for _ in range(topic.size)]
    slots: list[dict[int, float]] = [{} for _ in range(n)]   # ημέρα -> {θέμα: μονάδες}
    if total >= n:
        for unit, t in enumerate(owner):
            day = unit * n // total
            slots[day][t] = slots[day].get(t, 0) + 1
    else:
        for day in range(n):
            slots[day][owner[day * total // n]] = total / n

    parts: dict[int, int] = {}
    for slot in slots:
        for t in slot:
            parts[t] = parts.get(t, 0) + 1
    seen: dict[int, int] = {}
    plan = Plan(study_days=n, revision_days=revision_days)
    for day_no, (d, slot) in enumerate(zip(study, slots), 1):
        for t, effort in slot.items():
            seen[t] = seen.get(t, 0) + 1
            suffix = f" (μέρος {seen[t]}/{parts[t]})" if parts[t] > 1 else ""
            plan.items.append(Item(d, day_no, topics[t].title + suffix, effort))
    for day_no, d in enumerate(days[n:], n + 1):
        plan.items.append(Item(d, day_no, REVISION, MEDIUM, revision=True))
    return plan


def save(conn: sqlite3.Connection, course_id: int, plan: Plan) -> int:
    """Γράφει το πλάνο ως κανονικές εβδομάδες και εργασίες. Επιστρέφει πόσες εργασίες δημιουργήθηκαν."""
    for position, (title, items) in enumerate(plan.weeks()):
        week_id = conn.execute("INSERT INTO week(course_id, position, title) VALUES(?,?,?)",
                               (course_id, position, title)).lastrowid
        conn.executemany(
            "INSERT INTO task(course_id, week_id, day_no, date, topic, effort) VALUES(?,?,?,?,?,?)",
            [(course_id, week_id, i.day_no, i.date.isoformat(), i.topic, i.effort) for i in items])
    return len(plan.items)
