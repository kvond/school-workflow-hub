"""Lesson plans from the Agenda's weekly class tabs.

Columns are found by their header text, rows by their week label, and tabs by
name. Whatever can be read is shown: a missing tab, header or cell becomes a
note on that class, never a failed page. The plain-text cells of a lesson
(topic, each day's plan and note, to-do, lab / prep) can be edited from the
dashboard, and so can its deck, activity and Turn-in Form links; see
agenda_edit.py.
"""
import re
from datetime import date, datetime, timedelta
from urllib.parse import quote

from . import google, lesson_prep

CLASSES = (("biology", "Biology"), ("forensics", "Forensics"), ("anatomy", "A&P"))
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")
LAST_ROW = 80
# How many Agenda weeks back "Last week's lessons" may look past breaks.
PREVIOUS_LOOK_BACK = 3
WORKSHEET_HEADERS = ("course", "wk", "topic", "document", "type", "link", "fileid")
WORKSHEET_MAX_ROWS, WORKSHEET_MAX_COLUMNS = 1000, 26
URL = re.compile(r"https://[^\s\"'<>]+")
FORM = re.compile(r"https://docs\.google\.com/forms/d/(e/)?([A-Za-z0-9_-]{20,})")


def norm(value):
    """'🌿 Biology' -> 'biology', 'Mon — plan' -> 'monplan', 'A&P' -> 'a&p'."""
    return re.sub(r"[^a-z0-9&]+", "", str(value or "").lower())


def lesson_kind(topic):
    """'lesson', 'free' (nothing planned, or an [EMPTY …] placeholder) or
    'closed' (a [CLOSED …], [SPRING BREAK …] or "No class" week)."""
    topic = str(topic or "").strip()
    if not topic or norm(topic).startswith("empty"):
        return "free"
    if topic.startswith("[") or norm(topic).startswith("noclass"):
        return "closed"
    return "lesson"


def first_link(cell, forms=False):
    """The first link in a cell (only a Google Form when forms=True), or ''."""
    for url, label in links(cell):
        if not forms or form_link(url, label):
            return url
    return ""


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
    # Calendar annotations are not lesson identities. In particular, the
    # winter-break handoff can point at the next Monday without being that
    # teaching week. Keep its explicit takeover date only as an interval end.
    takeover = col.get("takesoveron")
    transitions = {day for row in range(r + 1, LAST_ROW + 1)
                   if takeover and (day := parse_date(text(cells.get((row, takeover)))))}
    weeks = []
    for row in range(r + 1, LAST_ROW + 1):
        shown = text(cells.get((row, col["week"]))).strip()
        label = re.fullmatch(r"((?:Wk|Week)\s*\d{1,2}[a-z]?)(?:\s+[—–-]\s+.+)?", shown, re.I)
        monday = parse_date(text(cells.get((row, col["monday"]))))
        if label and monday:
            week = {"label": label.group(1), "monday": monday.isoformat(),
                    "closures": text(cells.get((row, closures))).strip() if closures else ""}
            ends = [day for day in transitions if monday < day < monday + timedelta(days=7)]
            if ends:
                week["end"] = min(ends).isoformat()
            weeks.append(week)
    return weeks, None


def parse_class_tab(cells):
    header_row = next((r for r in range(1, 8) if any(
        norm(text(cells.get((r, c)))) == "topic" for c in range(1, 27))), None)
    if not header_row:
        return None, "No header row with 'Topic' was found."
    # A column with no name in the header row may be named in a row above it
    # (the Forensics tab names its "2nd turn it in" column in row 2).
    headers = {}
    for c in range(1, 27):
        names = [norm(text(cells.get((r, c)))) for r in range(header_row, 0, -1)]
        headers[c] = next((n for n in names if n), "")

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
        "turnin2": col("2ndturnitin", "secondturnitin", "turnitin2"),
    }
    # Some existing tabs reserve a hidden URL slot between _teacherURL and
    # _folderURL for the visible 2nd-deck control, but omitted its header.
    # Recognize only this exact layout; arbitrary unnamed links keep no role.
    second_url = col("2nddeckurl", "seconddeckurl", "deck2url", "teacher2url")
    teacher, folder = columns["teacher_url"], columns["folder_url"]
    if (not second_url and columns["second_deck"] and teacher and folder == teacher + 2
            and not headers[teacher + 1]):
        second_url = teacher + 1
    if second_url:
        columns["second_deck_display"] = columns["second_deck"]
        columns["second_deck"] = second_url
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


def manual_material_links(cell):
    """A manually entered material link, rather than a derived link control.

    A pasted URL or explicitly linked label can replace an older hidden URL.
    Formula controls that refer to another cell still use that backing cell;
    a literal HYPERLINK URL is itself an explicit material choice.
    """
    cell = cell or {}
    formula = (cell.get("userEnteredValue") or {}).get("formulaValue") or ""
    if formula:
        literal = re.fullmatch(
            r'\s*=\s*HYPERLINK\(\s*"(https://[^"]+)"\s*(?:[,;]\s*"([^"]*)"\s*)?\)\s*',
            formula, re.I)
        return [(literal.group(1), literal.group(2) or "")] if literal else []
    shown = text(cell).strip()
    # A replaced plain URL can retain Sheets' older whole-cell link format.
    if URL.fullmatch(shown):
        return [(shown, "")]
    return links(cell)


def material_links(hidden, visible=None):
    """Resolve the teacher's explicit visible edit before a hidden fallback."""
    return manual_material_links(visible) or links(hidden) or links(visible)


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
                                   ("second_deck", "second_deck_display", "2nd deck"),
                                   ("extra_url", None, "Extra material")):
        found = material_links(cell(hidden), cell(visible) if visible else None)
        if found:
            materials.append({"label": label, "url": found[0][0]})
    for url, label in links(cell("activity")):
        if url not in {m["url"] for m in materials}:
            materials.append({"label": label or "Activity", "url": url})
    # Which text cells the dashboard may save: True, or False for a cell with a link or formula.
    editable = {key: plain(cells.get((row, c))) for key, c in field_columns(layout).items()}
    # The links the dashboard can change (see agenda_edit.LINKS), as the Agenda has them now.
    teacher = material_links(cell("teacher_url"), cell("teacher_deck"))
    second = material_links(cell("second_deck"), cell("second_deck_display"))
    current = {"deck": teacher[0][0] if teacher else "",
               "deck2": second[0][0] if second else "",
               "activity": first_link(cell("extra_url")) or first_link(cell("activity")),
               "turnin": first_link(cell("turnin"), forms=True) or first_link(cell("turnin_url"), forms=True),
               "turnin2": first_link(cell("turnin2"), forms=True)}
    link_fields = {key: url for key, url in current.items()
                   if cols.get({"deck": "teacher_url", "deck2": "second_deck", "activity": "extra_url",
                                "turnin": "turnin_url", "turnin2": "turnin2"}[key])}
    return {"row": row, "week": text(cell("week")).strip(), "topic": text(cell("topic")).strip(),
            "dates": text(cell("dates")).strip(), "plans": plans, "editable": editable,
            "todo": text(cell("todo")).strip(), "prep": text(cell("prep")).strip(),
            "materials": materials, "forms": lesson_forms(cell("turnin"), cell("turnin_url"), cell("turnin2")),
            "links": link_fields,
            # Each Turn-in Form's name, as its link label shows it (the page can rename it).
            "link_labels": {key: next((label for url, label in links(cell(column)) if form_link(url, label)), "")
                            for key, column in (("turnin", "turnin"), ("turnin2", "turnin2")) if key in link_fields},
            "edit_url": edit_url}


def lesson_forms(visible, hidden, second=None):
    """The lesson's Forms: the named 'Turn it in' links, or the hidden
    _turnInURL when none of the named links can be read, plus any Form in the
    '2nd turn it in' column."""
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
    for form in forms_in(second):
        if not any((f["form_id"] or f["url"]) == (form["form_id"] or form["url"]) for f in forms):
            forms.append(form)
    for form in forms:
        form["label"] = form["label"] or "Turn in"
    return forms


class Agenda:
    def __init__(self, spreadsheet_id, title, tabs, grids):
        self.tabs = dict(tabs)
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
            end = date.fromisoformat(week["end"]) if week.get("end") else start + timedelta(days=7)
            if start <= day < end:
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

    def prep_notes(self, class_key, lesson):
        """Display-only copy: canonical lesson dictionaries bind saved evidence.

        Do not add prep metadata in build_lesson/lessons: their exact dictionaries
        are hashed by teacher-material, historical-intake and saved-score readers.
        """
        entry = self.classes[class_key]
        column = entry["layout"]["columns"].get("prep") if entry["layout"] else None
        prep_links = links(entry["cells"].get((lesson["row"], column))) if column else []
        display = {**lesson, "prep_links": [{"label": label, "url": url} for url, label in prep_links]}
        return lesson_prep.project(display, biology=class_key == "biology")

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
                    lesson["prep_notes"] = self.prep_notes(key, lesson)
                item["previous"] = self.previous_lessons(key, week)
                item["next_prep"] = self.next_prep(key, week)
            classes.append(item)
        return {"date": day.isoformat(), "weekday": weekday, "week": week, "classes": classes,
                "problems": self.problems + ([] if week or not self.weeks else
                                             ["%s isn't in any week listed on the Weeks tab." % day.isoformat()])}

    def next_prep(self, class_key, week):
        """Exact following calendar week; never skip a break or move a lesson.

        The same already-read grid supplies both views. No extra provider call,
        cache, source repair, duplicated tracker or readiness execution occurs.
        """
        monday = (date.fromisoformat(week["monday"]) + timedelta(days=7)).isoformat()
        matches = [w for w in self.weeks if w["monday"] == monday]
        if len(matches) != 1:
            return {"monday": monday, "lessons": [],
                    "problem": "Next week's Agenda date is unavailable or ambiguous; prep timing is unverified."}
        future = matches[0]
        if sum(norm(w["label"]) == norm(future["label"]) for w in self.weeks) != 1:
            return {"monday": monday, "week": future["label"], "lessons": [],
                    "problem": "Next week's Agenda label has more than one date; prep timing is unverified."}
        lessons = self.lessons(class_key, future["label"])
        if not lessons:
            return {"monday": monday, "week": future["label"], "lessons": [],
                    "problem": "No lesson row was found for next week's Agenda label; prep is unverified."}
        return {"monday": monday, "week": future["label"], "closures": future["closures"],
                "lessons": [{k: lesson[k] for k in ("row", "week", "topic", "dates", "edit_url")}
                            | {"kind": lesson_kind(lesson["topic"]), "prep_notes": prep}
                            for lesson in lessons if (prep := self.prep_notes(class_key, lesson))], "problem": None}

    def previous_lessons(self, class_key, week, look_back=PREVIOUS_LOOK_BACK):
        """The lessons this class last taught before `week`, so an unfinished
        lesson's links are one click away. Weeks with nothing planned or no
        class (a break) are skipped, at most `look_back` Agenda weeks back.
        Read only: links and topics as the Agenda has them, nothing inferred
        about whether the lesson was finished."""
        earlier = sorted((w for w in self.weeks if w["monday"] < week["monday"]),
                         key=lambda w: w["monday"], reverse=True)
        for prior in earlier[:look_back]:
            taught = [lesson for lesson in self.lessons(class_key, prior["label"])
                      if lesson_kind(lesson["topic"]) == "lesson"]
            if taught:
                return {"week": prior["label"], "monday": prior["monday"],
                        "lessons": [{"row": l["row"], "topic": l["topic"], "materials": l["materials"],
                                     "forms": [{"label": f["label"], "url": f["url"]} for f in l["forms"]],
                                     "edit_url": l["edit_url"]} for l in taught]}
        return None

    def week_options(self, class_key):
        """Every week this class has a row for, with its topic, for Page 02."""
        entry = self.classes[class_key]
        if not entry["layout"]:
            return []
        dated = {norm(w["label"]): w for w in self.weeks}
        options, grouped = [], {}
        for row, label in entry["layout"]["rows"]:
            lesson = build_lesson(entry["cells"], entry["layout"], row, "")
            key = norm(label)
            if key not in grouped:
                week = dated.get(key, {})
                item = {"label": label, "topic": "", "monday": week.get("monday"), "forms": 0}
                grouped[key] = (item, [], set())
                options.append(item)
            item, topics, forms = grouped[key]
            if lesson["topic"] and lesson["topic"] not in topics:
                topics.append(lesson["topic"])
            forms.update(f["form_id"] or f["url"] for f in lesson["forms"])
            item.update(topic=" / ".join(topics), forms=len(forms))
        return options


def load(token, spreadsheet_id):
    title, tabs = google.sheet_tabs(token, spreadsheet_id)
    wanted = [t for t in [find_tab(tabs, "Weeks")] + [find_tab(tabs, name) for _, name in CLASSES] if t]
    ranges = ["'%s'!A1:Z%d" % (t.replace("'", "''"), LAST_ROW) for t in wanted]
    grids = google.sheet_grid(token, spreadsheet_id, ranges) if ranges else {}
    return Agenda(spreadsheet_id, title, tabs, grids)


def read_worksheets(token, agenda, *, can_read=lambda: True):
    """Lazy, bounded index read for Week ahead only; never part of load/day.

    Grid dimensions distinguish complete coverage from a capped read. The
    historical Course/Wk columns locate index rows, not current assignments.
    Nothing is cached on the possibly long-lived Agenda instance.
    """
    base_url = "https://docs.google.com/spreadsheets/d/%s/edit" % agenda.spreadsheet_id
    result = {"status": "unavailable", "reason": "", "source_url": base_url, "rows": [],
              "tab": None, "header_row": None, "columns": {}}
    matches = [t for t in agenda.tabs if norm(t) == "worksheets"]
    if len(matches) != 1:
        result["reason"] = "Worksheets tab is missing or its name is ambiguous; index coverage is unverified."
        return result
    title = matches[0]
    result.update(tab=title, source_url=base_url + "#gid=%s" % agenda.tabs[title])
    try:
        if not can_read():
            return result | {"status": "pending", "reason": "The run budget ended before the Worksheets index read."}
        data = google.get_json("https://sheets.googleapis.com/v4/spreadsheets/" + quote(agenda.spreadsheet_id), token,
            {"fields": "sheets(properties(sheetId,title,gridProperties(rowCount,columnCount)))"})
        found = [s.get("properties", {}) for s in data.get("sheets", [])
                 if s.get("properties", {}).get("title") == title]
        props = found[0] if len(found) == 1 else {}
        grid = props.get("gridProperties") or {}
        rows, cols = grid.get("rowCount"), grid.get("columnCount")
        if (props.get("sheetId") != agenda.tabs[title] or type(rows) is not int or type(cols) is not int
                or rows < 1 or cols < 1):
            result["reason"] = "Worksheets tab identity or grid dimensions could not be verified."
            return result
        height, width = min(rows, WORKSHEET_MAX_ROWS), min(cols, WORKSHEET_MAX_COLUMNS)
        result.update(grid_rows=rows, grid_columns=cols, rows_read=height, columns_read=width)
        if not can_read():
            return result | {"status": "pending", "reason": "The run budget ended before the Worksheets grid read."}
        grids = google.sheet_grid(token, agenda.spreadsheet_id,
            ["'%s'!A1:%s%d" % (title.replace("'", "''"), chr(64 + width), height)])
        if title not in grids:
            result["reason"] = "The Worksheets grid was not returned; index coverage is unverified."
            return result
    except google.GoogleError as error:
        return result | {"reason": "The Worksheets index could not be read: " + str(error), "http_status": error.status}
    return parse_worksheets(grids[title], result)


def parse_worksheets(cells, result):
    """Parse the seven named columns, keeping source cells and row addresses."""
    width, height = result["columns_read"], result["rows_read"]
    required = set(WORKSHEET_HEADERS)
    candidates = []
    for row in range(1, min(height, 7) + 1):
        names = [norm(text(cells.get((row, c)))) for c in range(1, width + 1)]
        if required <= set(names):
            candidates.append((row, names))
    if len(candidates) != 1 or any(candidates[0][1].count(h) != 1 for h in required):
        return result | {"status": "malformed", "reason": "Worksheets needs one unambiguous header row with "
                        "Course, Wk, Topic, Document, Type, Link and _fileID; index coverage is unverified."}
    header, names = candidates[0]
    columns = {h: names.index(h) + 1 for h in WORKSHEET_HEADERS}
    rows = []
    for row in range(header + 1, height + 1):
        source = {h: cells.get((row, c), {}) for h, c in columns.items()}
        if not any(text(c).strip() or links(c) for c in source.values()):
            continue
        values = {h: text(c).strip() for h, c in source.items()}
        # Keep unsupported addresses as evidence too. They are never declared
        # missing just because the Drive reader cannot understand their URL.
        link_cell = source["link"]
        urls = [u for u, _ in links(link_cell)]
        urls += [link_cell.get("hyperlink", "")]
        urls += [((r.get("format") or {}).get("link") or {}).get("uri", "")
                 for r in link_cell.get("textFormatRuns", [])]
        formula = (link_cell.get("userEnteredValue") or {}).get("formulaValue", "")
        urls += re.findall(r'HYPERLINK\(\s*"([^"]+)"', formula, re.I)
        urls += re.findall(r'https?://[^\s"<>]+', values["link"])
        rows.append(values | {"row": row, "urls": list(dict.fromkeys(u for u in urls if u)),
            "source_cells": source,
            "source_url": result["source_url"] + "&range=A%d:%s%d" % (row, chr(64 + width), row),
            "link_cell": "%s%d" % (chr(64 + columns["link"]), row),
            "id_cell": "%s%d" % (chr(64 + columns["fileid"]), row)})
    truncated = result["grid_rows"] > height or result["grid_columns"] > width
    return result | {"status": "truncated" if truncated else "complete", "header_row": header, "columns": columns,
        "rows": rows, "reason": "The bounded Worksheets read does not cover the whole grid." if truncated else ""}
