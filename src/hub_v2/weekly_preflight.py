"""Week-ahead lesson preflight: is each lesson of the next 1-4 weeks ready to teach?

Deterministic checks only. Nothing here grades, reads submissions, calls a
model or changes a source; the one write is repair_link, which the teacher
asks for by fingerprint.

- Calendar. The Weeks tab's closures cell is the calendar for its week: which
  Friday is an A or B Friday and which dates are closed. A4 Biology and A2/A3
  Forensics meet on A days (Mon, Wed, A Fridays), B3 A&P on B days (Tue, Thu,
  B Fridays). Dates stay plain dates in America/New_York; nothing is shifted.
- Plans. Each lesson row's day plans are checked against that calendar: "no
  class" on a real meeting day, plan content on a day the section doesn't
  meet, a dated claim ("moved from Fri 10/23 - in-service") the calendar
  contradicts, VERIFY DAY / Check the day / NEEDS CONTENT, an empty plan on a
  meeting day. Prep cells ("PREP by Thu 10/22 -> LAB ...") are deadlines,
  never meeting claims.
- Agenda links. A visible label cell that is plain text while its hidden URL
  cell has a URL can't be clicked. Its repair plan restores the formula the
  same column uses in its other rows, or, when the column shares no formula,
  links the label the cell already shows to its hidden URL (see repair_link).
  A visible link that disagrees with its hidden URL is reported, never
  overwritten.
- Sources, as the Hub's own account sees them (not proof of student access):
  decks, the /copy student template (the same file), the lesson folder (a
  complete listing, or held), Forms with their published responder IDs, and
  extra material. A Form shared by several rows is not an error, and no
  submission count is read, so an untaught lesson never looks like missing work.
- Deck content: the existing completion readiness (audit._release_deck), the
  teacher-reference blocks TO PRINT, BUY AHEAD, PREPARE and PUT OUT FOR
  STUDENTS, the deck's web links, and authoring notes ("[DRAFT — …]") left on
  slides students see.
- Print and supply lists: quantities from the roster, print lines matched to
  folder files by name (or, with no file, to a web page the line or the deck's
  link list names), page counts; BUY / PULL / on order, never "in hand".

Reviewed parts (a meeting's 90-minute flow, a VT verdict, a print decision, a
visual check) are records a person saves, bound to the source revisions they
saw; when a source changes the record shows "needs re-review".

Each content check is cached in the Hub's private state by (check, source,
revision, rules) and saved as soon as it finishes, so an interrupted run
resumes where it stopped. A run that did not finish is "incomplete", never
Ready, and a lesson is Ready only when every required check is verified.
"""
import math
import re
import time
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from urllib.parse import parse_qs, urlsplit

from . import agenda as agenda_module, audit, completion, google, roster, work
from .agenda import first_link, lesson_kind, links, norm, plain, text
from .agenda_edit import REF, _cell_range, _linked_cell, _write_error, column_letter
from school_workflow_hub.adapters.firestore_state import configured_store
from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable, bounded_json, key
from school_workflow_hub.core.runs import content_hash

SCHEMA = "hub-weekly-preflight/v1"
RULES = "weekly-preflight-rules/5"
MAX_WEEKS = 4
BUDGET_SECONDS = 200
LETTER = {"biology": "A", "forensics": "A", "anatomy": "B"}
FIXED_DAYS = {"A": ("Mon", "Wed"), "B": ("Tue", "Thu")}
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")
ACCESS = "Hub service-account token only; student access is not verified"
PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PRINTABLE = (PDF, google.DOC, DOCX)
DECKS = (completion.SLIDES, google.PPTX)
NODE_FIELDS = ("id,name,mimeType,trashed,parents,modifiedTime,version,md5Checksum,size,"
               "shortcutDetails(targetId,targetMimeType)")
EXCEPTION_STATUSES = ("needs_decision", "inaccessible", "repairable", "repaired_and_verified")
LESSON_ORDER = ("inaccessible", "incomplete", "needs_decision", "repaired_and_verified", "ready")


class Held(ValueError):
    """A request the teacher can put right; the message says how."""


def now():
    return datetime.now(timezone.utc).isoformat()


def monday_of(value):
    """The Monday of a plain date ('2026-10-21' -> 2026-10-19); never a timezone shift."""
    if isinstance(value, str):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise Held("Choose a start date as YYYY-MM-DD.")
        try:
            value = date.fromisoformat(value)
        except ValueError:
            raise Held("Choose a real start date.") from None
    return value - timedelta(days=value.weekday())


def week_count(value):
    try:
        weeks = int(value)
    except (TypeError, ValueError):
        raise Held("Choose 1 to 4 weeks.") from None
    if isinstance(value, bool) or not 1 <= weeks <= MAX_WEEKS:
        raise Held("Choose 1 to 4 weeks.")
    return weeks


# ---------- calendar ----------

DATE_REF = re.compile(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\.?\s+(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?\b", re.I)
CLOSED = re.compile(r"in[\s-]*service|schools?\s+(?:are\s+)?closed|no\s+school|holiday|\bclosed\b(?![-\w])", re.I)
# A plan cell's own words: "closed-toe shoes" or a holiday party is not a closure.
PLAN_CLOSED = re.compile(r"in[\s-]*service|schools?\s+(?:are\s+|is\s+)?closed|no\s+school", re.I)
OFF = re.compile(r"\bOFF\b")
FRIDAY = re.compile(r"\b([AB])[\s-]*(?:Friday|FRIDAY|friday|Fri)\b")


def _near(monday, month, day, year=None):
    """The date with this month/day nearest the week's Monday (or in the year given)."""
    if year:
        year = int(year) + (2000 if len(year) == 2 else 0)
        try:
            return date(year, month, day)
        except ValueError:
            return None
    best = None
    for candidate_year in (monday.year - 1, monday.year, monday.year + 1):
        try:
            candidate = date(candidate_year, month, day)
        except ValueError:
            continue
        if best is None or abs((candidate - monday).days) < abs((best - monday).days):
            best = candidate
    return best


def _dated(text, monday):
    """[(date, weekday matches, clause)]: each 'Fri 10/23' with the words that
    belong to it (up to the next date, or its parentheses)."""
    refs = list(DATE_REF.finditer(text))
    out = []
    for i, ref in enumerate(refs):
        when = _near(monday, int(ref.group(2)), int(ref.group(3)), ref.group(4))
        if not when:
            continue
        opened = text.rfind("(", 0, ref.start())
        if opened > text.rfind(")", 0, ref.start()) and text.find(")", ref.end()) != -1:
            start, end = opened, text.find(")", ref.end()) + 1
        else:
            start = ref.start()
            end = refs[i + 1].start() if i + 1 < len(refs) else len(text)
            stop = re.search(r"[\n;]", text[ref.end():end])
            end = ref.end() + stop.start() if stop else end
            # "Mon 10/12 & Tue 10/13 OFF": a bare joiner shares the next clause.
            if i + 1 < len(refs) and re.fullmatch(r"[\s,&/+]*(?:and)?[\s,&/+]*", text[ref.end():end]):
                later = refs[i + 1].end()
                follow = re.search(r"[\n;]", text[later:])
                end = later + follow.start() if follow else len(text)
        out.append((when, when.strftime("%a").lower() == ref.group(1)[:3].lower(), text[start:end], (start, end)))
    return out


def week_calendar(week):
    """{"monday", "fridays": {date: "A"|"B"}, "closed": {date: line}, "problems"} from
    one Weeks-tab row. The closures cell is the authority for its own week."""
    monday = date.fromisoformat(week["monday"])
    fridays, closed, problems = {}, {}, []
    for line in str(week.get("closures") or "").splitlines():
        for when, weekday_ok, clause, _ in _dated(line, monday):
            if not weekday_ok:
                problems.append("The Weeks tab names the wrong weekday for %s: “%s”." % (when.isoformat(), line.strip()))
                continue
            friday = FRIDAY.search(clause)
            if friday and when.weekday() == 4:
                prior = fridays.get(when.isoformat())
                if prior and prior != friday.group(1):
                    problems.append("The Weeks tab calls %s both an A and a B Friday." % when.isoformat())
                fridays[when.isoformat()] = friday.group(1)
            if CLOSED.search(clause) or OFF.search(clause):
                closed[when.isoformat()] = " ".join(line.split())
    return {"label": week["label"], "monday": monday.isoformat(), "closures": week.get("closures") or "",
            "fridays": fridays, "closed": closed, "problems": problems}


def class_days(calendar, class_key):
    """Each weekday of the week for one class: meets True/False, or None when
    the Weeks tab doesn't say which Friday it is."""
    letter, monday = LETTER[class_key], date.fromisoformat(calendar["monday"])
    out = []
    for offset, name in enumerate(DAYS):
        day = (monday + timedelta(days=offset)).isoformat()
        if name == "Fri":
            kind = calendar["fridays"].get(day)
            meets = None if kind is None else kind == letter
            reason = "the Weeks tab doesn't say whether this is an A or B Friday" if kind is None else "%s Friday" % kind
        else:
            meets = name in FIXED_DAYS[letter]
            reason = "%s day" % letter if meets else "not a %s day" % letter
        if day in calendar["closed"]:
            meets, reason = False, "closed (%s)" % calendar["closed"][day]
        out.append({"day": name, "date": day, "meets": meets, "reason": reason})
    return out


def _calendar_lookup(calendars, day):
    monday = (day - timedelta(days=day.weekday())).isoformat()
    return calendars.get(monday)


# ---------- plans against the calendar ----------

ALIASES = {"biology": {"bio", "biology"}, "forensics": {"forensics", "foren", "forensic"},
           "anatomy": {"a&p", "ap", "anatomy", "aandp"}}
NO_MEET = re.compile(r"(?:\b([A-Za-z&]+)\s+)?(?:does\s*n[o'’]?t|doesn[’']t|won[’']t|will\s+not|do\s+not|don[’']t)\s+meet\b"
                     r"|\bno\s+class\b|\bnot\s+meeting\b", re.I)
VERIFY = re.compile(r"verify\s+day|check\s+the\s+day|needs\s+content", re.I)
EMPTY = re.compile(r"[\s—–\-·.?!:]*")


def _blank(value):
    return EMPTY.fullmatch(str(value or "")) is not None


def plan_reading(value, class_key, day_name, monday):
    """What one plan cell claims: not meeting on its own day, dated claims about
    other days, a VERIFY / NEEDS CONTENT marker, and whether it has content."""
    value = str(value or "")
    dated = _dated(value, monday)
    spans = []
    for start, end in sorted({d[3] for d in dated}):
        if spans and start < spans[-1][1]:
            spans[-1] = (spans[-1][0], max(end, spans[-1][1]))
        else:
            spans.append((start, end))
    residual = value
    for start, end in reversed(spans):
        residual = residual[:start] + " " + residual[end:]
    not_meeting = False
    for match in NO_MEET.finditer(residual):
        who = norm(match.group(1) or "")
        named = {k for k, names in ALIASES.items() if who in names}
        if not named or class_key in named:
            not_meeting = True
    if PLAN_CLOSED.search(residual) or OFF.search(residual):
        not_meeting = True
    friday = FRIDAY.search(residual)
    if day_name == "Fri" and friday and friday.group(1) != LETTER[class_key]:
        not_meeting = True
    content = NO_MEET.sub(" ", residual)
    content = FRIDAY.sub(" ", OFF.sub(" ", PLAN_CLOSED.sub(" ", content)))
    content = re.sub(r"[⚠️❗✔→←·()\[\]]+", " ", VERIFY.sub(" ", content))
    return {"text": value.strip(), "blank": _blank(value), "not_meeting": not_meeting,
            "content": not _blank(re.sub(r"[^\w]+", " ", content)), "verify": bool(VERIFY.search(value)),
            "dated": [{"date": when.isoformat(), "weekday_ok": ok, "clause": clause.strip(),
                       "closed": bool(PLAN_CLOSED.search(clause) or OFF.search(clause)),
                       "friday": (FRIDAY.search(clause) or [None, None])[1]}
                      for when, ok, clause, _ in dated]}


# ---------- Agenda link integrity ----------

# (visible column, hidden URL column, role) as agenda.parse_class_tab names them.
LINK_PAIRS = (("student_deck", "student_url", "Student copy"), ("teacher_deck", "teacher_url", "Teacher deck"),
              ("folder", "folder_url", "Lesson folder"), ("activity", "extra_url", "Extra material"),
              ("second_deck_display", "second_deck", "2nd deck"), ("turnin", "turnin_url", "Turn-in Form"))
SAFE = re.compile(r'=IF\(\s*\$?([A-Z]{1,3})\{row\}\s*=\s*""\s*[,;]\s*"[^"{}]{0,40}"\s*[,;]\s*'
                  r'HYPERLINK\(\s*\$?([A-Z]{1,3})\{row\}\s*[,;]\s*"([^"{}]{1,80})"\s*\)\s*\)')
FORMULA_TARGET = re.compile(r'HYPERLINK\(\s*\$?([A-Z]{1,3})\$?(\d+)\s*[,;)]', re.I)
FILE_ID = re.compile(r"/(?:presentation|document|spreadsheets|file|forms)/d/([A-Za-z0-9_-]{20,})"
                     r"|/folders/([A-Za-z0-9_-]{10,})|[?&]id=([A-Za-z0-9_-]{20,})")
SECOND_DECK_URL = re.compile(r"https://docs\.google\.com/presentation/d/([A-Za-z0-9_-]{20,})"
                             r"(?:/(?:edit|view|preview|copy))?(?:[?#][^\s]*)?")
SECOND_DECK_PLACEHOLDERS = ("", "—", "–", "-")


def file_id(url):
    match = FILE_ID.search(str(url or ""))
    return next((g for g in match.groups() if g), "") if match else ""


def _formula(cell):
    return ((cell or {}).get("userEnteredValue") or {}).get("formulaValue") or ""


def row_template(formula, row):
    """The formula with its own-row references as {row}, or None when it reads
    another row, a fixed row or another tab (then it isn't a row pattern)."""
    if not formula or "{" in formula or "}" in formula or "!" in formula:
        return None
    pieces = formula.split('"')
    other = False

    def swap(match):
        nonlocal other
        if match.group(3) or int(match.group(4)) != row:
            other = True
        return match.group(1) + match.group(2) + "{row}"
    pieces = [REF.sub(swap, piece) if i % 2 == 0 else piece for i, piece in enumerate(pieces)]
    return None if other else '"'.join(pieces)


def column_pattern(cells, layout, visible_col, hidden_col, skip_row):
    """(template, label) used by the other rows of this column: the most common
    safe =IF($P{row}="","—",HYPERLINK($P{row},"label")) reading the hidden
    column, or (None, None) when there is no single such pattern."""
    hidden = column_letter(hidden_col)
    found = Counter()
    for row, _ in layout["rows"]:
        if row == skip_row:
            continue
        template = row_template(_formula(cells.get((row, visible_col))), row)
        match = SAFE.fullmatch(template or "")
        if match and match.group(1) == hidden and match.group(2) == hidden:
            found[template] += 1
    ranked = found.most_common(2)
    if not ranked or (len(ranked) > 1 and ranked[0][1] == ranked[1][1]):
        return None, None
    return ranked[0][0], SAFE.fullmatch(ranked[0][0]).group(3)


def column_link_labels(cells, layout, visible_col, skip_row):
    """The labels the other rows of this column show as links (normalized)."""
    return {norm(text(cells.get((row, visible_col)))) for row, _ in layout["rows"]
            if row != skip_row and not plain(cells.get((row, visible_col)))} - {""}


def _cell_url(agenda, entry, col, row):
    return "https://docs.google.com/spreadsheets/d/%s/edit#gid=%s&range=%s%d" % (
        agenda.spreadsheet_id, entry.get("sheet_id", 0), column_letter(col), row)


def _second_deck_target(cells, layout, row):
    """One exact current second-deck column pair and one existing Slides URL."""
    header = next((r for r in range(1, 8) if any(
        norm(text(cells.get((r, c)))) == "topic" for c in range(1, 27))), None)
    if not header:
        return None
    names = {c: next((name for r in range(header, 0, -1)
                      if (name := norm(text(cells.get((r, c)))))), "") for c in range(1, 27)}
    displays = [c for c, name in names.items() if name in ("2nddeck", "seconddeck")]
    hidden = [c for c, name in names.items() if name in ("2nddeckurl", "seconddeckurl", "deck2url", "teacher2url")]
    cols = layout["columns"]
    if displays != [cols.get("second_deck_display")]:
        return None
    if hidden:
        if hidden != [cols.get("second_deck")]:
            return None
    else:
        teachers = [c for c, name in names.items() if name == "teacherurl"]
        folders = [c for c, name in names.items() if name == "folderurl"]
        own = cols.get("second_deck")
        if not own or names.get(own) or teachers != [own - 1] or folders != [own + 1]:
            return None
    urls = links(cells.get((row, cols["second_deck"])))
    match = SECOND_DECK_URL.fullmatch(urls[0][0]) if len(urls) == 1 else None
    return match.group(1) if match else None


def _second_deck_source(token, fix):
    try:
        meta = google.drive_file(token, fix["target_id"], NODE_FIELDS)
    except google.GoogleError as error:
        raise Held("The exact second-deck source could not be verified (%s)." % error) from None
    if (meta.get("id") != fix["target_id"] or meta.get("mimeType") != completion.SLIDES
            or meta.get("trashed") is not False or not meta.get("name")
            or not meta.get("version") or not meta.get("modifiedTime")
            or not isinstance(meta.get("parents"), list)):
        raise Held("The exact second-deck target is not an untrashed Google Slides source with complete current metadata.")
    return deepcopy(meta)


def link_findings(agenda, class_key, rows):
    """Link problems for these lesson rows of one class tab, with a repair plan
    for each label that can be made clickable again."""
    entry = agenda.classes[class_key]
    layout, cells = entry["layout"], entry["cells"]
    if not layout:
        return []
    cols, found = layout["columns"], []
    for row in rows:
        week = dict(layout["rows"]).get(row, "")
        for visible_key, hidden_key, role in LINK_PAIRS:
            visible_col, hidden_col = cols.get(visible_key), cols.get(hidden_key)
            if not visible_col or not hidden_col or visible_col == hidden_col:
                continue
            visible, hidden = cells.get((row, visible_col)), cells.get((row, hidden_col))
            hidden_url = first_link(hidden)
            formula = _formula(visible)
            base = {"class_key": class_key, "week": week, "row": row, "role": role,
                    "column": column_letter(visible_col), "hidden_column": column_letter(hidden_col),
                    "sheet_id": entry.get("sheet_id", 0), "tab": entry.get("tab"),
                    "visible_text": text(visible).strip(), "hidden_url": hidden_url,
                    "source_url": _cell_url(agenda, entry, visible_col, row)}
            if formula:
                target = FORMULA_TARGET.search(formula)
                if target and (target.group(1).upper() != column_letter(hidden_col) or int(target.group(2)) != row):
                    found.append({**base, "kind": "visible_link_disagrees", "formula": formula,
                                  "reason": "The visible %s link reads %s%s instead of this row's hidden %s%d."
                                  % (role, target.group(1).upper(), target.group(2), column_letter(hidden_col), row)})
                    continue
            shown = [url for url, _ in links(visible) if url.startswith("https://")]
            if hidden_key == "turnin_url":
                pass  # audit.check compares the visible and hidden Forms already
            elif shown and hidden_url and file_id(shown[0]) and file_id(hidden_url) \
                    and file_id(shown[0]) != file_id(hidden_url):
                found.append({**base, "kind": "visible_link_disagrees", "visible_url": shown[0],
                              "reason": "The visible %s link opens a different file than the hidden %s%d URL."
                              % (role, column_letter(hidden_col), row)})
                continue
            if not hidden_url or not plain(visible) or formula:
                continue
            template, label = column_pattern(cells, layout, visible_col, hidden_col, row)
            shown_text = text(visible).strip()
            # A label, not typed text: blank, an ↗ label, or the label the column's other rows link.
            broken = (_blank(shown_text) or "↗" in shown_text or (label and norm(shown_text) == norm(label))
                      or norm(shown_text) in column_link_labels(cells, layout, visible_col, row))
            item = {**base, "kind": "visible_link_not_clickable",
                    "reason": "The visible %s cell %s%d is plain text, so it can't be clicked, while its hidden "
                              "%s%d holds the link." % (role, column_letter(visible_col), row,
                                                        column_letter(hidden_col), row)}
            if visible_key == "second_deck_display" and shown_text in SECOND_DECK_PLACEHOLDERS:
                target = _second_deck_target(cells, layout, row)
                if target:
                    item["plan"] = {"kind": "linked_label", "column": column_letter(visible_col), "row": row,
                                    "label": "2nd deck ↗", "url": hidden_url, "target_id": target,
                                    "source_check": "exact_second_deck", "pattern_from": "the exact second-deck role and hidden URL"}
                else:
                    item["reason"] += " Its second-deck columns or exact single Google Slides URL are unverified."
            elif broken and template:
                item["plan"] = {"kind": "formula", "column": column_letter(visible_col), "row": row,
                                "formula": template.replace("{row}", str(row)), "label": label,
                                "pattern_from": "other rows of column %s" % column_letter(visible_col)}
            elif not broken:
                item["reason"] += " The cell holds typed text, so the Hub leaves it for you."
            elif not _blank(shown_text):
                # No formula to copy: the same label, linked to the hidden URL
                # the way agenda_edit writes a linked label.
                item["plan"] = {"kind": "linked_label", "column": column_letter(visible_col), "row": row,
                                "label": shown_text, "url": hidden_url,
                                "pattern_from": "the label cell %s%d already shows" % (column_letter(visible_col), row)}
            else:
                item["reason"] += (" The other rows of this column don't share one link formula to copy, and the "
                                   "cell has no label to link.")
            found.append(item)
    for item in found:
        plan = item.get("plan") or {}
        item["fingerprint"] = content_hash({"kind": item["kind"], "agenda": agenda.spreadsheet_id,
                                            "sheet": item["sheet_id"], "row": item["row"], "column": item["column"],
                                            "text": item["visible_text"], "hidden": item["hidden_url"],
                                            "plan": plan.get("formula") or plan.get("kind")})
    return found


def _link_repair_grid(token, agenda, entry, plan):
    tab = "'%s'" % entry["tab"].replace("'", "''")
    row = plan["row"]
    try:
        fresh = google.sheet_grid(token, agenda.spreadsheet_id, [
            "%s!A1:Z7" % tab, "%s!A%d:Z%d" % (tab, row, row)]).get(entry["tab"], {})
    except google.GoogleError as error:
        raise Held("The Agenda could not be read again to verify the repair (%s)." % error) from None
    layout, problem = agenda_module.parse_class_tab(fresh)
    visible_key = next(v for v, h, role in LINK_PAIRS if role == plan["role"])
    hidden_key = next(h for v, h, role in LINK_PAIRS if role == plan["role"])
    old_cols = entry["layout"]["columns"]
    if (problem or not layout or layout["columns"].get(visible_key) != old_cols.get(visible_key)
            or layout["columns"].get(hidden_key) != old_cols.get(hidden_key)
            or norm(dict(layout["rows"]).get(row, "")) != norm(plan["week"])):
        raise Held("This Agenda row or its columns changed. Run the check again.")
    return fresh, layout, old_cols[visible_key], old_cols[hidden_key]


def _second_deck_row_basis(cells, row, visible_col):
    header = next((r for r in range(1, 8) if any(
        norm(text(cells.get((r, c)))) == "topic" for c in range(1, 27))), 0)
    return {(r, c): value for (r, c), value in cells.items()
            if (r <= header or r == row) and (r, c) != (row, visible_col)}


def repair_link(token, write_token, config, agenda, fingerprint, *, dry_run=True, port=None):
    """Make one plain-text Agenda label clickable again, by fingerprint.

    The plan is recomputed from the freshly loaded Agenda. Before writing, the
    tab's header rows and this row are read again: the week label, the column
    headers, the plain label text and the hidden URL must still be what the
    plan saw. Only that one plain cell is written: with the formula the same
    column uses in its other rows, or, when the column shares no formula, as
    the same label text linked to the hidden URL (agenda_edit's linked cell).
    An empty/dash second-deck placeholder may instead receive the neutral
    role label, after exact same-row URL, column and Slides metadata bookends.
    It is read back afterwards. A second request finds nothing to do.
    """
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
        raise Held("Choose one exact link repair from the latest check.")
    port = port if port is not None else configured_store(config)
    receipt_id = key("weeklypreflightrepair", fingerprint)
    prior = port.read([receipt_id]).documents[receipt_id]
    if prior and prior.value.get("status") == "repaired_and_verified":
        return {**prior.value, "changed": False, "dry_run": dry_run}
    plan = next((item for class_key, _ in agenda_module.CLASSES if agenda.classes[class_key]["layout"]
                 for item in link_findings(agenda, class_key, [r for r, _ in agenda.classes[class_key]["layout"]["rows"]])
                 if item["fingerprint"] == fingerprint), None)
    if not plan:
        return {"status": "nothing_to_do", "fingerprint": fingerprint, "changed": False, "dry_run": dry_run,
                "message": "That link is no longer a plain label with a hidden URL. Nothing was written."}
    if not plan.get("plan"):
        raise Held("This link needs your decision; the Hub has no safe formula to restore for it.")
    fix = plan["plan"]
    linked = fix["kind"] == "linked_label"
    guarded = fix.get("source_check") == "exact_second_deck"
    reply = {"status": "planned", "fingerprint": fingerprint, "dry_run": dry_run, "changed": False,
             "class_key": plan["class_key"], "week": plan["week"], "row": plan["row"],
             "cell": fix["column"] + str(plan["row"]), "formula": fix.get("formula"),
             "label": fix["label"] if linked else None, "url": fix["url"] if linked else None,
             "writes": "the label “%s” linked to %s" % (fix["label"], fix["url"]) if linked else fix["formula"],
             "source_url": plan["source_url"]}
    if dry_run and not guarded:
        return reply
    entry = agenda.classes[plan["class_key"]]
    tab = "'%s'" % entry["tab"].replace("'", "''")
    row = plan["row"]
    source = _second_deck_source(token, fix) if guarded else None
    # The second row read bookends the source read, including every same-row
    # lesson/Form/URL cell. Only this new placeholder repair needs that proof.
    for pass_index in range(2 if guarded else 1):
        fresh, layout, visible_col, hidden_col = _link_repair_grid(token, agenda, entry, plan)
        cell = fresh.get((row, visible_col))
        if (not plain(cell) or _formula(cell) or text(cell).strip() != plan["visible_text"]
                or first_link(fresh.get((row, hidden_col))) != plan["hidden_url"]):
            raise Held("That cell changed since the check (it may already be fixed). Nothing was written.")
        if guarded:
            if (_second_deck_target(fresh, layout, row) != fix["target_id"]
                    or _second_deck_row_basis(fresh, row, visible_col)
                    != _second_deck_row_basis(entry["cells"], row, visible_col)):
                raise Held("The exact second-deck row, columns or hidden URL changed. Nothing was written.")
            if pass_index == 0 and _second_deck_source(token, fix) != source:
                raise Held("The exact second-deck source changed during verification. Nothing was written.")
    if guarded:
        reply["target_source"] = source
    if dry_run:
        return reply
    new_cell = _linked_cell(fix["label"], fix["url"]) if linked else {"userEnteredValue": {"formulaValue": fix["formula"]}}
    try:
        google.batch_update(write_token, agenda.spreadsheet_id, [{"updateCells": {
            "range": _cell_range(entry.get("sheet_id", 0), row, visible_col), "rows": [{"values": [new_cell]}],
            "fields": "userEnteredValue,textFormatRuns" if linked else "userEnteredValue"}}])
    except google.GoogleError as error:
        raise _write_error(config, error) from None
    if guarded:
        try:
            saved, layout, _, _ = _link_repair_grid(token, agenda, entry, plan)
            after = saved.get((row, visible_col))
            verified_source = (_second_deck_source(token, fix) == source
                               and _second_deck_target(saved, layout, row) == fix["target_id"]
                               and _second_deck_row_basis(saved, row, visible_col)
                               == _second_deck_row_basis(entry["cells"], row, visible_col))
        except Held as error:
            return {**reply, "status": "unconfirmed", "changed": True, "problem": str(error)}
    else:
        after = google.sheet_grid(token, agenda.spreadsheet_id, ["%s!%s%d" % (tab, column_letter(visible_col), row)]
                                  ).get(entry["tab"], {}).get((row, visible_col))
    shown = [url for url, _ in links(after)]
    if linked:
        verified = (not _formula(after) and text(after).strip() == fix["label"] and bool(shown)
                    and (shown[0] == fix["url"] or file_id(shown[0]) == file_id(fix["url"]) != ""))
        if guarded:
            verified = verified and verified_source and shown == [fix["url"]]
    else:
        verified = (_formula(after).replace(" ", "") == fix["formula"].replace(" ", "")
                    and (not shown or file_id(shown[0]) == file_id(plan["hidden_url"])))
    reply.update(status="repaired_and_verified" if verified else "unconfirmed", changed=True,
                 verified_at=now() if verified else None)
    if verified:
        record = {"schema": SCHEMA, "kind": "link_repair", "fingerprint": fingerprint, "status": reply["status"],
                  "class_key": plan["class_key"], "week": plan["week"], "row": row, "column": fix["column"],
                  "repair": fix["kind"], "formula": fix.get("formula"), "label": reply["label"], "url": reply["url"],
                  "role": plan["role"], "verified_at": reply["verified_at"], "source_url": plan["source_url"]}
        if guarded:
            record["target_source"] = source
        _put(port, receipt_id, record)
        _index_repair(port, agenda.spreadsheet_id, record)
    return reply


def _index_repair(port, spreadsheet_id, record):
    ident = key("weeklypreflightrepairs", spreadsheet_id)
    for _ in range(3):
        old = port.read([ident]).documents[ident]
        items = dict((old.value if old else {}).get("items", {}))
        items["%s:%s:%s" % (record["class_key"], record["row"], record["column"])] = {
            field: record.get(field) for field in ("fingerprint", "week", "repair", "formula", "label", "url", "role",
                                                   "verified_at", "source_url")}
        if len(items) > 400:
            items = dict(sorted(items.items(), key=lambda kv: kv[1]["verified_at"])[-400:])
        try:
            _put(port, ident, {"schema": SCHEMA, "items": items}, old)
            return
        except Conflict:
            continue
    raise StoreUnavailable("weekly_preflight_repair_index_unconfirmed")


# ---------- private state ----------

def _put(port, ident, value, old=None):
    """One CAS write, confirmed by exact readback; an uncertain write is only reread."""
    try:
        port.commit({ident: (value, old.version if old else None)})
    except (Conflict, StoreUnavailable):
        actual = port.read([ident]).documents[ident]
        if not actual or actual.value != value:
            raise
    actual = port.read([ident]).documents[ident]
    if not actual or actual.value != value:
        raise StoreUnavailable("weekly_preflight_readback_unconfirmed")
    return actual


def _read_many(port, ids):
    found = {}
    ids = list(dict.fromkeys(ids))
    for start in range(0, len(ids), 40):
        found.update(port.read(ids[start:start + 40]).documents)
    return found


def check_id(kind, source_id, revision):
    return key("weeklypreflightcheck", [kind, source_id, revision, RULES])


def week_id(spreadsheet_id, monday, which):
    return key("weeklypreflightweek", [spreadsheet_id, monday, which])


def run_id(spreadsheet_id, monday, weeks):
    return key("weeklypreflightrun", [spreadsheet_id, monday, weeks])


# ---------- deck content ----------

TEACHER_ONLY = re.compile(r"TEACHER\s+(?:REFERENCE|NAVIGATION)")
HEADINGS = (("TO PRINT", "print"), ("BUY AHEAD", "buy"), ("PREPARE", "prepare"), ("PUT OUT FOR STUDENTS", "put_out"))
BULLET = " \t•▪◦*-–—·>‣○●□☐✓"
WEB = re.compile(r"https?://[^\s\"'<>\[\]]+")
MAX_WEB_LINKS = 80
# A note the author left for herself on a slide students see: "[DRAFT — confirm
# …]", "[added — …]", "[corrected: …]", "[TODO: …]", "[verify verbatim …]".
# Only a bracket that opens with one of these marker words and then a dash or
# colon counts (an all-lowercase or all-caps "verify" may take its object
# instead). Sentence-case prompts ("[Note: use mL]", "[Verify your answer]")
# and [[codes]] never match, and a box holding a code (an answer area or the
# code list) is never read for notes.
AUTHORING_NOTE = re.compile(
    r"(?<!\[)\[(?!\[)[ \t]*(?:(?:DRAFT|TODO|NOTE|TEACHER(?:[ \t]+(?:NOTE|ONLY))?|ADDED|[Aa]dded|CORRECTED"
    r"|[Cc]orrected|VERIFY|[Vv]erify)[ \t]*[—–:]|(?:verify|VERIFY)[ \t]+[A-Za-z])[^\]]{0,600}\]?")


def _container_text(runs, links, index):
    """One shape's or table cell's words; each linked run's start goes to links."""
    value = ""
    for run in runs:
        piece = (run or {}).get("textRun") or {}
        url = (((piece.get("style") or {}).get("link") or {}).get("url") or "")
        if url:
            links.append({"text": index, "at": len(value), "end": len(value) + len(piece.get("content", "")), "url": url})
        value += piece.get("content", "")
    # A soft line break (Shift+Enter) is \v in Slides; read it as a line break.
    # Same length, so the link offsets above still hold.
    return value.replace("\v", "\n")


def _native_texts(elements, out, links, depth=0):
    for element in elements or []:
        if not isinstance(element, dict) or depth > 20:
            continue
        if "elementGroup" in element:
            _native_texts((element["elementGroup"] or {}).get("children"), out, links, depth + 1)
        if "shape" in element:
            runs = ((element["shape"] or {}).get("text") or {}).get("textElements") or []
            out.append(_container_text(runs, links, len(out)))
        if "table" in element:
            for table_row in (element["table"] or {}).get("tableRows") or []:
                for table_cell in (table_row or {}).get("tableCells") or []:
                    runs = ((table_cell or {}).get("text") or {}).get("textElements") or []
                    out.append(_container_text(runs, links, len(out)))


def outline_native(presentation):
    """[{"number", "object_id", "texts", "links"}] from a presentations.get
    response; links retain their exact text index, start/end offsets and URL."""
    slides = presentation.get("slides") if isinstance(presentation, dict) else None
    if not isinstance(slides, list):
        raise completion.Unreadable("The native deck has no readable slide list.")
    out = []
    for number, page in enumerate(slides, 1):
        texts, links = [], []
        _native_texts((page or {}).get("pageElements"), texts, links)
        out.append({"number": number, "object_id": (page or {}).get("objectId", ""), "texts": texts, "links": links})
    return out


def outline_deck(deck, raw=None):
    """Keep completion text unchanged; retain PPTX hyperlink spans separately."""
    outline = [{"number": slide["number"], "object_id": "", "texts": [unit["text"] for unit in slide["units"]],
                "links": []} for slide in deck["slides"]]
    if raw is None:
        return outline
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            for slide, name in zip(outline, completion._slide_names(archive)):
                relname = name.rsplit("/", 1)[0] + "/_rels/" + name.rsplit("/", 1)[1] + ".rels"
                targets = {}
                if relname in archive.namelist():
                    if archive.getinfo(relname).file_size > completion.MAX_SLIDE_BYTES:
                        raise completion.Unreadable("This deck's hyperlink relationships are too large to read.")
                    targets = {r.get("Id"): r.get("Target", "") for r in ET.fromstring(archive.read(relname))
                               if r.get("Type", "").endswith("/hyperlink") and r.get("TargetMode") == "External"}
                root = ET.fromstring(archive.read(name))
                slide["linked_texts"] = []
                for body in root.iter():
                    if body.tag not in (completion.P + "txBody", completion.A + "txBody"):
                        continue
                    value, spans = "", []
                    for paragraph in body.findall(completion.A + "p"):
                        if value:
                            value += "\n"
                        for run in paragraph:
                            if run.tag == completion.A + "br":
                                value += "\n"
                                continue
                            content = "".join(t.text or "" for t in run.iter(completion.A + "t"))
                            link = run.find(completion.A + "rPr/" + completion.A + "hlinkClick")
                            target = targets.get(link.get(completion.R + "id")) if link is not None else None
                            if target:
                                spans.append({"at": len(value), "end": len(value) + len(content), "url": target})
                            value += content
                    if spans:
                        slide["linked_texts"].append({"value": value, "links": spans})
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as error:
        raise completion.Unreadable("This deck's hyperlink relationships could not be read.") from error
    return outline


def _google_file_id(url):
    """Exact supported Google file address; never extract an ID from another host."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    if parsed.scheme not in ("http", "https") or parsed.netloc.lower() not in ("docs.google.com", "drive.google.com"):
        return ""
    match = re.fullmatch(r"/(?:presentation|document|spreadsheets|forms|file)/d/([A-Za-z0-9_-]{20,})(?:/[^/]*)?/?",
                         parsed.path)
    if match:
        return match.group(1)
    if parsed.netloc.lower() == "drive.google.com" and parsed.path in ("/open", "/uc"):
        ids = parse_qs(parsed.query).get("id", [])
        if len(ids) == 1 and re.fullmatch(r"[A-Za-z0-9_-]{20,}", ids[0]):
            return ids[0]
    return ""


def deck_link_mismatches(outline):
    """Only a whole displayed Google file URL covered by one actual link can disagree.

    Adjacent style runs with the same target join; distinct links or an unlinked
    part of an address never get borrowed to manufacture a target.
    """
    found = {}
    for slide in outline:
        containers = slide.get("linked_texts", []) + [
            {"value": value, "links": [link for link in slide.get("links", []) if link["text"] == i]}
            for i, value in enumerate(slide["texts"])]
        for container in containers:
            spans = []
            for link in sorted(container["links"], key=lambda item: item["at"]):
                if "end" not in link or link["end"] <= link["at"]:
                    continue
                if spans and spans[-1]["end"] == link["at"] and spans[-1]["url"] == link["url"]:
                    spans[-1]["end"] = link["end"]
                else:
                    spans.append(dict(link))
            for match in WEB.finditer(container["value"]):
                shown = match.group(0).rstrip(".,;)")
                shown_id = _google_file_id(shown)
                if not shown_id:
                    continue
                covering = [link for link in spans if link["at"] <= match.start()
                            and link["end"] >= match.start() + len(shown)]
                if len(covering) != 1:
                    continue
                target = covering[0]["url"]
                target_id = _google_file_id(target)
                if target_id and target_id != shown_id:
                    found[(slide["number"], shown, target)] = {
                        "slide": slide["number"], "displayed_url": shown, "target_url": target}
    return list(found.values())


def web_links(slide):
    """[{"label", "url", "slide"}]: each http(s) page one slide links, with the
    words that name it. A URL typed on a line, or linked text, is named by its
    own line ("Science World — Yeast-Inflated Balloons: https://…"); a bare URL
    line, or a URL alone in the next table cell, by the line before it."""
    linked = {}
    for item in slide.get("links") or []:
        if WEB.match(str(item.get("url") or "")):
            linked.setdefault(item["text"], []).append((item["at"], item["url"]))
    out, previous = [], ""
    for index, value in enumerate(slide["texts"]):
        start = 0
        for raw in value.split("\n"):
            end = start + len(raw)
            urls = [m.group(0).rstrip(".,;)") for m in WEB.finditer(raw)]
            urls += [url for at, url in linked.get(index, []) if start <= at <= end and url not in urls]
            label = " ".join(WEB.sub(" ", raw).split()).strip(BULLET + ":|")
            start = end + 1
            if not urls:
                previous = label or previous
                continue
            for url in urls:
                if (label or previous) and len(url) <= 1000:
                    out.append({"label": (label or previous)[:160], "url": url, "slide": slide["number"]})
            previous = ""
    return out


def authoring_notes(slide):
    """The bracketed authoring notes on one slide, each as written."""
    return [" ".join(match.group(0).split()) for value in slide["texts"] if "[[" not in value
            for match in AUTHORING_NOTE.finditer(value)]


def _heading(line):
    """(known heading kind or "", rest of line) for a heading line, else None."""
    for name, kind in HEADINGS:
        match = re.match(r"%s\b\s*:?\s*(.*)$" % re.escape(name), line)
        if match:
            return kind, match.group(1).strip()
    letters = re.sub(r"[^A-Za-z]", "", line.rstrip(":"))
    if len(letters) >= 4 and letters.isupper():
        return "", ""
    return None


def deck_summary(outline):
    """Slide count, teacher-only slides, the TO PRINT / BUY AHEAD / PREPARE /
    PUT OUT blocks of teacher-reference slides, where each code sits, the
    deck's web links, and authoring notes on the slides students see."""
    teacher = [s["number"] for s in outline if any(TEACHER_ONLY.search(t.upper()) for t in s["texts"])]
    blocks = {kind: [] for _, kind in HEADINGS}
    for slide in outline:
        if slide["number"] not in teacher:
            continue
        current = None
        for raw in "\n".join(slide["texts"]).splitlines():
            line = " ".join(raw.strip(BULLET).split())
            if not line:
                continue
            head = _heading(line)
            if head is not None:
                current = head[0] or None
                if current and head[1]:
                    blocks[current].append({"text": head[1], "slide": slide["number"]})
                continue
            if current == "print" and WEB.fullmatch(line) and blocks["print"] \
                    and blocks["print"][-1]["slide"] == slide["number"]:
                # A bare URL under a print item is where that item prints from.
                blocks["print"][-1].setdefault("url", line.rstrip(".,;)"))
                continue
            if current:
                blocks[current].append({"text": line, "slide": slide["number"]})
    codes = {}
    for slide in outline:
        for value in slide["texts"]:
            for match in completion.MARKER.finditer(value):
                codes.setdefault("%s %s" % (match.group(1), match.group(3).strip()), []).append(slide["number"])
    links = list({(link["label"], link["url"]): link for slide in outline for link in web_links(slide)}.values())
    notes = []
    for slide in outline:
        found = [] if slide["number"] in teacher else authoring_notes(slide)
        if found:
            notes.append({"slide": slide["number"], "count": len(found),
                          "note": found[0] if len(found[0]) <= 80 else found[0][:79].rstrip() + "…"})
    return {"slide_count": len(outline), "teacher_only_slides": teacher, "blocks": blocks,
            "code_slides": {k: sorted(set(v)) for k, v in codes.items()},
            "page_ids": {str(s["number"]): s["object_id"] for s in outline if s["object_id"]},
            "web_links": links[:MAX_WEB_LINKS], "authoring_notes": notes,
            "link_mismatches": deck_link_mismatches(outline)}


def _native_teacher(node):
    # These roles originate only in the current Agenda row's exact link cells.
    return bool(set(node.get("roles", [])) & {"teacher_deck", "second_deck"})


def _readiness_source(token, node):
    expected = node.get("_source_metadata") or {}
    if (expected.get("id") != node["id"] or expected.get("mimeType") not in DECKS
            or expected.get("trashed") is not False or not expected.get("name")
            or not expected.get("version") or not expected.get("modifiedTime")
            or not isinstance(expected.get("parents"), list)):
        raise completion.Unreadable("The exact Agenda deck has incomplete current source metadata; recheck its source.")
    actual = google.drive_file(token, node["id"], NODE_FIELDS)
    if actual != expected:
        raise completion.Unreadable("The exact Agenda deck changed since source discovery; run the check again.")
    return actual


def _readiness_failure(error):
    return {"status": "NOT_READY", "label": "Cannot read lesson", "revision": "",
            "problem": str(error), "issues": ["deck_release_unreadable"],
            "readability": {"stage": "metadata", "http_status": getattr(error, "status", None)}}


def _readiness(token, node):
    try:
        before = _readiness_source(token, node)
        native_teacher = _native_teacher(node)
        result = audit._release_deck(token, node["id"], native_teacher=native_teacher)
        _readiness_source(token, node)
        if native_teacher and not result.get("readability"):
            consumed = result.get("_teacher_source_metadata") or {}
            if any(consumed.get(field) != before.get(field) for field in audit._NATIVE_METADATA):
                raise completion.Unreadable("The exact teacher metadata consumed by the code check does not match its current source.")
    except (google.GoogleError, completion.Unreadable) as error:
        return _readiness_failure(error)
    keep = ("status", "label", "issues", "missing_ids", "extra_ids", "problem", "readability", "revision",
            "inventory_cycle", "counts", "declared_counts", "diagnostics")
    return {field: result.get(field) for field in keep if field in result}


def _check_identity(kind, node):
    # A teacher's export-403 fallback must not authorize a student-only link,
    # even if both rows reference the same file and Drive revision.
    scoped = "teacher_readiness" if kind == "readiness" and _native_teacher(node) else kind
    return scoped, node["id"], node["revision"]


def _outline(token, node):
    stage = "native_read" if node["mimeType"] == completion.SLIDES else "download"
    try:
        _readiness_source(token, node)
        if node["mimeType"] == completion.SLIDES:
            presentation = google.native_presentation(token, node["id"])
            stage = "parse"
            if presentation.get("presentationId") != node["id"]:
                raise completion.Unreadable("The native deck identity does not match the current Agenda source.")
            outline = outline_native(presentation)
            revision_id = str(presentation.get("revisionId") or "")
        else:
            raw = google.deck_bytes(token, node["id"], node["mimeType"])
            stage = "parse"
            outline, revision_id = outline_deck(completion.read_deck(raw), raw), ""
        _readiness_source(token, node)
        return {"ok": True, **deck_summary(outline), "revision_id": revision_id}
    except (google.GoogleError, completion.Unreadable) as error:
        return {"ok": False, "stage": stage, "http_status": getattr(error, "status", None), "problem": str(error)}


def _pages(token, node):
    try:
        raw = google.printable_pdf_bytes(token, node["id"], node["mimeType"])
        from pypdf import PdfReader
        pages = len(PdfReader(BytesIO(raw), strict=False).pages)
        if pages < 1:
            raise ValueError()
        return {"ok": True, "pages": pages}
    except google.GoogleError as error:
        return {"ok": False, "stage": "download", "http_status": error.status, "problem": str(error)}
    except Exception:
        return {"ok": False, "stage": "parse", "http_status": None, "problem": "The print file's pages couldn't be counted."}


def sides(pages):
    if not pages:
        return "unknown"
    if pages == 1:
        return "single-sided"
    return "double-sided" if pages % 2 == 0 else "double-sided (odd page count: last sheet single)"


# ---------- print and supply lists ----------

QUANTITY = re.compile(r"(\d+)\s*(?:cop(?:y|ies)\s*)?(?:per|/|for\s+each|each)\s*(student|group|pair|table|section|class|teacher)"
                      r"(?:\s+of\s+(\d+))?", re.I)
COPIES = re.compile(r"\b(\d+)\s*(?:cop(?:y|ies)|sets?)\b", re.I)
OPTIONAL = re.compile(r"\(if\b[^)]*\bneeded\)|support\s+cop(?:y|ies)|\bif\s+needed\b|\boptional\b", re.I)
# Keys and answers are teacher-only; a dichotomous key or key-terms sheet is a student handout.
KEYWORDS = re.compile(r"\b(?:answer\s*key|(?<!dichotomous\s)keys?(?!\s+(?:terms?|vocab\w*|words?|concepts?))|answers?"
                      r"|teacher\s+(?:copy|only|version|reference))\b", re.I)
PREFIX = re.compile(r"^\s*(?:cycle|wk|week|c)\s*\d+\s*[—–:\-|]+\s*", re.I)
STOP = {"the", "a", "an", "of", "for", "and", "to", "per", "student", "students", "group", "groups", "copy", "copies",
        "print", "printed", "each", "on", "in", "with", "set", "sets", "class", "pair", "pairs", "if", "needed",
        "support", "sheet", "page", "pages", "pdf", "docx", "doc"}


def _tokens(value, stop=STOP):
    value = PREFIX.sub("", str(value or ""))
    value = QUANTITY.sub(" ", COPIES.sub(" ", re.sub(r"\([^)]*\)", " ", value)))
    value = re.sub(r"\.(pdf|docx?|pptx?)$", "", value.strip(), flags=re.I)
    words = re.findall(r"[a-z0-9]+", value.lower().replace("₂", "2"))
    return [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words if w not in stop]


def quantity(line, sections, counts):
    """How many to print or have, per meeting section, and on what basis."""
    optional = bool(OPTIONAL.search(line))
    match = QUANTITY.search(line)
    copies = COPIES.search(line)
    per_section, basis = {}, "quantity not stated"
    if match:
        each, unit, size = int(match.group(1)), match.group(2).lower(), match.group(3)
        if unit in ("student",):
            basis = "%d per student (current roster)" % each
            rule = lambda n: each * n
        elif unit in ("group", "table"):
            size = int(size) if size else 4
            basis = "%d per %s of %d: %d x ceil(students/%d)" % (each, unit, size, each, size)
            rule = lambda n, size=size: each * math.ceil(n / size)
        elif unit == "pair":
            basis = "%d per pair: %d x ceil(students/2)" % (each, each)
            rule = lambda n: each * math.ceil(n / 2)
        else:
            basis = "%d per %s" % (each, unit)
            rule = lambda n: each
        for section in sections:
            n = counts.get(section) if isinstance(counts, dict) else None
            per_section[section] = rule(n) if isinstance(n, int) else None
        if any(v is None for v in per_section.values()):
            basis += "; roster count unavailable for " + ", ".join(s for s, v in per_section.items() if v is None)
    elif copies:
        basis = "%s copies (as written)" % copies.group(1)
        per_section = {"all": int(copies.group(1))}
    return {"optional": optional, "basis": basis, "per_section": per_section,
            "stated": bool(match or copies)}


def match_file(line, files):
    """Name-token match; one current Agenda extra material may break an exact tie."""
    want = set(_tokens(line))
    if not want:
        return None, 0, False
    by_id = {}
    explicit = {item["id"]: item for item in files if item.get("agenda_extra_material")}
    for item in files:
        # Rank the current Agenda name, not a stale shortcut/folder alias of
        # that same file. An alias must not manufacture a winning exact tie.
        item = explicit.get(item["id"], item)
        have = set(_tokens(item.get("name", "")))
        inter = len(want & have)
        if not inter:
            continue
        score = inter / len(want)
        if score >= 0.6 or (inter >= 2 and score >= 0.5):
            candidate = (-score, -inter, len(have), item.get("name", ""), item["id"], item)
            if item["id"] not in by_id or candidate[:5] < by_id[item["id"]][:5]:
                by_id[item["id"]] = candidate
    ranked = list(by_id.values())
    ranked.sort(key=lambda r: r[:5])
    if not ranked:
        return None, 0, False
    # Fewer unmatched words wins ("CER worksheet" over "CER worksheet answer key").
    tied = [candidate for candidate in ranked if candidate[:3] == ranked[0][:3]]
    bound = [candidate for candidate in tied if candidate[4] in explicit]
    best = bound[0] if len(bound) == 1 else ranked[0]
    return best[5], round(-best[0], 2), len(tied) > 1 and len(bound) != 1


def match_link(line, links):
    """The one deck link whose name carries the print line's words: at least
    three in four of them and two or more (or exactly the same words). Two
    equally good links to different pages match neither."""
    want = set(_tokens(WEB.sub(" ", line)))
    if not want:
        return None
    ranked = []
    for link in links:
        have = set(_tokens(link["label"]))
        shared = len(want & have)
        if shared and shared / len(want) >= 0.75 and (shared >= 2 or have == want):
            ranked.append((-shared / len(want), len(have), link))
    ranked.sort(key=lambda r: r[:2])
    if not ranked or any(r[:2] == ranked[0][:2] and r[2]["url"] != ranked[0][2]["url"] for r in ranked[1:]):
        return None
    return ranked[0][2]


def web_source(line, links):
    """Where a print line with no folder file prints from: the http(s) URL on
    the line itself (or on the bare line under it), else the matching entry of
    its own deck's link list; None when neither gives one."""
    own = line.get("url") or next((m.group(0).rstrip(".,;)") for m in WEB.finditer(line["text"])), "")
    if own:
        return {"url": own, "from": "the print line"}
    found = match_link(line["text"], links)
    if not found:
        return None
    return {"url": found["url"], "label": found["label"], "slide": found["slide"], "from": "the deck's link list"}


ORDER = re.compile(r"✔\s*on\s+order\s*[—–:\-]?\s*(.+)$", re.I)
SEGMENT = re.compile(r"\s+·\s+|\s*\n\s*|\s*;\s*(?=🛒|✔)")
PREP_BY = re.compile(r"PREP\s+by\s+(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\.?\s+(\d{1,2})/(\d{1,2})", re.I)
DAYS_IN = re.compile(r"\(\s*((?:Mon|Tue|Wed|Thu|Fri)(?:\s*/\s*(?:Mon|Tue|Wed|Thu|Fri))*)\s*\)")
ITEM_JUNK = re.compile(r"^[\s🛒✔⚠️🧪→\-–—:·•]+|[\s.;:]+$")


def _items(value):
    return [ITEM_JUNK.sub("", part).strip() for part in re.split(r",(?![^()]*\))", value) if ITEM_JUNK.sub("", part).strip()]


def parse_prep(value, monday, meeting_dates):
    """Supply rows and the activity from one Agenda prep cell. A "PREP by Thu
    10/22 -> LAB ..." cell is a deadline: its use dates are not stated."""
    value = str(value or "")
    deadline = PREP_BY.search(value)
    prep_by = None
    if deadline:
        when = _near(monday, int(deadline.group(2)), int(deadline.group(3)))
        prep_by = when.isoformat() if when else None
    days = DAYS_IN.search(value)
    named = [d.strip()[:3].title() for d in days.group(1).split("/")] if days else []
    use_dates = [] if deadline else [d["date"] for d in meeting_dates if not named or d["day"] in named]
    activity = ""
    head = re.search(r"\bLAB\b\s*(?:\([^)]*\))?\s*([^·\n]+)", value)
    if head:
        activity = head.group(1).strip()
    rows = []
    for segment in SEGMENT.split(value):
        segment = segment.strip()
        buy = re.match(r"^\W*BUY(?:\s+AHEAD)?\s*:\s*(.+)$", segment, re.I)
        pull = re.match(r"^\W*PULL\s*:\s*(.+)$", segment, re.I)
        order = ORDER.search(segment)
        cart = segment.startswith("🛒")
        if buy:
            rows += [(item, "buy") for item in _items(buy.group(1))]
        elif pull:
            rows += [(item, "pull") for item in _items(pull.group(1))]
        elif order:
            rows += [(item, "on order") for item in _items(order.group(1))]
        elif cart:
            rows += [(item, "buy") for item in _items(segment)]
    return {"activity": activity, "prep_by": prep_by, "use_dates": use_dates, "named_days": named,
            "items": rows}


HANDOUT = re.compile(r"\b(work)?sheets?\b|\bhandouts?\b|\bcopies\b|\bprint", re.I)
GENERIC = {"demo", "lab", "day", "activity", "station", "stations", "class", "lesson", "quick", "short"}


def _week_text(found):
    """This week's Agenda words for one lesson: day plans, notes, to-do and prep."""
    parts = [found.get("topic", ""), found.get("todo", ""), found.get("prep", "")]
    parts += [p.get("plan", "") + " " + p.get("note", "") for p in found.get("plans", [])]
    return set(_tokens(" ".join(parts), stop=set()))


def deck_supply_items(line, week_tokens, kind):
    """The supply items one deck BUY AHEAD / PUT OUT line names for THIS week.

    A deck line reads "A, B, and C — what it is for (Day 2)". A deck spans
    several weeks, so a line is kept only when this week's Agenda mentions
    what it is for (the yeast lab is kept, last week's marshmallow demo is
    not); a line naming no purpose is kept. Handouts on a PUT OUT line are
    print items, not supplies."""
    text = " ".join(str(line or "").split())
    head, purpose = (re.split(r"\s+[—–]\s+", text, maxsplit=1) + [""])[:2]
    purpose_tokens = [t for t in _tokens(re.sub(r"\(Day\s*\d+\)", " ", purpose), stop=STOP) if t not in GENERIC]
    if purpose_tokens and not set(purpose_tokens) & week_tokens:
        return []
    head = re.sub(r"\s+at each (lab )?station.*$", "", head, flags=re.I)
    parts = [part.strip(" .") for part in re.split(r",\s*(?:and\s+|or\s+)?|\s+and\s+(?![^()]*\))", head)]
    items = [part for part in parts if part and not (kind == "put_out" and HANDOUT.search(part))]
    return [re.sub(r"^(a|an|the)\s+", "", item, flags=re.I) for item in items]


def _head_tokens(name):
    return _tokens(re.sub(r"^\d+\s*\w*\s+(?:of\s+)?", "", name), stop={"of", "the", "a", "an", "and", "for"})


def merge_supplies(rows):
    """Merge rows naming the same thing (yeast / active dry yeast; water bath /
    warm-water bath) by name tokens and head noun, keeping every source. Status:
    on order (not verified in hand) > buy > pull > stock unknown; never in hand."""
    groups = []
    for row in rows:
        tokens = _head_tokens(row["item"])
        if not tokens:
            continue
        home = next((g for g in groups if tokens[-1] == g["tokens"][-1]
                     and (set(tokens) <= set(g["tokens"]) or set(g["tokens"]) <= set(tokens))), None)
        if home is None:
            home = {"tokens": tokens, "item": row["item"], "names": [], "sources": []}
            groups.append(home)
        elif len(tokens) > len(home["tokens"]):
            home["tokens"], home["item"] = tokens, row["item"]
        if row["item"] not in home["names"]:
            home["names"].append(row["item"])
        home["sources"].append(row)
    rank = {"on order": 0, "buy": 1, "pull": 2, "stock unknown": 3}
    out = []
    for group in groups:
        statuses = sorted({s["status"] for s in group["sources"]}, key=rank.get)
        stated = [s["quantity"] for s in group["sources"] if s.get("quantity") and s["quantity"] != "quantity not stated"]
        deadlines = sorted(s["prep_by"] for s in group["sources"] if s.get("prep_by"))
        out.append({"item": group["item"], "also_named": [n for n in group["names"] if n != group["item"]],
                    "status": "on order (not verified in hand)" if statuses[0] == "on order" else statuses[0],
                    "statuses": statuses, "quantity": "; ".join(dict.fromkeys(stated)) or "quantity not stated",
                    "lessons": list(dict.fromkeys(s["lesson"] for s in group["sources"])),
                    "classes": list(dict.fromkeys(s["class"] for s in group["sources"])),
                    "use_dates": sorted({d for s in group["sources"] for d in s.get("use_dates", [])}),
                    "prep_by": deadlines[0] if deadlines else None,
                    "sources": group["sources"], "in_hand_verified": False})
    return sorted(out, key=lambda r: (r["prep_by"] or (r["use_dates"] or ["9999"])[0], r["item"].lower()))


# ---------- the run ----------

def _url(node_id, mime):
    if mime == completion.SLIDES:
        return "https://docs.google.com/presentation/d/%s/edit" % node_id
    if mime == google.FOLDER:
        return "https://drive.google.com/drive/folders/%s" % node_id
    if mime == google.DOC:
        return "https://docs.google.com/document/d/%s/edit" % node_id
    if mime == "application/vnd.google-apps.form":
        return "https://docs.google.com/forms/d/%s/edit" % node_id
    return "https://drive.google.com/open?id=%s" % node_id


def revision(meta):
    return "%s|%s|%s" % (meta.get("version") or "", meta.get("modifiedTime") or "", meta.get("md5Checksum") or "")


class _Run:
    """Memoized reads for one run, the private check cache and the time budget."""

    def __init__(self, token, port, deadline, progress):
        self.token, self.port, self.deadline, self.progress = token, port, deadline, progress
        self.meta, self.listings, self.forms, self.checks = {}, {}, {}, {}
        self.looked, self.counts = set(), Counter()

    def file(self, ident):
        if ident not in self.meta:
            try:
                self.meta[ident] = google.drive_file(self.token, ident, NODE_FIELDS)
            except google.GoogleError as error:
                self.meta[ident] = {"id": ident, "error": str(error), "http_status": error.status}
        return self.meta[ident]

    def listing(self, ident):
        if ident not in self.listings:
            try:
                items, complete = google.folder_listing(self.token, ident)
                self.listings[ident] = {"items": items, "complete": complete}
            except google.GoogleError as error:
                self.listings[ident] = {"items": [], "complete": False, "error": str(error), "http_status": error.status}
        return self.listings[ident]

    def form(self, ident):
        if ident not in self.forms:
            meta = self.file(ident)
            info = {"meta": meta}
            if not meta.get("error"):
                try:
                    found = google.form_info(self.token, ident)
                    responder = found.get("responderUri", "") or ""
                    published = audit.PUBLISHED.search(responder)
                    info.update(title=(found.get("info") or {}).get("title", ""), responder_url=responder,
                                published_id=published.group(1) if published else "")
                except google.GoogleError as error:
                    info.update(error=str(error), http_status=error.status)
            self.forms[ident] = info
        return self.forms[ident]

    def prefetch(self, wanted):
        ids = [check_id(kind, ident, rev) for kind, ident, rev in wanted]
        found = _read_many(self.port, ids)
        for ident, cid in zip(wanted, ids):
            self._remember(ident, found.get(cid))

    def _remember(self, ident, doc):
        self.looked.add(ident)
        if (doc and doc.value.get("schema") == SCHEMA and doc.value.get("rules") == RULES
                and [doc.value.get("kind"), doc.value.get("source_id"), doc.value.get("revision")] == list(ident)):
            self.checks[ident] = doc.value["result"]
            self.counts["cached"] += 1

    def check(self, kind, node, compute):
        """A cached result for this exact revision, a fresh one saved at once
        (the checkpoint), or None when the time budget has run out."""
        ident = _check_identity(kind, node)
        if ident not in self.looked:
            self._remember(ident, self.port.read([check_id(*ident)]).documents[check_id(*ident)])
        if ident in self.checks:
            if kind in ("readiness", "outline"):
                try:
                    _readiness_source(self.token, node)
                except (google.GoogleError, completion.Unreadable) as error:
                    if kind == "outline":
                        return {"ok": False, "stage": "metadata", "problem": str(error),
                                "http_status": getattr(error, "status", None)}
                    return _readiness_failure(error)
            return self.checks[ident]
        if time.monotonic() > self.deadline:
            self.counts["pending"] += 1
            return None
        result = compute(self.token, node)
        self.counts["checked"] += 1
        changed = kind == "readiness" and result.get("revision") and str(node.get("version") or "") \
            and result["revision"] != str(node.get("version"))
        if changed:
            self.counts["pending"] += 1
            return None
        self.checks[ident] = result
        # Only a completed read is kept: a failed download is tried again next run.
        if result.get("ok") if "ok" in result else not result.get("readability"):
            record = {"schema": SCHEMA, "rules": RULES, "kind": ident[0], "source_id": node["id"],
                      "revision": node["revision"], "result": result, "checked_at": now()}
            try:
                bounded_json(record)
                doc = self.port.read([check_id(*ident)]).documents[check_id(*ident)]
                if not doc:
                    _put(self.port, check_id(*ident), record)
                self.progress(self.counts)
            except ValueError:
                pass  # an oversized result is used for this run only
        return result


def _exception(kind, status, lesson, reason, action, *, url, sources=(), revision_key="", detail="",
               date_value=None, slides=(), blocking=True, evidence=None):
    if status not in EXCEPTION_STATUSES:
        raise ValueError(status)
    return {"kind": kind, "status": status, "blocking": blocking,
            "class_key": lesson.get("class_key"), "class": lesson.get("class"),
            "sections": list(lesson.get("sections", [])), "week": lesson.get("week"),
            "date": date_value, "lesson": lesson.get("topic", ""), "row": lesson.get("row"),
            "slides": sorted(set(slides)), "source_url": url, "reason": reason, "next_action": action,
            "evidence": evidence or {},
            "fingerprint": content_hash({"kind": kind, "sources": sorted(set(sources)), "revision": revision_key,
                                         "detail": detail, "lesson": [lesson.get("class_key"), lesson.get("week"),
                                                                      lesson.get("row")]})}


def run(token, config, agenda, start, weeks, roster_counts, *, port=None, budget_seconds=BUDGET_SECONDS):
    """Check the selected weeks, save each finished source check at once, and
    store and return the result. Sources are read with the Hub's read token."""
    monday, weeks = monday_of(start), week_count(weeks)
    port = port if port is not None else configured_store(config)
    sheet = agenda.spreadsheet_id
    progress_id = run_id(sheet, monday.isoformat(), weeks)
    started = now()
    progress_state = {"doc": port.read([progress_id]).documents[progress_id]}

    def progress(counts, status="running"):
        value = {"schema": SCHEMA, "status": status, "start": monday.isoformat(), "weeks": weeks,
                 "started_at": started, "checked": counts["checked"], "cached": counts["cached"],
                 "pending": counts["pending"]}
        try:
            progress_state["doc"] = _put(port, progress_id, value, progress_state["doc"])
        except Conflict:
            progress_state["doc"] = port.read([progress_id]).documents[progress_id]
    ctx = _Run(token, port, time.monotonic() + budget_seconds, progress)
    progress(ctx.counts)
    calendars = {}
    for week in agenda.weeks:
        try:
            calendars[week["monday"]] = week_calendar(week)
        except (ValueError, KeyError):
            continue
    repairs = (port.read([key("weeklypreflightrepairs", sheet)]).documents[key("weeklypreflightrepairs", sheet)])
    repairs = (repairs.value.get("items", {}) if repairs else {})
    counts = roster_counts if isinstance(roster_counts, dict) else {}

    # 1. Agenda-only checks and each lesson's source references.
    plans = []
    for offset in range(weeks):
        when = (monday + timedelta(days=7 * offset)).isoformat()
        week = next((w for w in agenda.weeks if w["monday"] == when), None)
        plans.append(_plan_week(agenda, config, week, when, calendars, counts, repairs))
    # 2. Metadata for every source, then the cached or fresh content checks.
    for week_plan in plans:
        for lesson in week_plan["lessons"]:
            _resolve_sources(ctx, lesson)
    wanted = list(dict.fromkeys(
        _check_identity(kind, node) for week_plan in plans for lesson in week_plan["lessons"]
        for node in lesson["sources"] if node.get("accessible") and not node.get("trashed")
        for kind in _content_kinds(node)))
    ctx.prefetch(wanted)
    results = []
    for week_plan in plans:
        for lesson in week_plan["lessons"]:
            _content(ctx, lesson, counts)
        results.append(_finish_week(week_plan, sheet, port))
    status = "incomplete" if ctx.counts["pending"] or any(w["status"] == "incomplete" for w in results) else "complete"
    progress(ctx.counts, status)
    remember_labels(port, sheet, results)
    complete = [port.read([week_id(sheet, w["monday"], "complete")]).documents[week_id(sheet, w["monday"], "complete")]
                for w in results]
    reply = compose(results, monday, weeks, run_counts={k: ctx.counts[k] for k in ("checked", "cached", "pending")},
                    last_successful=min(d.value["checked_at"] for d in complete) if all(complete) else None)
    attach_reviews(port, reply)
    return reply


def _content_kinds(node):
    kinds = []
    if node["mimeType"] in DECKS and set(node["roles"]) & {"teacher_deck", "second_deck", "student_copy"}:
        kinds += ["readiness", "outline"]
    if node.get("print_candidate") and node["mimeType"] in (PDF, google.DOC):
        kinds.append("pages")
    return kinds


def _plan_week(agenda, config, week, when, calendars, counts, repairs):
    out = {"monday": when, "label": week["label"] if week else None, "lessons": [], "week_exceptions": [],
           "calendar": calendars.get(when), "problems": []}
    stub = {"class": "All classes", "sections": [], "week": week["label"] if week else when, "topic": ""}
    if not week:
        out["problems"].append("The week of %s isn't on the Agenda's Weeks tab." % when)
        out["week_exceptions"].append(_exception(
            "week_not_in_agenda", "needs_decision", stub, "The week of %s isn't on the Weeks tab, so its calendar "
            "and lessons can't be checked." % when, "Add the week to the Agenda's Weeks tab.",
            url="https://docs.google.com/spreadsheets/d/%s/edit" % agenda.spreadsheet_id, detail=when))
        return out
    calendar = out["calendar"]
    for problem in calendar["problems"]:
        out["week_exceptions"].append(_exception(
            "calendar_unreadable", "needs_decision", stub, problem, "Correct the Weeks tab's closures cell.",
            url="https://docs.google.com/spreadsheets/d/%s/edit" % agenda.spreadsheet_id,
            revision_key=calendar["closures"], detail=problem))
    week_decks = set()
    for class_key, _ in agenda_module.CLASSES:
        for lesson in agenda.lessons(class_key, week["label"]) if agenda.classes[class_key]["layout"] else []:
            week_decks.update(file_id(m["url"]) for m in lesson["materials"]
                              if re.search(r"/presentation/d/|/file/d/", m["url"]))
    for class_key, class_name in agenda_module.CLASSES:
        entry = agenda.classes[class_key]
        sections = list(roster.SECTIONS[class_key])
        days = class_days(calendar, class_key)
        meeting = [d for d in days if d["meets"]]
        base = {"class_key": class_key, "class": class_name, "sections": sections, "week": week["label"],
                "monday": when, "topic": "", "row": None}
        if not entry["layout"]:
            out["week_exceptions"].append(_exception(
                "agenda_tab_unreadable", "needs_decision", base, entry["problem"] or "The class tab couldn't be read.",
                "Fix the class tab's header row in the Agenda.",
                url="https://docs.google.com/spreadsheets/d/%s/edit" % agenda.spreadsheet_id, detail=class_key))
            continue
        policy = work.week_policy(config, agenda, class_key, week["label"])
        rows = agenda.lessons(class_key, week["label"])
        teaching = [r for r in rows if lesson_kind(r["topic"]) == "lesson"]
        if any(lesson_kind(r["topic"]) == "closed" for r in rows) and not teaching:
            continue
        if not teaching:
            if meeting:
                edit = rows[0]["edit_url"] if rows else "https://docs.google.com/spreadsheets/d/%s/edit" % agenda.spreadsheet_id
                out["week_exceptions"].append(_exception(
                    "needs_content", "needs_decision", base,
                    "%s meets on %s, but no lesson is planned for %s." % (
                        class_name, ", ".join(d["date"] for d in meeting), week["label"]),
                    "Plan the lesson in the Agenda, or mark the week [CLOSED …].", url=edit, detail="no lesson",
                    date_value=meeting[0]["date"]))
            continue
        empty_days = {d["date"]: d for d in meeting}
        for found in teaching:
            lesson = {**base, "row": found["row"], "topic": found["topic"], "agenda_url": found["edit_url"],
                      "days": days, "meeting_dates": {s: [d["date"] for d in meeting] for s in sections},
                      "week_policy": policy, "lesson_id": "%s:%s:%d" % (class_key, week["label"], found["row"]),
                      "found": found, "exceptions": [], "sources": [], "print_items": [], "supply_rows": [],
                      "teacher_prep": [], "pending": [], "repaired": []}
            _plan_checks(agenda, lesson, days, calendars, empty_days)
            _link_checks(agenda, lesson, repairs)
            lesson["refs"] = _references(found, week_decks)
            prep = parse_prep(found.get("prep", ""), date.fromisoformat(when), meeting)
            for item, status in prep["items"]:
                lesson["supply_rows"].append(_supply_row(item, status, lesson, prep, "Agenda prep cell",
                                                         _cell_url(agenda, entry, entry["layout"]["columns"]["prep"],
                                                                   found["row"]) if entry["layout"]["columns"].get("prep")
                                                         else found["edit_url"], sections, counts))
            lesson["print_lines"] = _agenda_print_lines(found)
            out["lessons"].append(lesson)
        unknown = [d for d in days if d["meets"] is None]
        if unknown and "Fri" in entry["layout"]["plans"]:
            lesson = next(l for l in out["lessons"] if l["class_key"] == class_key)
            lesson["exceptions"].append(_exception(
                "calendar_friday_unknown", "needs_decision", lesson,
                "The Weeks tab doesn't say whether %s is an A or B Friday, so %s's Friday can't be checked." % (
                    unknown[0]["date"], class_name),
                "Add “Fri %s = A Friday” or “= B Friday” to the week's closures cell." % (
                    "%d/%d" % (date.fromisoformat(unknown[0]["date"]).month, date.fromisoformat(unknown[0]["date"]).day)),
                url="https://docs.google.com/spreadsheets/d/%s/edit" % agenda.spreadsheet_id,
                revision_key=calendar["closures"], detail="friday:" + unknown[0]["date"], date_value=unknown[0]["date"]))
        for day in sorted(empty_days):
            lesson = next(l for l in out["lessons"] if l["class_key"] == class_key)
            column = entry["layout"]["plans"].get(empty_days[day]["day"], {}).get("plan")
            lesson["exceptions"].append(_exception(
                "needs_content", "needs_decision", lesson,
                "%s meets %s %s (%s), but the plan for that day is empty." % (
                    class_name, empty_days[day]["day"], day, empty_days[day]["reason"]),
                "Write the plan for %s in the Agenda, or note that the class doesn't meet." % day,
                url=_cell_url(agenda, entry, column, lesson["row"]) if column else lesson["agenda_url"],
                detail="empty:" + day, date_value=day))
    return out


def _plan_checks(agenda, lesson, days, calendars, empty_days):
    entry = agenda.classes[lesson["class_key"]]
    layout, found = entry["layout"], lesson["found"]
    monday = date.fromisoformat(lesson["monday"])
    for plan in found["plans"]:
        day = next((d for d in days if d["day"] == plan["day"]), None)
        if not day:
            continue
        column = layout["plans"][plan["day"]]["plan"]
        url = _cell_url(agenda, entry, column, lesson["row"])
        reading = plan_reading(plan["plan"], lesson["class_key"], plan["day"], monday)
        if not reading["blank"]:
            empty_days.pop(day["date"], None)
        cell = {"plan": reading["text"][:300], "day": plan["day"], "calendar": day["reason"]}
        if reading["verify"]:
            lesson["exceptions"].append(_exception(
                "needs_content", "needs_decision", lesson,
                "The %s plan (%s) is marked for checking: “%s”." % (plan["day"], day["date"], reading["text"][:160]),
                "Confirm the day and finish the plan, then remove the marker.", url=url,
                revision_key=reading["text"], detail="verify:" + day["date"], date_value=day["date"], evidence=cell))
        if day["meets"] is True and reading["not_meeting"]:
            lesson["exceptions"].append(_exception(
                "calendar_conflict", "needs_decision", lesson,
                "The %s plan says the class doesn't meet, but the Weeks calendar has %s meeting on %s (%s)." % (
                    plan["day"], ", ".join(lesson["sections"]), day["date"], day["reason"]),
                "Decide which is right: correct the Weeks tab or write the lesson for %s." % day["date"], url=url,
                revision_key=reading["text"], detail="meets:" + day["date"], date_value=day["date"], evidence=cell))
        elif day["meets"] is False and reading["content"] and not reading["not_meeting"]:
            lesson["exceptions"].append(_exception(
                "calendar_conflict", "needs_decision", lesson,
                "There is a plan for %s %s, but %s don't meet that day (%s)." % (
                    plan["day"], day["date"], ", ".join(lesson["sections"]), day["reason"]),
                "Move this plan to a meeting day, or correct the Weeks tab.", url=url,
                revision_key=reading["text"], detail="off:" + day["date"], date_value=day["date"], evidence=cell))
        for claim in reading["dated"]:
            claimed = date.fromisoformat(claim["date"])
            calendar = _calendar_lookup(calendars, claimed)
            if not calendar or not claim["weekday_ok"]:
                continue
            closed = claim["date"] in calendar["closed"]
            friday = calendar["fridays"].get(claim["date"])
            wrong = None
            if claim["closed"] and not closed:
                wrong = "the Weeks calendar lists %s as a normal school day%s" % (
                    claim["date"], " (%s Friday)" % friday if friday else "")
            elif claim["friday"] and friday and claim["friday"] != friday and claimed.weekday() == 4:
                wrong = "the Weeks calendar calls %s a %s Friday" % (claim["date"], friday)
            if wrong:
                lesson["exceptions"].append(_exception(
                    "calendar_conflict", "needs_decision", lesson,
                    "The %s plan says “%s”, but %s." % (plan["day"], claim["clause"][:160], wrong),
                    "Check the move: correct the plan's note or the Weeks tab.", url=url,
                    revision_key=reading["text"], detail="claim:%s:%s" % (day["date"], claim["date"]),
                    date_value=claim["date"], evidence=cell))


def _link_checks(agenda, lesson, repairs):
    for item in link_findings(agenda, lesson["class_key"], [lesson["row"]]):
        plan = item.get("plan")
        if not plan:
            action = "Fix the %s link in the Agenda by hand." % item["role"]
        elif plan.get("source_check") == "exact_second_deck":
            action = ("Repair the placeholder in %s%d as “%s”, linked to its exact existing %s%d URL; "
                      "verify the Slides source before applying." % (
                          plan["column"], plan["row"], plan["label"], item["hidden_column"], plan["row"]))
        elif plan["kind"] == "linked_label":
            action = ("Use “Repair link” to link the label “%s” in %s%d to its hidden %s%d URL (the same text, "
                      "now clickable)." % (plan["label"], plan["column"], plan["row"], item["hidden_column"], plan["row"]))
        else:
            action = ("Use “Repair link” to restore %s in %s%d (the same formula the column uses in its other rows)."
                      % (plan["formula"], plan["column"], plan["row"]))
        lesson["exceptions"].append(_exception(
            item["kind"], "repairable" if plan else "needs_decision", lesson, item["reason"], action,
            url=item["source_url"], sources=[item["hidden_url"]], revision_key=item["visible_text"],
            detail="%s%d" % (item["column"], item["row"]),
            evidence={k: item.get(k) for k in ("column", "hidden_column", "visible_text", "hidden_url", "plan",
                                              "formula", "visible_url")}))
        lesson["exceptions"][-1]["fingerprint"] = item["fingerprint"]
    entry = agenda.classes[lesson["class_key"]]
    for ident, receipt in sorted(repairs.items()):
        class_key, row, column = ident.split(":")
        if class_key != lesson["class_key"] or int(row) != lesson["row"]:
            continue
        cell = entry["cells"].get((lesson["row"], _column_index(column)))
        if receipt.get("formula"):
            holds = _formula(cell).replace(" ", "") == receipt["formula"].replace(" ", "")
        else:  # a linked label: the same text, still linked to the same URL
            holds = (not _formula(cell) and text(cell).strip() == receipt.get("label")
                     and first_link(cell) == receipt.get("url"))
        if holds:
            lesson["repaired"].append(receipt)
            done = _exception("visible_link_not_clickable", "repaired_and_verified", lesson,
                              "The %s link in %s%d was restored and read back %s." % (
                                  receipt["role"], column, lesson["row"], receipt["verified_at"]),
                              "Nothing to do.", url=receipt["source_url"], blocking=False,
                              evidence={k: receipt.get(k) for k in ("formula", "label", "url") if receipt.get(k)})
            done["fingerprint"] = receipt["fingerprint"]
            lesson["exceptions"].append(done)


def _column_index(letters):
    number = 0
    for ch in letters:
        number = number * 26 + ord(ch) - 64
    return number


def _references(found, week_decks):
    """[(role, id, url)] from one lesson row's links."""
    refs = []
    roles = {"Teacher deck": "teacher_deck", "Student copy": "student_copy", "Lesson folder": "folder",
             "2nd deck": "second_deck", "Extra material": "extra_material"}
    for material in found["materials"]:
        ident = file_id(material["url"])
        if ident:
            refs.append((roles.get(material["label"], "extra_material"), ident, material["url"]))
    for form in found["forms"]:
        refs.append(("turn_in_form", form["form_id"] or "", form["url"]))
    return {"items": refs, "week_decks": sorted(i for i in week_decks if i)}


def _agenda_print_lines(found):
    lines = []
    for field in ("prep", "todo"):
        for segment in SEGMENT.split(str(found.get(field) or "")):
            match = re.match(r"^\W*(?:PRINT|COPIES|COPY)\s*:\s*(.+)$", segment.strip(), re.I)
            if match:
                lines += [{"text": item, "source": "Agenda %s cell" % field, "slide": None}
                          for item in _items(match.group(1))]
    return lines


def _supply_row(item, status, lesson, prep, source, url, sections, counts):
    amount = quantity(item, sections, counts)
    stated = amount["stated"]
    if stated and amount["per_section"]:
        shown = ", ".join("%s: %s" % (s, n if n is not None else "?") for s, n in amount["per_section"].items())
        text_quantity = "%s (%s)" % (shown, amount["basis"])
    else:
        lead = re.match(r"^(\d+(?:\.\d+)?\s*(?:bags?|boxes?|packs?|packets?|bottles?|cups?|dozen|lbs?|oz|g|kg|ml|l)?)\b", item)
        text_quantity = lead.group(1).strip() if lead else "quantity not stated"
    return {"item": item, "status": status, "quantity": text_quantity, "lesson": lesson["topic"],
            "class": lesson["class"], "class_key": lesson["class_key"], "week": lesson["week"],
            "activity": prep.get("activity", ""), "use_dates": list(prep.get("use_dates") or []),
            "prep_by": prep.get("prep_by"), "source": source, "source_url": url}


def _node(ctx, role, ident, url):
    meta = ctx.file(ident)
    node = {"id": ident, "roles": [role], "url": url, "access_evidence": ACCESS}
    if meta.get("error"):
        node.update(accessible=False, trashed=None, name="", mimeType="", revision="", problem=meta["error"],
                    http_status=meta.get("http_status"))
        return node
    node.update(accessible=True, trashed=bool(meta.get("trashed")), name=meta.get("name", ""),
                mimeType=meta.get("mimeType", ""), parents=meta.get("parents") or [],
                modifiedTime=meta.get("modifiedTime"), version=meta.get("version"), revision=revision(meta),
                _source_metadata=deepcopy(meta),
                url=_url(ident, meta.get("mimeType", "")) if role != "student_copy" else url)
    return node


def _resolve_sources(ctx, lesson):
    """Every source of one lesson as the Hub's own account sees it."""
    nodes = {}
    teacher = next((ident for role, ident, _ in lesson["refs"]["items"] if role == "teacher_deck"), "")
    excluded = lesson["week_policy"].get("excluded")
    for role, ident, url in lesson["refs"]["items"]:
        if role == "turn_in_form":
            continue
        if role == "student_copy" and ident == teacher:
            role = "student_copy_template"  # the /copy link makes copies of the teacher file itself
            if not re.search(r"/copy\b", url):
                lesson["exceptions"].append(_exception(
                    "student_link_not_copy", "needs_decision", lesson,
                    "The Student copy link opens the teacher deck itself rather than making a copy.",
                    "Use the deck's /copy link for students.", url=url, sources=[ident], blocking=False))
        if ident in nodes:
            nodes[ident]["roles"].append(role)
            continue
        nodes[ident] = _node(ctx, role, ident, url)
    for role, ident, url in lesson["refs"]["items"]:
        if role != "turn_in_form" or excluded:
            continue
        if not ident:
            lesson["exceptions"].append(_exception(
                "form_link_unreadable", "needs_decision", lesson,
                "This Turn-in link can't be checked (a published or forms.gle link).",
                "Put the Form's own editor link in the Agenda.", url=url, sources=[url]))
            continue
        info = ctx.form(ident)
        node = nodes.get(ident) or _node(ctx, "turn_in_form", ident, url)
        if ident in nodes and "turn_in_form" not in node["roles"]:
            node["roles"].append("turn_in_form")
        node.update(editor_id=ident, published_id=info.get("published_id", ""),
                    responder_url=info.get("responder_url", ""), title=info.get("title", ""))
        if info.get("error"):
            node.update(accessible=False, problem=info["error"], http_status=info.get("http_status"))
        nodes[ident] = node
    lesson["sources"] = list(nodes.values())
    if not any(set(n["roles"]) & {"teacher_deck", "second_deck", "folder"} for n in lesson["sources"]):
        lesson["exceptions"].append(_exception(
            "no_lesson_source", "needs_decision", lesson, "No teacher deck or lesson folder is linked for this lesson.",
            "Link the lesson's deck and folder in the Agenda.", url=lesson["agenda_url"], detail="no source"))
    for node in lesson["sources"]:
        label = "/".join(node["roles"]).replace("_", " ")
        if not node["accessible"]:
            lesson["exceptions"].append(_exception(
                "source_inaccessible", "inaccessible", lesson,
                "The Hub's account can't open the %s (%s)." % (label, node.get("problem") or "no access"),
                "Share it with the Hub's account, or fix the Agenda link.", url=node["url"], sources=[node["id"]],
                evidence={"http_status": node.get("http_status"), "access": ACCESS}))
        elif node["trashed"]:
            lesson["exceptions"].append(_exception(
                "source_trashed", "inaccessible", lesson, "The %s is in Drive's trash." % label,
                "Restore it, or link the current file in the Agenda.", url=node["url"], sources=[node["id"]],
                revision_key=node["revision"]))
    _folders(ctx, lesson)
    _name_lesson(ctx, lesson)


# ---------- lesson names ----------

# The weekly folder's name is the lesson's name; the Agenda topic may be the
# teacher's shorthand ("Fermentation" for "Cellular Respiration & Fermentation").
WEEKLY_PREFIX = re.compile(r"^\s*(?:(?:▶\s*)?LIVE\s*[—–-]\s*)?(?:(?:Cycle|Week|Wk|Unit)\s*0*\d+[a-z]?\b[\s—–\-:·|]*)+",
                           re.I)
UNIT_SUFFIX = re.compile(r"\s*\((?:Unit|Cycle|Week|Wk)\s*\d+[a-z]?\)\s*$", re.I)


def folder_lesson_name(name):
    """The lesson a weekly folder names: "Cycle 06 — Cellular Respiration &
    Fermentation (Unit 1)" -> "Cellular Respiration & Fermentation"; "" for a
    folder named only by its week ("Week 07")."""
    rest = UNIT_SUFFIX.sub("", WEEKLY_PREFIX.sub("", str(name or ""))).strip(" —–-:·|")
    return rest if re.search(r"[A-Za-z]{3}", rest) else ""


def lesson_name(folder_names, agenda_topic):
    """The lesson's name from its weekly folders, in order and without repeats
    ("History of Forensics + Crime Scene to Courtroom (Mock Trial)"), or the
    Agenda topic when no folder names a lesson."""
    names = []
    for name in folder_names:
        name = folder_lesson_name(name)
        if name and norm(name) not in {norm(n) for n in names}:
            names.append(name)
    return " + ".join(names) if names else str(agenda_topic or "").strip()


def _name_lesson(ctx, lesson):
    """Name the lesson by its linked lesson folder, then by the weekly folder of
    each linked deck kept elsewhere (a two-lesson week); keep the Agenda topic
    as agenda_topic. Only weekly folders ("Week/Wk/Cycle/Unit NN …") name a
    lesson, so a general folder ("03 BIOLOGY (Public)") never renames it."""
    folders = [n for n in lesson["sources"] if "folder" in n["roles"] and n.get("accessible") and not n.get("trashed")]
    seen = {n["id"] for n in folders}
    names = [n.get("name", "") for n in folders if WEEKLY_PREFIX.match(str(n.get("name") or ""))]
    for node in lesson["sources"]:
        if not set(node["roles"]) & {"teacher_deck", "second_deck"} or not node.get("accessible"):
            continue
        for parent in node.get("parents") or []:
            if parent in seen:
                continue
            seen.add(parent)
            meta = ctx.file(parent)
            if not meta.get("error") and WEEKLY_PREFIX.match(str(meta.get("name") or "")):
                names.append(meta["name"])
    lesson["agenda_topic"] = lesson["topic"]
    lesson["topic"] = lesson_name(names, lesson["topic"])
    for item in lesson["exceptions"]:
        item["lesson"] = lesson["topic"]
    for row in lesson["supply_rows"]:
        row["lesson"] = lesson["topic"]


def _folders(ctx, lesson):
    decks = [n for n in lesson["sources"] if set(n["roles"]) & {"teacher_deck", "second_deck"} and n["accessible"]]
    lesson["folder_files"] = []
    for node in lesson["sources"]:
        if "folder" not in node["roles"] or not node["accessible"] or node["trashed"]:
            continue
        if node["mimeType"] != google.FOLDER:
            lesson["exceptions"].append(_exception(
                "folder_not_folder", "needs_decision", lesson, "The Lesson folder link doesn't open a folder.",
                "Link the lesson's Drive folder in the Agenda.", url=node["url"], sources=[node["id"]]))
            continue
        listing = ctx.listing(node["id"])
        node["listing_complete"] = listing["complete"]
        node["children"] = len(listing["items"])
        if listing.get("error"):
            lesson["exceptions"].append(_exception(
                "source_inaccessible", "inaccessible", lesson,
                "The lesson folder's contents can't be listed (%s)." % listing["error"],
                "Share the folder with the Hub's account.", url=node["url"], sources=[node["id"]]))
            continue
        if not listing["complete"]:
            lesson["incomplete"] = True
            lesson["exceptions"].append(_exception(
                "folder_listing_incomplete", "needs_decision", lesson,
                "The lesson folder has more items than the Hub lists in one check (%d read)." % len(listing["items"]),
                "Move unused files out of the lesson folder, then run the check again.", url=node["url"],
                sources=[node["id"]], evidence={"items_read": len(listing["items"])}))
        members = {}
        for item in listing["items"]:
            target = item.get("target") or {}
            ident, mime = target.get("id") or item["id"], target.get("mimeType") or item.get("mimeType")
            members[ident] = {"id": ident, "name": item.get("name", ""), "mimeType": mime,
                              "shortcut": bool(target), "folder": node["id"], "student_work": _student_work(item)}
        for deck in decks:
            if node["id"] not in deck.get("parents", []) and deck["id"] not in members:
                lesson["exceptions"].append(_exception(
                    "deck_outside_folder", "needs_decision", lesson,
                    "The linked %s “%s” isn't inside the linked lesson folder." % (
                        "/".join(deck["roles"]).replace("_", " "), deck["name"]),
                    "Move the deck into the lesson folder, or link the right folder (report only).",
                    url=deck["url"], sources=[deck["id"], node["id"]], blocking=False))
        linked = set(lesson["refs"]["week_decks"])
        unlinked = sorted((m for m in members.values() if m["mimeType"] in DECKS and m["id"] not in linked),
                          key=lambda m: m["name"])
        if unlinked:
            lesson["exceptions"].append(_exception(
                "unlinked_deck_in_folder", "needs_decision", lesson,
                "The lesson folder holds deck(s) no Agenda row of %s links: %s." % (
                    lesson["week"], "; ".join(m["name"] for m in unlinked)),
                "Decide whether these belong to this lesson or another week; link or move them (report only).",
                url=node["url"], sources=[node["id"]] + [m["id"] for m in unlinked], blocking=False,
                evidence={"decks": [{"id": m["id"], "name": m["name"]} for m in unlinked]}))
        for member in members.values():
            if member["mimeType"] in PRINTABLE and not member["student_work"]:
                lesson["folder_files"].append(member)
    for node in lesson["sources"]:
        if node["accessible"] and not node["trashed"] and node["mimeType"] in PRINTABLE \
                and "extra_material" in node["roles"]:
            lesson["folder_files"].append({"id": node["id"], "name": node["name"], "mimeType": node["mimeType"],
                                           "shortcut": False, "folder": None, "agenda_extra_material": True})


def _student_work(item):
    """Student copies and feedback never go on a print list."""
    name = str(item.get("name") or "")
    return bool(roster.file_username(name) or re.search(r"\bfeedback\b", name, re.I)
                or (item.get("properties") or {}).get(google.PREFIX_TAG))


def _content(ctx, lesson, counts):
    """Deck codes and outline, then the print list, for one lesson."""
    summaries = []
    for node in lesson["sources"]:
        if not node["accessible"] or node["trashed"] or not _content_kinds(node):
            continue
        if "readiness" in _content_kinds(node):
            ready = ctx.check("readiness", node, _readiness)
            outline = ctx.check("outline", node, _outline)
            if ready is None or outline is None:
                lesson["pending"].append(node["id"])
                continue
            node["outline"] = {k: outline.get(k) for k in ("ok", "slide_count", "teacher_only_slides", "stage",
                                                            "http_status", "problem")}
            node["readiness"] = {k: ready.get(k) for k in ("status", "label", "missing_ids", "extra_ids",
                                                           "issues", "readability")}
            if outline.get("ok"):
                summaries.append((node, outline))
            else:
                lesson["exceptions"].append(_exception(
                    "deck_unreadable", "inaccessible" if outline.get("http_status") in (401, 403, 404) else "needs_decision",
                    lesson, "The %s couldn't be read at the %s stage: %s" % (
                        "/".join(node["roles"]).replace("_", " "), outline.get("stage"), outline.get("problem")),
                    "Check the deck's link and sharing, then run the check again.", url=node["url"],
                    sources=[node["id"]], revision_key=node["revision"], detail="outline",
                    evidence={"stage": outline.get("stage"), "http_status": outline.get("http_status")}))
            if ready.get("status") != "READY":
                slides_of = (outline or {}).get("code_slides") or {}
                readability = ready.get("readability")
                if readability:
                    lesson["exceptions"].append(_exception(
                        "deck_unreadable", "inaccessible" if readability.get("http_status") in (401, 403, 404)
                        or readability.get("stage") == "metadata" else "needs_decision", lesson,
                        "The %s's codes couldn't be checked at the %s stage: %s" % (
                            "/".join(node["roles"]).replace("_", " "), readability.get("stage"), ready.get("problem")),
                        "Check the deck's link and access, then run the check again.", url=node["url"],
                        sources=[node["id"]], revision_key=node["revision"], detail="readiness",
                        evidence={"readability": readability}))
                else:
                    missing, extra = ready.get("missing_ids") or [], ready.get("extra_ids") or []
                    slides = [1] if missing else []
                    slides += [n for ident in extra for n in slides_of.get(ident, [])]
                    detail = "; ".join(filter(None, [
                        "declared but not embedded: " + ", ".join(missing) if missing else "",
                        "embedded but not declared: " + ", ".join("%s (slide %s)" % (
                            ident, ", ".join(str(n) for n in slides_of.get(ident, [])) or "?") for ident in extra)
                        if extra else ""]))
                    lesson["exceptions"].append(_exception(
                        "needs_embedded_codes", "needs_decision", lesson,
                        completion.NEEDS_CODES + ": " + (detail or ready.get("problem") or "the code list needs checking."),
                        "Run the embed-coding program on this deck (or sync its inventory), preserving content; "
                        "then run the check again.", url=node["url"], sources=[node["id"]],
                        revision_key=node["revision"], detail="codes", slides=slides,
                        evidence={"issues": ready.get("issues"), "missing_ids": missing, "extra_ids": extra}))
    for node, outline in summaries:
        blocks = outline.get("blocks") or {}
        teacher = outline.get("teacher_only_slides") or []
        for line in blocks.get("print", []):
            lesson["print_lines"].append({"text": line["text"], "source": "deck “%s” slide %d" % (node["name"], line["slide"]),
                                          "slide": line["slide"], "deck_id": node["id"], "url": line.get("url")})
        lesson.setdefault("deck_links", {})[node["id"]] = outline.get("web_links") or []
        pages = outline.get("page_ids") or {}
        for mismatch in outline.get("link_mismatches") or []:
            page = pages.get(str(mismatch["slide"]))
            lesson["exceptions"].append(_exception(
                "deck_link_target_mismatch", "needs_decision", lesson,
                "Slide %d displays %s, but that hyperlink opens %s." % (
                    mismatch["slide"], mismatch["displayed_url"], mismatch["target_url"]),
                "Repair worker: verify the intended existing resource before correcting this slide's link; "
                "neither address is assumed correct.",
                url=node["url"] + ("#slide=id." + page if page and node["url"].endswith("/edit") else ""),
                sources=[node["id"]], revision_key=node["revision"],
                detail=content_hash(mismatch), slides=[mismatch["slide"]], evidence=mismatch))
        for note in outline.get("authoring_notes") or []:
            page = pages.get(str(note["slide"]))
            lesson["exceptions"].append(_exception(
                "authoring_note_on_student_slide", "needs_decision", lesson,
                "Slide %d of the %s is a slide students see, and it carries an authoring note: “%s”%s." % (
                    note["slide"], "/".join(node["roles"]).replace("_", " "), note["note"],
                    " (and %d more on that slide)" % (note["count"] - 1) if note["count"] > 1 else ""),
                "Remove or move this note to speaker notes.",
                url=node["url"] + ("#slide=id." + page if page and node["url"].endswith("/edit") else ""),
                sources=[node["id"]], revision_key=note["note"], detail="authoring:%d" % note["slide"],
                slides=[note["slide"]], evidence={"note": note["note"], "notes_on_slide": note["count"]}))
        week_text = _week_text(lesson["found"])
        for kind, status in (("buy", "buy"), ("put_out", "stock unknown")):
            for line in blocks.get(kind, []):
                items = deck_supply_items(line["text"], week_text, kind)
                for item in items:
                    lesson["supply_rows"].append(_supply_row(
                        item, status, lesson, {"use_dates": sorted({d for s in lesson["meeting_dates"].values() for d in s})},
                        "deck “%s” slide %d (%s)" % (node["name"], line["slide"], "BUY AHEAD" if kind == "buy" else "PUT OUT"),
                        node["url"], lesson["sections"], counts))
        lesson["teacher_prep"] += [{"text": line["text"], "slide": line["slide"], "deck": node["name"]}
                                   for line in blocks.get("prepare", [])]
        lesson.setdefault("teacher_only_slides", {})[node["id"]] = teacher
    _print_items(ctx, lesson, counts)


def _print_items(ctx, lesson, counts):
    files = lesson.get("folder_files", [])
    deck_links = lesson.get("deck_links", {})
    used = set()
    dates = sorted({d for s in lesson["meeting_dates"].values() for d in s})
    for line in lesson["print_lines"]:
        amount = quantity(line["text"], lesson["sections"], counts)
        best, score, ambiguous = match_file(WEB.sub(" ", line["text"]), files)
        # No folder file: a web page the line or its own deck's link list names is a print instruction.
        web = None if best else web_source(line, deck_links.get(line.get("deck_id"), []))
        teacher_only = bool(KEYWORDS.search(line["text"]) or (best and KEYWORDS.search(best["name"])))
        item = {"id": content_hash([lesson["lesson_id"], line["text"], line.get("slide")])[:24],
                "class_key": lesson["class_key"], "class": lesson["class"], "week": lesson["week"],
                "lesson": lesson["topic"], "row": lesson["row"], "text": line["text"], "source": line["source"],
                "slide": line.get("slide"), "dates": dates, "optional": amount["optional"], "basis": amount["basis"],
                "per_section": amount["per_section"], "teacher_only": teacher_only, "file": None,
                "match_score": score}
        if best and not ambiguous:
            used.add(best["id"])
            item["file"] = _print_file(ctx, lesson, best)
        elif web:
            item.update(web=web, instruction="print from web: " + web["url"])
        elif not amount["optional"] or ambiguous:
            reason = ("“%s” matches more than one folder file equally." % line["text"]) if ambiguous else \
                "Print item “%s” (%s) has no matching file in the lesson folder." % (line["text"], line["source"])
            lesson["exceptions"].append(_exception(
                "print_item_ambiguous" if ambiguous else "print_item_no_file", "needs_decision", lesson, reason,
                "Add the handout to the lesson folder with a matching name, or edit the print list.",
                url=lesson["agenda_url"] if not line.get("deck_id") else
                "https://docs.google.com/presentation/d/%s/edit" % line["deck_id"],
                sources=[line.get("deck_id") or lesson["lesson_id"]], revision_key=line["text"],
                detail=line["text"], slides=[line["slide"]] if line.get("slide") else (),
                blocking=not amount["optional"]))
        lesson["print_items"].append(item)
    lesson["unlisted_files"] = [{"id": f["id"], "name": f["name"], "mimeType": f["mimeType"]}
                                for f in files if f["id"] not in used]


def _print_file(ctx, lesson, member):
    meta = ctx.file(member["id"])
    node = {"id": member["id"], "name": member["name"], "mimeType": member["mimeType"], "roles": ["print_file"],
            "print_candidate": True, "url": _url(member["id"], member["mimeType"]), "access_evidence": ACCESS}
    if meta.get("error") or meta.get("trashed"):
        lesson["exceptions"].append(_exception(
            "source_inaccessible", "inaccessible", lesson,
            "The print file “%s” can't be opened by the Hub's account%s." % (
                member["name"], " (in trash)" if meta.get("trashed") else ""),
            "Share or restore the file.", url=node["url"], sources=[member["id"]]))
        return {**node, "pages": None, "sides": "unknown", "revision": ""}
    node.update(revision=revision(meta), version=meta.get("version"))
    if member["mimeType"] == DOCX:
        # The Hub can't render Word files; the packet lists it to print from
        # the folder instead. That is a print instruction, not a decision.
        return {**node, "pages": None, "sides": "unknown", "print_from_folder": True}
    pages = ctx.check("pages", node, _pages)
    if pages is None:
        lesson["pending"].append(member["id"])
        return {**node, "pages": None, "sides": "unknown"}
    if not pages.get("ok"):
        lesson["exceptions"].append(_exception(
            "print_file_unreadable", "needs_decision", lesson,
            "“%s” couldn't be read at the %s stage: %s" % (member["name"], pages.get("stage"), pages.get("problem")),
            "Check the file, then run the check again.", url=node["url"], sources=[member["id"]],
            revision_key=node["revision"]))
        return {**node, "pages": None, "sides": "unknown"}
    return {**node, "pages": pages["pages"], "sides": sides(pages["pages"])}


def lesson_status(lesson):
    open_ = [e for e in lesson["exceptions"] if e["status"] != "repaired_and_verified" and e["blocking"]]
    if any(e["status"] == "inaccessible" for e in open_):
        return "inaccessible"
    if lesson.get("pending") or lesson.get("incomplete"):
        return "incomplete"
    if open_:
        return "needs_decision"
    if lesson.get("repaired"):
        return "repaired_and_verified"
    return "ready"


def _finish_week(week_plan, sheet, port):
    previous_id = week_id(sheet, week_plan["monday"], "latest")
    previous = port.read([previous_id]).documents[previous_id]
    seen = {e["fingerprint"]: e.get("first_seen") for e in (previous.value.get("exceptions", []) if previous else [])}
    checked_at = now()
    lessons, exceptions, print_items, supply_rows = [], list(week_plan["week_exceptions"]), [], []
    for lesson in week_plan["lessons"]:
        lesson["status"] = lesson_status(lesson)
        exceptions += lesson["exceptions"]
        print_items += lesson["print_items"]
        supply_rows += lesson["supply_rows"]
        lessons.append({field: lesson.get(field) for field in (
            "lesson_id", "class_key", "class", "sections", "week", "monday", "row", "topic", "agenda_topic",
            "agenda_url", "days", "meeting_dates", "status", "teacher_prep", "unlisted_files", "teacher_only_slides", "pending")}
            | {"week_policy": {k: lesson["week_policy"].get(k) for k in ("excluded", "reason", "problem")},
               "sources": [_compact_node(n) for n in lesson["sources"]],
               "exceptions": [e["fingerprint"] for e in lesson["exceptions"]],
               "print_items": [p["id"] for p in lesson["print_items"]]})
    unique = {}
    for item in exceptions:
        item["first_seen"] = seen.get(item["fingerprint"]) or checked_at
        unique.setdefault(item["fingerprint"], item)
    incomplete = not week_plan["label"] or any(l["status"] == "incomplete" for l in lessons)
    blocking = any(e["blocking"] and e["status"] != "repaired_and_verified" for e in unique.values())
    record = {"schema": SCHEMA, "rules": RULES, "agenda_id": sheet, "monday": week_plan["monday"],
              "label": week_plan["label"], "calendar": week_plan["calendar"], "problems": week_plan["problems"],
              "status": "incomplete" if incomplete else ("needs_decision" if blocking else "ready"),
              "checked_at": checked_at, "lessons": lessons, "exceptions": list(unique.values()),
              "print_items": print_items, "supply_rows": supply_rows}
    record = _bounded(record)
    _put(port, previous_id, record, previous)
    if record["status"] != "incomplete":
        complete_id = week_id(sheet, week_plan["monday"], "complete")
        _put(port, complete_id, record, port.read([complete_id]).documents[complete_id])
    return record


def _compact_node(node):
    keep = ("id", "roles", "name", "mimeType", "trashed", "parents", "modifiedTime", "version", "revision", "url",
            "accessible", "access_evidence", "http_status", "problem", "published_id", "responder_url", "editor_id",
            "title", "listing_complete", "children", "readiness", "outline")
    return {field: node[field] for field in keep if field in node}


def _bounded(record):
    """Keep one week within the private store's record size: drop evidence first."""
    try:
        bounded_json(record)
        return record
    except ValueError:
        pass
    for item in record["exceptions"]:
        item["evidence"] = {}
    for lesson in record["lessons"]:
        for node in lesson["sources"]:
            node.pop("parents", None)
    bounded_json(record)
    return record


def compose(weeks, monday, count, *, run_counts=None, last_successful=None):
    """The combined reply for the selected weeks."""
    weeks = [w for w in weeks if w]
    exceptions = [e for w in weeks for e in w["exceptions"]]
    supply_rows = [r for w in weeks for r in w["supply_rows"]]
    statuses = [w["status"] for w in weeks]
    if len(weeks) < count or "incomplete" in statuses:
        status = "incomplete"
    elif "needs_decision" in statuses:
        status = "needs_decision"
    else:
        status = "ready"
    lessons = [l for w in weeks for l in w["lessons"]]
    return {"schema": SCHEMA, "start": monday.isoformat() if isinstance(monday, date) else monday, "weeks_requested": count,
            "weeks": [{"label": w["label"], "monday": w["monday"], "status": w["status"], "checked_at": w["checked_at"],
                       "calendar": w["calendar"], "problems": w["problems"]} for w in weeks],
            "lessons": lessons, "exceptions": exceptions,
            "print_items": [p for w in weeks for p in w["print_items"]],
            "supplies": merge_supplies(supply_rows),
            "coverage": {"weeks_checked": len(weeks), "lessons": len(lessons),
                         "lesson_status": dict(Counter(l["status"] for l in lessons)),
                         "unresolved": sum(e["status"] != "repaired_and_verified" for e in exceptions),
                         "blocking": sum(e["blocking"] and e["status"] != "repaired_and_verified" for e in exceptions),
                         "run": run_counts or {}, "access_evidence": ACCESS,
                         "excluded_weeks": [{"class": l["class"], "week": l["week"], "reason": l["week_policy"]["reason"]}
                                            for l in lessons if l["week_policy"].get("excluded")]},
            "checked_at": min((w["checked_at"] for w in weeks), default=None),
            "last_successful_check": last_successful, "status": status,
            "model_calls": 0, "external_writes": 0}


def status(config, spreadsheet_id, start, weeks, *, port=None):
    """The stored result for the selected weeks; reads only, no source call."""
    monday, weeks = monday_of(start), week_count(weeks)
    port = port if port is not None else configured_store(config)
    mondays = [(monday + timedelta(days=7 * i)).isoformat() for i in range(weeks)]
    ids = [week_id(spreadsheet_id, m, which) for m in mondays for which in ("latest", "complete")]
    found = _read_many(port, ids)
    latest = [found[week_id(spreadsheet_id, m, "latest")] for m in mondays]
    complete = [found[week_id(spreadsheet_id, m, "complete")] for m in mondays]
    successful = [doc.value["checked_at"] for doc in complete if doc]
    reply = compose([doc.value if doc else None for doc in latest], monday, weeks,
                    last_successful=min(successful) if len(successful) == weeks else None)
    progress = port.read([run_id(spreadsheet_id, monday.isoformat(), weeks)]).documents[
        run_id(spreadsheet_id, monday.isoformat(), weeks)]
    reply["run"] = progress.value if progress else None
    if not any(latest):
        reply["status"] = "not_checked"
    attach_reviews(port, reply)
    return reply


# ---------- reviewed records ----------

REVIEW_KINDS = {"meeting_flow": "90-minute meeting flow", "vt_verdict": "VT critical aspect and sequence",
                "print_decision": "Print or digital alternative", "visual_check": "Visual check of edited slides"}
REVIEW_SCHEMA = "hub-weekly-preflight-review/v1"


def review_scope(kind, class_key, week, row, day=""):
    return {"kind": kind, "class_key": class_key, "week": week, "row": row, "date": day or ""}


def review_id(scope):
    return key("weeklypreflightreview", scope)


def save_review(config, spreadsheet_id, payload, actor, *, port=None):
    """Save one person's reviewed decision, bound to the exact source revisions
    the latest check saw for that lesson. An identical save changes nothing."""
    if not isinstance(payload, dict) or set(payload) - {"kind", "class", "week", "row", "date", "sources", "text",
                                                         "verdict"}:
        raise Held("That review wasn't understood.")
    kind, class_key, week, row = payload.get("kind"), payload.get("class"), payload.get("week"), payload.get("row")
    day, body, verdict = payload.get("date") or "", payload.get("text"), payload.get("verdict") or ""
    sources = payload.get("sources")
    if (kind not in REVIEW_KINDS or class_key not in dict(agenda_module.CLASSES) or not isinstance(week, str)
            or not 1 <= len(week) <= 100 or not isinstance(row, int) or isinstance(row, bool)
            or not 1 <= row <= agenda_module.LAST_ROW or not isinstance(body, str) or not 1 <= len(body.strip()) <= 6000
            or verdict not in ("", "ok", "needs_change", "print", "digital", "both")
            or (day and (not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day)))
            or not isinstance(sources, list) or not 1 <= len(sources) <= 12
            or any(not isinstance(s, dict) or set(s) != {"id", "revision"} or not isinstance(s["id"], str)
                   or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", s["id"]) or not isinstance(s["revision"], str)
                   or len(s["revision"]) > 200 for s in sources)
            or not isinstance(actor, str) or not actor):
        raise Held("A review needs its kind, lesson, the sources it reviewed and the reviewed text.")
    port = port if port is not None else configured_store(config)
    stored = [doc.value for doc in _read_many(port, [week_id(spreadsheet_id, m, "latest") for m in
                                                     _mondays_for(port, spreadsheet_id, week)]).values() if doc]
    lesson = next((l for record in stored for l in record["lessons"]
                   if l["class_key"] == class_key and l["week"] == week and l["row"] == row), None)
    if not lesson:
        raise Held("Run the week's check first; that lesson isn't in the latest check.")
    current = {n["id"]: n.get("revision") for n in lesson["sources"]}
    if any(current.get(s["id"]) != s["revision"] for s in sources):
        raise Held("A source changed since the check you reviewed. Run the check again and review the current version.")
    if day and day not in {d for dates in lesson["meeting_dates"].values() for d in dates}:
        raise Held("That date isn't one of this lesson's meeting dates.")
    scope = review_scope(kind, class_key, week, row, day)
    ident = review_id(scope)
    entry = {"text": body.strip(), "verdict": verdict, "sources": sorted(sources, key=lambda s: s["id"]),
             "reviewer": actor.lower()}
    for _ in range(3):
        old = port.read([ident]).documents[ident]
        value = dict(old.value) if old else {"schema": REVIEW_SCHEMA, "scope": scope, "history": []}
        if value.get("current") and {k: value["current"][k] for k in entry} == entry:
            return {**value, "changed": False}
        if value.get("current"):
            value["history"] = (value["history"] + [value["current"]])[-20:]
        value["current"] = {**entry, "saved_at": now()}
        try:
            _put(port, ident, value, old)
            return {**value, "changed": True}
        except Conflict:
            continue
    raise StoreUnavailable("weekly_preflight_review_unconfirmed")


def _mondays_for(port, spreadsheet_id, week):
    """Week records are keyed by Monday; a review names the week label, so look
    the label up in the Agenda-independent run index (the latest check)."""
    index = port.read([key("weeklypreflightlabels", spreadsheet_id)]).documents[key("weeklypreflightlabels", spreadsheet_id)]
    monday = (index.value.get("labels", {}) if index else {}).get(week)
    return [monday] if monday else []


def remember_labels(port, spreadsheet_id, weeks):
    ident = key("weeklypreflightlabels", spreadsheet_id)
    for _ in range(3):
        old = port.read([ident]).documents[ident]
        labels = dict((old.value if old else {}).get("labels", {}))
        new = {w["label"]: w["monday"] for w in weeks if w.get("label")}
        if all(labels.get(k) == v for k, v in new.items()):
            return
        labels.update(new)
        try:
            _put(port, ident, {"schema": SCHEMA, "labels": dict(sorted(labels.items())[-300:])}, old)
            return
        except Conflict:
            continue


def review_states(port, lessons):
    """{lesson_id: {kind or kind:date: {"state", "text", ...}}}: verified when
    the bound revisions still match, needs re-review when a source changed."""
    scopes = {}
    for lesson in lessons:
        dates = sorted({d for s in lesson["meeting_dates"].values() for d in s})
        for kind in REVIEW_KINDS:
            for day in (dates if kind == "meeting_flow" else [""]):
                scopes[review_id(review_scope(kind, lesson["class_key"], lesson["week"], lesson["row"], day))] = (
                    lesson, kind, day)
    found = _read_many(port, list(scopes))
    out = {}
    for ident, (lesson, kind, day) in scopes.items():
        doc = found.get(ident)
        slot = out.setdefault(lesson["lesson_id"], {})
        name = kind + (":" + day if day else "")
        if not doc or not doc.value.get("current"):
            slot[name] = {"state": "not_reviewed"}
            continue
        current = {n["id"]: n.get("revision") for n in lesson["sources"]}
        record = doc.value["current"]
        changed = [s["id"] for s in record["sources"] if current.get(s["id"]) != s["revision"]]
        slot[name] = {"state": "needs_re_review" if changed else "verified", "text": record["text"],
                      "verdict": record.get("verdict", ""), "reviewer": record.get("reviewer"),
                      "saved_at": record.get("saved_at"), "changed_sources": changed,
                      "sources": record["sources"]}
    return out


def attach_reviews(port, reply):
    states = review_states(port, reply["lessons"]) if reply["lessons"] else {}
    for lesson in reply["lessons"]:
        lesson["reviews"] = states.get(lesson["lesson_id"], {})
    return reply
