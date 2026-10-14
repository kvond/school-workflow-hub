"""Saving a lesson from the dashboard back to the Agenda, and moving lessons
between weeks.

The teacher edits a lesson on the Lesson plans page and chooses Save to
Agenda. The page sends each changed field with what it started from; the
server re-reads the Agenda, checks the row still holds that week, and writes
a field only when its cell still says what the page started from. A cell
someone changed in the Agenda meanwhile is returned for the teacher to look
at, and nothing in that save is written. Text is written as plain text; a
formula cell is never written. Links (deck, activity, Turn-in Forms) are
written as plain URLs or as a linked label, never as formulas. Each written
cell is read back. Sheets has no conditional write, so an edit made in the
Agenda in the second between the check and the write could still be replaced.
"""
import random
import re

from . import google
from .agenda import build_lesson, field_columns, first_link, form_link, lesson_kind, manual_material_links, norm, plain, text

MAX_TEXT = 10000
MAX_FIELDS = 20
MAX_NAME = 120
# The links Edit here can change: {field: the Agenda column that holds it}.
LINKS = {"deck": "teacher_url", "deck2": "second_deck", "activity": "extra_url", "turnin": "turnin_url",
         "turnin2": "turnin2"}
NAMED = ("turnin", "turnin2")  # the links that carry a name students and IC see
DECK = re.compile(r"https://(?:docs\.google\.com/presentation|drive\.google\.com/file)/d/([A-Za-z0-9_-]{20,})")
DRIVE = re.compile(r"https://(?:docs|drive)\.google\.com/\S+$")
REF = re.compile(r"(?<![A-Za-z_])(\$?)([A-Z]{1,3})(\$?)(\d+)(?![\d(])")


class Changed(Exception):
    """The Agenda changed under the page: {field: what the cell says now}, or a row that moved."""
    def __init__(self, current=None, message=""):
        super().__init__(message)
        self.current = current or {}


class Refused(ValueError):
    """A request the teacher can put right; the message says how."""


def column_letter(col):
    """1 → 'A', 27 → 'AA'."""
    letters = ""
    while col:
        col, rest = divmod(col - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def clean(value):
    return "\n".join(line.rstrip() for line in str(value).replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip()


def _formula(cells, row, col):
    return ((cells.get((row, col)) or {}).get("userEnteredValue") or {}).get("formulaValue") or ""


def _derived(cells, row, col):
    """A formula built from other cells (e.g. the student /copy link from the
    teacher link): the Agenda fills it in, so it's never written."""
    formula = _formula(cells, row, col)
    return bool(formula) and "http" not in formula


def normal_link(key, url):
    """The link to keep for a field, or '' to clear it. Refused when it can't be used."""
    url = str(url or "").strip()
    if not url:
        return ""
    if key in ("deck", "deck2"):
        match = DECK.match(url)
        if not match:
            raise Refused("The deck link should be a Google Slides link (docs.google.com/presentation/d/…).")
        return "https://docs.google.com/presentation/d/%s/edit" % match.group(1)
    if key in ("turnin", "turnin2"):
        form = form_link(url)
        if not form or not form["form_id"]:
            raise Refused("Use the Form's own link: open the Form's editor and copy its address "
                          "(docs.google.com/forms/d/…). Published and forms.gle links can't be read.")
        return "https://docs.google.com/forms/d/%s/viewform" % form["form_id"]
    if not DRIVE.match(url):
        raise Refused("The activity link should be a Google Drive link.")
    return url


def _plain_cell(value):
    return {"userEnteredValue": {"stringValue": value}} if value else {}


def _linked_cell(label, url):
    return ({"userEnteredValue": {"stringValue": label},
             "textFormatRuns": [{"startIndex": 0, "format": {"link": {"uri": url}}}]} if url else {})


def _link_cells(cells, cols, row, key, url, topic, name=""):
    """[(column, new cell, link expected there)] for one link field. name is
    a Turn-in Form's label, when the teacher gave one."""
    own = cols[LINKS[key]]
    if _formula(cells, row, own):
        raise Refused("That link's cell in the Agenda is a formula, so change it in the Agenda.")

    def visible_material(column, wanted):
        shown = cols.get(column)
        cell = cells.get((row, shown)) if shown else None
        manual = manual_material_links(cell)
        if not shown or shown == own or not manual:
            return []
        if len({link for link, _ in manual}) > 1:
            raise Refused("That visible cell contains multiple material links, so change it in the Agenda.")
        if _formula(cells, row, shown):
            raise Refused("That visible link is an explicit formula, so change it in the Agenda.")
        label = text(cell).strip()
        new = _plain_cell(wanted) if label.startswith("https://") else _linked_cell(label, wanted)
        # Replace only this link's URI, retaining the cell's other formatting.
        new["userEnteredFormat"] = {"textFormat": {"link": {"uri": wanted}}} if wanted else {}
        return [(shown, new, wanted)]

    if key == "deck":
        out = [(own, _plain_cell(url), url)]
        copy = url[:-len("/edit")] + "/copy" if url else ""
        student = cols.get("student_url")
        if student and not _derived(cells, row, student):
            out.append((student, _plain_cell(copy), copy))
        out += visible_material("teacher_deck", url)
        out += visible_material("student_deck", copy)
        return out
    if key in ("activity", "deck2"):
        return [(own, _plain_cell(url), url)] + (
            visible_material("second_deck_display", url) if key == "deck2" else [])
    name = " ".join(str(name or "").split()).lstrip("📤 ").strip()[:MAX_NAME]
    if key == "turnin":
        out = [(own, _plain_cell(url), url)]
        shown = cols.get("turnin")
        if shown and not _derived(cells, row, shown):
            label = "📤 %s" % name if name else "📤 %s — Turn in" % topic if topic else "📤 Turn in"
            out.append((shown, _linked_cell(label, url), url))
        return out
    return [(own, _linked_cell("📤 %s" % name if name else "📤 2nd turn in", url), url)]


def _cell_range(sheet_id, row, col):
    return {"sheetId": sheet_id, "startRowIndex": row - 1, "endRowIndex": row,
            "startColumnIndex": col - 1, "endColumnIndex": col}


def _write_error(config, error):
    if error.status in (401, 403):
        return google.GoogleError("The dashboard can't write to the Agenda. Share the Agenda spreadsheet with %s "
                                  "as an Editor." % google.service_account(config), error.status)
    return error


def save(token, write_token, config, agenda, class_key, week, row, changes):
    """Write the changed fields of one lesson row: {"saved": [...], "unconfirmed": [...]}."""
    entry = agenda.classes[class_key]
    layout = entry["layout"]
    if not layout or not entry.get("tab"):
        raise ValueError("class")
    label = dict(layout["rows"]).get(row)
    if label is None or norm(label) != norm(week):
        raise Changed(message="This lesson's row moved in the Agenda. Reload the page and try again.")
    columns, cols, cells = field_columns(layout), layout["columns"], entry["cells"]
    asked = {key[5:]: change for key, change in changes.items() if key.startswith("link:")}
    texts = {key: change for key, change in changes.items() if not key.startswith("link:")}
    if (not changes or len(changes) > MAX_FIELDS or any(key not in columns for key in texts)
            or any(key not in LINKS or not cols.get(LINKS[key]) for key in asked)):
        raise ValueError("fields")
    updates, conflicts = [], {}
    for key, change in texts.items():
        cell = entry["cells"].get((row, columns[key]))
        if not plain(cell):
            raise ValueError("That cell has a link or formula, so it can only be edited in the Agenda.")
        current = text(cell).strip()
        if clean(change["from"]) != clean(current):
            conflicts[key] = current
        elif clean(change["to"]) != clean(current):
            updates.append((key, columns[key], clean(change["to"])))
    lesson = build_lesson(cells, layout, row, "")
    topic = next((value for key, _, value in updates if key == "topic"), lesson["topic"])
    links = []  # (field, column, new cell, link expected there)
    for key, change in asked.items():
        current = lesson["links"].get(key, "")
        name = change.get("label")
        if name is not None and (key not in NAMED or not isinstance(name, str) or len(name) > MAX_NAME):
            raise ValueError("label")
        renamed = name is not None and name.strip() != lesson.get("link_labels", {}).get(key, "")
        if change["from"].strip() != current:
            conflicts["link:" + key] = current
        elif change["to"].strip() != current or (renamed and change["to"].strip()):
            wanted = normal_link(key, change["to"])
            links += [("link:" + key, col, new_cell, expect)
                      for col, new_cell, expect in _link_cells(cells, cols, row, key, wanted, topic, name)]
    if conflicts:
        raise Changed(conflicts, "Some of this changed in the Agenda since you opened it.")
    if not updates and not links:
        return {"saved": [], "unconfirmed": []}
    tab = "'%s'" % entry["tab"].replace("'", "''")
    ranges = ["%s!%s%d" % (tab, column_letter(col), row) for _, col, _ in updates]
    try:
        if updates:
            google.update_cells(write_token, agenda.spreadsheet_id,
                                [(where, value) for where, (_, _, value) in zip(ranges, updates)])
        if links:
            google.batch_update(write_token, agenda.spreadsheet_id, [
                {"updateCells": {"range": _cell_range(entry.get("sheet_id", 0), row, col), "rows": [{"values": [new_cell]}],
                                 "fields": "userEnteredValue,textFormatRuns" + (
                                     ",userEnteredFormat.textFormat.link" if "userEnteredFormat" in new_cell else "")}}
                for _, col, new_cell, _ in links])
    except google.GoogleError as error:
        raise _write_error(config, error) from None
    ranges += ["%s!%s%d" % (tab, column_letter(col), row) for _, col, _, _ in links]
    after = google.sheet_grid(token, agenda.spreadsheet_id, ranges).get(entry["tab"], {})
    unconfirmed = [key for key, col, value in updates if clean(text(after.get((row, col)))) != value]
    unconfirmed += [key for key, col, _, expect in links
                    if (first_link(after.get((row, col))) if expect else text(after.get((row, col))).strip()) != expect]
    unconfirmed = list(dict.fromkeys(unconfirmed))
    saved = list(dict.fromkeys([key for key, _, _ in updates] + [key for key, _, _, _ in links]))
    return {"saved": [key for key in saved if key not in unconfirmed], "unconfirmed": unconfirmed}


def _points_elsewhere(formula, row):
    """Whether a formula reads a cell outside its own row (another row, a
    fixed row or another tab), which a move would leave pointing at the old place."""
    bare = re.sub(r'"[^"]*"', '""', formula)
    if "!" in bare:
        return True
    return any(fixed or int(number) != row for _, _, fixed, number in REF.findall(bare))


def _runs(columns):
    """[(first, last)] for each run of consecutive columns."""
    runs = []
    for col in sorted(columns):
        if runs and col == runs[-1][1] + 1:
            runs[-1][1] = col
        else:
            runs.append([col, col])
    return [tuple(run) for run in runs]


def _copy(from_sheet, from_row, to_sheet, to_row, first, last):
    span = {"startColumnIndex": first - 1, "endColumnIndex": last}
    return {"copyPaste": {"source": {"sheetId": from_sheet, "startRowIndex": from_row - 1, "endRowIndex": from_row, **span},
                          "destination": {"sheetId": to_sheet, "startRowIndex": to_row - 1, "endRowIndex": to_row, **span},
                          "pasteType": "PASTE_NORMAL", "pasteOrientation": "NORMAL"}}


def move(token, write_token, config, agenda, class_key, week, row, to_week):
    """Move a lesson to another week the way items move in a list: the lessons
    in between slide one week toward the week it left, skipping weeks with no
    class or nothing planned. Only the lesson's cells move (topic, plans,
    notes, links, Forms); every row keeps its Wk label and Dates, so the
    Agenda stays in week order. Into a week with nothing planned, the lesson
    just moves there. Done in one Sheets request, through a hidden scratch
    tab that is removed in the same request."""
    entry = agenda.classes[class_key]
    layout = entry["layout"]
    if not layout or not entry.get("tab"):
        raise ValueError("class")
    labels = dict(layout["rows"])
    label = labels.get(row)
    if label is None or norm(label) != norm(week):
        raise Changed(message="This lesson's row moved in the Agenda. Reload the page and try again.")
    if norm(to_week) == norm(week):
        raise ValueError("week")
    cols, cells = layout["columns"], entry["cells"]
    topic_col = cols.get("topic")
    if not topic_col:
        raise Refused("This Agenda tab has no Topic column, so lessons can't be moved from here.")
    topic = lambda r: text(cells.get((r, topic_col))).strip()
    kind = lambda r: lesson_kind(topic(r))
    if kind(row) != "lesson":
        raise Refused("There's no lesson in %s to move." % label)
    target = next((r for r, other in layout["rows"] if norm(other) == norm(to_week)), None)
    if target is None:
        raise Refused("%s has no row in the Agenda." % to_week)
    if kind(target) == "closed":
        raise Refused("%s has no class, so a lesson can't move there." % labels[target])
    order = [r for r, _ in layout["rows"]]
    here, there = order.index(row), order.index(target)
    step = 1 if there > here else -1
    between = order[here + step:there:step]
    chain = [row, target] if kind(target) == "free" else [row] + [r for r in between if kind(r) == "lesson"] + [target]
    # The lesson goes to the target; each other one takes the place of the one before it.
    moves = [(row, target)] + [(chain[k], chain[k - 1]) for k in range(1, len(chain))]
    used = [c for key, c in cols.items() if c and key not in ("week", "dates")]
    used += [c for day in layout["plans"].values() for c in day.values() if c]
    content = set(range(min(used), max(used) + 1)) - {cols.get("week"), cols.get("dates")}
    for r in chain:
        for c in sorted(content):
            formula = _formula(cells, r, c)
            if formula and _points_elsewhere(formula, r):
                raise Refused("A formula in %s (cell %s%d) reads another row or tab, so this move has to be "
                              "made in the Agenda." % (labels[r], column_letter(c), r))
    sheet_id, scratch = entry.get("sheet_id", 0), random.randint(10 ** 8, 2 * 10 ** 9)
    runs = _runs(content)
    requests = [{"addSheet": {"properties": {"sheetId": scratch, "title": "_hub_move_%d" % scratch, "hidden": True,
                                             "gridProperties": {"rowCount": len(chain), "columnCount": max(content)}}}}]
    requests += [_copy(sheet_id, r, scratch, k + 1, first, last) for k, r in enumerate(chain) for first, last in runs]
    requests += [_copy(scratch, chain.index(src) + 1, sheet_id, dst, first, last)
                 for src, dst in moves for first, last in runs]
    placeholder = kind(target) == "free" and topic(target)
    if placeholder:  # an [EMPTY …] note belongs to its week, not to the one the lesson left
        requests.append({"updateCells": {"range": _cell_range(sheet_id, row, topic_col), "rows": [{"values": [{}]}],
                                         "fields": "userEnteredValue,textFormatRuns"}})
    requests.append({"deleteSheet": {"sheetId": scratch}})
    try:
        google.batch_update(write_token, agenda.spreadsheet_id, requests)
    except google.GoogleError as error:
        raise _write_error(config, error) from None
    tab = "'%s'" % entry["tab"].replace("'", "''")
    after = google.sheet_grid(token, agenda.spreadsheet_id,
                              ["%s!%s%d" % (tab, column_letter(topic_col), r) for r in chain]).get(entry["tab"], {})
    expected = {dst: ("" if placeholder and src == target else topic(src)) for src, dst in moves}
    unconfirmed = [labels[r] for r, want in expected.items() if clean(text(after.get((r, topic_col)))) != clean(want)]
    return {"moved": {"from": label, "to": labels[target], "topic": topic(row)},
            "shifted": [{"topic": topic(src), "from": labels[src], "to": labels[dst]}
                        for src, dst in moves[1:] if kind(src) == "lesson"],
            "skipped": [labels[r] for r in between if kind(r) != "lesson"] if kind(target) == "lesson" else [],
            "unconfirmed": unconfirmed}
