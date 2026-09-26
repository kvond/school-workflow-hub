"""Saving a lesson's plain-text cells from the dashboard back to the Agenda.

The teacher edits a lesson on the Lesson plans page and chooses Save to
Agenda. The page sends each changed field with the text it started from; the
server re-reads the Agenda, checks the row still holds that week, and writes
a field only when its cell still says what the page started from. A cell
someone changed in the Agenda meanwhile is returned for the teacher to look
at, and nothing in that save is written. Cells with links or formulas are never
written, and text is written as plain text. Each written cell is read back.
Sheets has no conditional write, so an edit made in the Agenda in the second
between the check and the write could still be replaced.
"""
from . import google
from .agenda import field_columns, norm, plain, text

MAX_TEXT = 10000
MAX_FIELDS = 20


class Changed(Exception):
    """The Agenda changed under the page: {field: what the cell says now}, or a row that moved."""
    def __init__(self, current=None, message=""):
        super().__init__(message)
        self.current = current or {}


def column_letter(col):
    """1 → 'A', 27 → 'AA'."""
    letters = ""
    while col:
        col, rest = divmod(col - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def clean(value):
    return "\n".join(line.rstrip() for line in str(value).replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip()


def save(token, write_token, config, agenda, class_key, week, row, changes):
    """Write the changed fields of one lesson row: {"saved": [...], "unconfirmed": [...]}."""
    entry = agenda.classes[class_key]
    layout = entry["layout"]
    if not layout or not entry.get("tab"):
        raise ValueError("class")
    label = dict(layout["rows"]).get(row)
    if label is None or norm(label) != norm(week):
        raise Changed(message="This lesson's row moved in the Agenda. Reload the page and try again.")
    columns = field_columns(layout)
    if not changes or len(changes) > MAX_FIELDS or any(key not in columns for key in changes):
        raise ValueError("fields")
    updates, conflicts = [], {}
    for key, change in changes.items():
        cell = entry["cells"].get((row, columns[key]))
        if not plain(cell):
            raise ValueError("That cell has a link or formula, so it can only be edited in the Agenda.")
        current = text(cell).strip()
        if clean(change["from"]) != clean(current):
            conflicts[key] = current
        elif clean(change["to"]) != clean(current):
            updates.append((key, columns[key], clean(change["to"])))
    if conflicts:
        raise Changed(conflicts, "Some of this changed in the Agenda since you opened it.")
    if not updates:
        return {"saved": [], "unconfirmed": []}
    tab = "'%s'" % entry["tab"].replace("'", "''")
    ranges = ["%s!%s%d" % (tab, column_letter(col), row) for _, col, _ in updates]
    try:
        google.update_cells(write_token, agenda.spreadsheet_id,
                            [(where, value) for where, (_, _, value) in zip(ranges, updates)])
    except google.GoogleError as error:
        if error.status in (401, 403):
            raise google.GoogleError("The dashboard can't write to the Agenda. Share the Agenda spreadsheet with %s "
                                     "as an Editor." % google.service_account(config), error.status) from None
        raise
    after = google.sheet_grid(token, agenda.spreadsheet_id, ranges).get(entry["tab"], {})
    unconfirmed = [key for key, col, value in updates if clean(text(after.get((row, col)))) != value]
    return {"saved": [key for key, _, _ in updates if key not in unconfirmed], "unconfirmed": unconfirmed}


def move(token, write_token, config, agenda, class_key, week, row, to_week):
    """Trade places: this lesson's row takes to_week, and the lesson(s) now in
    to_week take this lesson's week. Only the Wk and Dates cells change, so the
    rest of each row (plans, notes, deck links, Forms) moves with its lesson.
    Dates that are formulas update themselves; with no lesson in to_week yet,
    this row's Dates cell is left for the teacher."""
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
    targets = [r for r, other in layout["rows"] if norm(other) == norm(to_week)]
    target_label = labels[targets[0]] if targets else next(
        (w["label"] for w in agenda.weeks if norm(w["label"]) == norm(to_week)), None)
    if not target_label:
        raise ValueError("That week isn't in the Agenda.")
    cols, cells = layout["columns"], entry["cells"]
    wk, dates = cols["week"], cols.get("dates")
    if not all(plain(cells.get((r, wk))) for r in [row] + targets):
        raise ValueError("A Wk cell in these rows has a formula, so the move can only be made in the Agenda.")
    updates = [(row, wk, target_label)] + [(r, wk, label) for r in targets]
    dates_left = False
    if dates:
        literal = [r for r in [row] + targets if plain(cells.get((r, dates)))]
        if targets and len(literal) == len(targets) + 1:
            mine, theirs = (text(cells.get((r, dates))).strip() for r in (row, targets[0]))
            updates += [(row, dates, theirs)] + [(r, dates, mine) for r in targets]
        elif row in literal and not targets:
            dates_left = True
    tab = "'%s'" % entry["tab"].replace("'", "''")
    ranges = ["%s!%s%d" % (tab, column_letter(col), r) for r, col, _ in updates]
    try:
        google.update_cells(write_token, agenda.spreadsheet_id,
                            [(where, value) for where, (_, _, value) in zip(ranges, updates)])
    except google.GoogleError as error:
        if error.status in (401, 403):
            raise google.GoogleError("The dashboard can't write to the Agenda. Share the Agenda spreadsheet with %s "
                                     "as an Editor." % google.service_account(config), error.status) from None
        raise
    after = google.sheet_grid(token, agenda.spreadsheet_id, ranges).get(entry["tab"], {})
    unconfirmed = [r for r, col, value in updates if clean(text(after.get((r, col)))) != clean(value)]
    topic = cols.get("topic")
    return {"moved": {"from": label, "to": target_label,
                      "topic": text(cells.get((row, topic))).strip() if topic else ""},
            "swapped": [text(cells.get((r, topic))).strip() if topic else "" for r in targets],
            "dates_left": dates_left, "unconfirmed": sorted(set(unconfirmed))}
