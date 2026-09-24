"""Lesson plans from the Agenda's weekly class tabs.

Columns are found by their header text, rows by their week label, and tabs by
name. Whatever can be read is shown: a missing tab, header or cell becomes a
note on that class, never a failed page. The plain-text cells of a lesson
(topic, each day's plan and note, to-do, lab / prep) can be edited from the
dashboard; see agenda_edit.py. Cells with links or formulas are only read.
"""
import re
from datetime import date, datetime, timedelta

from . import google

CLASSES = (("biology", "Biology"), ("forensics", "Forensics"), ("anatomy", "A&P"))
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")
LAST_ROW = 80
URL = re.compile(r"https://[^\s\"'<>]+")
FORM = re.compile(r"https://docs\.google\.com/forms/d/(e/)?([A-Za-z0-9_-]{20,})")


def norm(value):
    """'🌿 Biology' -> 'biology', 'Mon — plan' -> 'monplan', 'A&P' -> 'a&p'."""
    return re.sub(r"[^a-z0-9&]+", "", str(value or "").lower())


def text(cell):
    value = (cell or {}).get("formattedValue", "")
    return value if isinstance(value, str) else str(value)


def links(cell):
    """Every (url, label) in a cell: rich-text links, HYPERLINK formulas, a
    whole-cell link, or a pasted URL. First label wins; duplicates collapse."""
    if not cell:
        return []
    found = []
    shown = text(cell)
    runs = cell.get("textFormatRuns") or []
    if runs:
        units = shown.encode("utf-16-le")
        starts = [run.get("startIndex", 0) for run in runs] + [len(units) // 2]
        for i, run in enumerate(runs):
            uri = ((run.get("format") or {}).get("link") or {}).get("uri")
            if uri:
                piece = units[starts[i] * 2:starts[i + 1] * 2].decode("utf-16-le", "ignore")
                found.append((uri, " ".join(piece.split()).strip(" ·|,")))
    formula = (cell.get("userEnteredValue") or {}).get("formulaValue") or ""
    for match in re.finditer(r'HYPERLINK\(\s*"([^"]+)"\s*(?:[,;]\s*"([^"]*)")?', formula, re.I):
        found.append((match.group(1), (match.group(2) or "").strip()))
    if cell.get("hyperlink"):
        found.append((cell["hyperlink"], " ".join(shown.split())))
    for match in URL.finditer(shown):
        found.append((match.group(0).rstrip(".,;)"), ""))
    merged = {}
    for url, label in found:
        if url.startswith("https://"):
            if url not in merged or (not merged[url] and label):
                merged[url] = label
    return list(merged.items())


def form_link(url, label=""):
    """A lesson's Google Form. Only the direct /forms/d/<id>/ link can be read
    by the API; a published /forms/d/e/... link is shown but flagged."""
    match = FORM.match(url)
    if match:
        direct = not match.group(1)
        return {"form_id": match.group(2) if direct else None, "url": url, "label": label,
                "problem": None if direct else
                "This is a published Form link. Replace it in the Agenda with the Form's "
                "own link (open the Form's editor and copy its address) so responses can be read."}
    if url.startswith("https://forms.gle/"):
        return {"form_id": None, "url": url, "label": label,
                "problem": "Short forms.gle links can't be read. Put the Form's own link in the Agenda."}
    return None


def parse_date(value):
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def find_tab(tabs, name):
    """The tab whose name is `name` once emoji and spacing are ignored."""
    matches = [title for title in tabs if norm(title) == norm(name)]
    return matches[0] if len(matches) == 1 else None


def parse_weeks(cells):
    for r in range(1, 8):
        header = {c: norm(text(cells.get((r, c)))) for c in range(1, 12)}
        values = set(header.values())
        if "week" in values and "monday" in values:
            break
    else:
        return [], "The Weeks tab has no row with 'week' and 'Monday' headers."
    col = {v: c for c, v in header.items() if v}
    closures = next((c for v, c in col.items() if "closure" in v), None)
    weeks = []
    for row in range(r + 1, LAST_ROW + 1):
        label = text(cells.get((row, col["week"]))).strip()
        monday = parse_date(text(cells.get((row, col["monday"]))))
        if label and monday:
            weeks.append({"label": label, "monday": monday.isoformat(),
                          "closures": text(cells.get((row, closures))).strip() if closures else ""})
    return weeks, None


def parse_class_tab(cells):
    header_row = next((r for r in range(1, 8) if any(
        norm(text(cells.get((r, c)))) == "topic" for c in range(1, 27))), None)
    if not header_row:
        return None, "No header row with 'Topic' was found."
    headers = {c: norm(text(cells.get((header_row, c)))) for c in range(1, 27)}

    def col(*names, starts=None, ends=None):
        for c, h in headers.items():
            if h and (h in names or (starts and h.startswith(starts)) or (ends and h.endswith(ends))):
                return c
        return None

    columns = {
        "week": col("wk", "week") or 1, "dates": col(starts="dates"), "topic": col("topic"),
        "todo": col("todo"), "prep": col(ends="prep"),
        "teacher_url": col("teacherurl"), "student_url": col("studenturl"),
        "folder_url": col("folderurl"), "extra_url": col("extraurl"),
        "second_deck": col("2nddeck", "seconddeck"),
        "teacher_deck": col("teacherdeck"), "student_deck": col("studentdeck"),
        "folder": col("folder"), "activity": col(starts="activity"),
        "turnin": col("turnitin"), "turnin_url": col("turninurl"),
    }
    plans = {}
    for c, h in headers.items():
        match = re.fullmatch(r"(mon|tue|wed|thu|fri)[a-z]*(plan|note)", h)
        if match:
            plans.setdefault(match.group(1).title(), {})[match.group(2)] = c
    rows = [(r, text(cells.get((r, columns["week"]))).strip())
            for r in range(header_row + 1, LAST_ROW + 1)]
    return {"columns": columns, "plans": {d: plans[d] for d in DAYS if "plan" in plans.get(d, {})},
            "rows": [(r, label) for r, label in rows if label]}, None


def field_columns(layout):
    """{field: column} for a lesson's text cells: 'topic', 'todo', 'prep',
    'plan:Mon', 'note:Mon', … — only those this tab has a column for."""
    cols = layout["columns"]
    out = {key: cols[key] for key in ("topic", "todo", "prep") if cols.get(key)}
    for day, c in layout["plans"].items():
        out["plan:" + day] = c["plan"]
        if c.get("note"):
            out["note:" + day] = c["note"]
    return out


def plain(cell):
    """Whether a cell is plain text that can be replaced without losing
    anything: no formula and no link (a URL typed as text is fine)."""
    if not cell:
        return True
    if (cell.get("userEnteredValue") or {}).get("formulaValue") or cell.get("hyperlink"):
        return False
    return not any(((run.get("format") or {}).get("link") or {}).get("uri")
                   for run in cell.get("textFormatRuns") or [])


def build_lesson(cells, layout, row, edit_url):
    cols = layout["columns"]

    def cell(key):
        return cells.get((row, cols[key])) if cols.get(key) else None

    plans = [{"day": day, "plan": text(cells.get((row, c["plan"]))).strip(),
              "note": text(cells.get((row, c["note"]))).strip() if c.get("note") else ""}
             for day, c in layout["plans"].items()]
    materials = []
    for hidden, visible, label in (("teacher_url", "teacher_deck", "Teacher deck"),
                                   ("student_url", "student_deck", "Student copy"),
                                   ("folder_url", "folder", "Lesson folder"),
                                   ("second_deck", None, "2nd deck"),
                                   ("extra_url", None, "Extra material")):
        found = links(cell(hidden)) or (links(cell(visible)) if visible else [])
        if found:
            materials.append({"label": label, "url": found[0][0]})
    for url, label in links(cell("activity")):
        if url not in {m["url"] for m in materials}:
            materials.append({"label": label or "Activity", "url": url})
    # Which text cells the dashboard may save: True, or False for a cell with a link or formula.
    editable = {key: plain(cells.get((row, c))) for key, c in field_columns(layout).items()}
    return {"row": row, "week": text(cell("week")).strip(), "topic": text(cell("topic")).strip(),
            "dates": text(cell("dates")).strip(), "plans": plans, "editable": editable,
            "todo": text(cell("todo")).strip(), "prep": text(cell("prep")).strip(),
            "materials": materials, "forms": lesson_forms(cell("turnin"), cell("turnin_url")),
            "edit_url": edit_url}


def lesson_forms(visible, hidden):
    """The lesson's Forms: the named 'Turn it in' links, or the hidden
    _turnInURL when none of the named links can be read."""
    def forms_in(cell):
        found = {}
        for url, label in links(cell):
            form = form_link(url, label)
            if form:
                ident = form["form_id"] or url
                if ident in found:
                    found[ident]["label"] = found[ident]["label"] or label
                else:
                    found[ident] = form
        return list(found.values())

    named, raw = forms_in(visible), forms_in(hidden)
    if any(f["form_id"] for f in named) or not any(f["form_id"] for f in raw):
        forms = named or raw
    else:
        forms = [f for f in raw if f["form_id"]]
        if len(named) == 1 and len(forms) == 1:
            forms[0]["label"] = forms[0]["label"] or named[0]["label"]
    for form in forms:
        form["label"] = form["label"] or "Turn in"
    return forms


class Agenda:
    def __init__(self, spreadsheet_id, title, tabs, grids):
        self.spreadsheet_id, self.title, self.problems = spreadsheet_id, title, []
        weeks_tab = find_tab(tabs, "Weeks")
        self.weeks = []
        if weeks_tab:
            self.weeks, problem = parse_weeks(grids.get(weeks_tab, {}))
            if problem:
                self.problems.append(problem)
        else:
            self.problems.append("The Agenda has no 'Weeks' tab, so dates can't be matched to weeks.")
        self.classes = {}
        for key, name in CLASSES:
            tab = find_tab(tabs, name)
            entry = {"key": key, "name": name, "tab": tab, "layout": None, "problem": None, "cells": {}}
            if not tab:
                entry["problem"] = "No '%s' tab was found in the Agenda." % name
            else:
                entry["cells"] = grids.get(tab, {})
                entry["layout"], entry["problem"] = parse_class_tab(entry["cells"])
                entry["sheet_id"] = tabs[tab]
            self.classes[key] = entry

    def week_for(self, day):
        for week in self.weeks:
            start = date.fromisoformat(week["monday"])
            if start <= day < start + timedelta(days=7):
                return week
        return None

    def lessons(self, class_key, week_label):
        entry = self.classes[class_key]
        if not entry["layout"]:
            return []
        edit = "https://docs.google.com/spreadsheets/d/%s/edit#gid=%s&range=A%%d" % (
            self.spreadsheet_id, entry.get("sheet_id", 0))
        return [build_lesson(entry["cells"], entry["layout"], row, edit % row)
                for row, label in entry["layout"]["rows"] if norm(label) == norm(week_label)]

    def day(self, day):
        week = self.week_for(day)
        weekday = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[day.weekday()]
        classes = []
        for key, entry in self.classes.items():
            item = {"key": key, "name": entry["name"], "lessons": [], "problem": entry["problem"]}
            if week and entry["layout"]:
                item["lessons"] = self.lessons(key, week["label"])
                if not item["lessons"]:
                    item["problem"] = "No row labelled '%s' in the %s tab." % (week["label"], entry["tab"])
                for lesson in item["lessons"]:
                    today = next((p for p in lesson["plans"] if p["day"] == weekday), None)
                    lesson["today"] = today
            classes.append(item)
        return {"date": day.isoformat(), "weekday": weekday, "week": week, "classes": classes,
                "problems": self.problems + ([] if week or not self.weeks else
                                             ["%s isn't in any week listed on the Weeks tab." % day.isoformat()])}

    def week_options(self, class_key):
        """Every week this class has a row for, with its topic, for Page 02."""
        entry = self.classes[class_key]
        if not entry["layout"]:
            return []
        dated = {norm(w["label"]): w for w in self.weeks}
        options, seen = [], set()
        for row, label in entry["layout"]["rows"]:
            if norm(label) in seen:
                continue
            seen.add(norm(label))
            lesson = build_lesson(entry["cells"], entry["layout"], row, "")
            week = dated.get(norm(label), {})
            options.append({"label": label, "topic": lesson["topic"], "monday": week.get("monday"),
                            "forms": len(lesson["forms"])})
        return options


def load(token, spreadsheet_id):
    title, tabs = google.sheet_tabs(token, spreadsheet_id)
    wanted = [t for t in [find_tab(tabs, "Weeks")] + [find_tab(tabs, name) for _, name in CLASSES] if t]
    ranges = ["'%s'!A1:Z%d" % (t.replace("'", "''"), LAST_ROW) for t in wanted]
    grids = google.sheet_grid(token, spreadsheet_id, ranges) if ranges else {}
    return Agenda(spreadsheet_id, title, tabs, grids)
