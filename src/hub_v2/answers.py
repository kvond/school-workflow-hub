"""The research sheet: every Biology student's answer to every coded box,
kept for the teacher to copy and code later.

It is a Google Sheet named exactly "Student answers — Biology" at the top of
the turn-in root (00 REAL ALL TURN IN), which the teacher makes once, blank;
the dashboard's account can edit files there but can't own new ones. When
an assignment's two weeks have closed, its on-time answers are final, and
opening it on the dashboard adds one row per student per coded box to the
sheet's first tab. Each row carries a key, so a box already in the sheet is
never added twice; rows are never edited or deleted, and a student who
changes their deck afterwards doesn't change what was saved. Answers are
written as plain text, so nothing a student typed runs as a formula.
"""
import re
import threading
from datetime import timedelta, timezone

from . import google, ledger, work

SHEETS = {"biology": "Student answers — Biology"}
HEADER = ["Saved at", "Class", "Section", "Week", "Lesson", "Assignment", "Student", "Slide", "Slide title",
          "Type", "Code", "Answer", "Status", "Turned in", "Deck last changed", "Deck link", "Row key"]
STATUS = {"done": "answered", "blank": "blank", "copied": "copied from the slide", "not found": "code missing"}
MAX_ROWS = 200000
MAX_CELL = 45000  # a Sheets cell holds 50,000 characters
_lock = threading.Lock()


def _plain_name(name):
    return " ".join(re.sub(r"[-–—]+", "-", str(name or "")).lower().split())


def setup_note(class_key):
    return ("To keep %s answers for research, make a blank Google Sheet named “%s” at the top of "
            "00 REAL ALL TURN IN." % (class_key.capitalize(), SHEETS[class_key]))


def find(token, config, class_key):
    """The class's research sheet, or None. Only the exact name counts, so a
    copy made for coding ("Copy of …", "… coded") is never written to."""
    root = str(config.get("HUB_SUBMISSIONS_ROOT_FOLDER", "") or "")
    if not root:
        return None
    wanted = _plain_name(SHEETS[class_key])
    found = [f for f in google.files_named_in(token, "Student answers", google.SHEET, root, prefix=True)
             if _plain_name(f.get("name")) == wanted]
    found.sort(key=lambda f: f.get("createdTime", ""))
    return found[0] if found else None


def row_key(form_id, row, item):
    return "|".join([form_id, row["section"], row["last"], row["first"], item["category"], item["id"]])


def rows_for(context, lesson, scored):
    """(sheet rows, their keys) for the on-time, roster-matched students' scored decks."""
    out = []
    for row, result in scored:
        if not row.get("matched") or not result or result.get("problem"):
            continue
        upload = next((f for f in row["files"] if f.get("id") == result.get("file_id")), row["files"][0])
        for item in result.get("items", []):
            out.append((row_key(context["form_id"], row, item), [
                ledger.eastern_now(), context["class"], row["section"], context["week"], lesson,
                context["assignment"], "%s, %s" % (row["last"], row["first"]),
                item.get("at") or "", item.get("question") or "", item["category"], item["id"],
                (item.get("answer") or "")[:MAX_CELL], STATUS.get(item["status"], item["status"]),
                _school_time(row.get("submitted_at")),
                _school_time(result.get("file_updated")), upload.get("url") or "",
                row_key(context["form_id"], row, item)]))
    return out


def _column(index):
    """0 → 'A', 16 → 'Q', 26 → 'AA'."""
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def _school_time(stamp):
    """A Google timestamp as '2026-09-24 10:15' school time, or ''."""
    moment = work.when(stamp)
    if not moment:
        return ""
    naive = moment.astimezone(timezone.utc).replace(tzinfo=None)
    local = naive - timedelta(hours=4)
    if work.eastern_offset(local) == 5:
        local = naive - timedelta(hours=5)
    return local.strftime("%Y-%m-%d %H:%M")


def closed(form, now=None):
    """Whether this assignment's two weeks are over, so its on-time answers are final."""
    closes = work.when((form.get("window") or {}).get("at"))
    return bool(closes and closes <= (now or work._now()))


def save(token, write_token, config, class_key, context, lesson, form, scored, now=None):
    """Add this assignment's final answers to the class's research sheet."""
    if class_key not in SHEETS:
        raise ValueError("class")
    if not closed(form, now):
        return {"saved": 0, "already": 0, "waiting": (form.get("window") or {}).get("label") or ""}
    sheet = find(token, config, class_key)
    if not sheet:
        return {"configured": False, "setup": setup_note(class_key)}
    url = "https://docs.google.com/spreadsheets/d/%s/edit" % sheet["id"]
    candidates = rows_for(context, lesson, scored)
    with _lock:
        _, tabs = google.sheet_tabs(token, sheet["id"])
        title = next(iter(tabs), None)
        if not title:
            raise google.GoogleError("The “%s” sheet has no tab to write to." % SHEETS[class_key])
        tab = "'%s'" % title.replace("'", "''")
        first = (google.sheet_values(token, sheet["id"], tab + "!1:1") or [[]])[0]
        # Find the keys by their heading, in case a column was added in front.
        key = _column(first.index("Row key") if "Row key" in first else HEADER.index("Row key"))
        saved = {r[0] for r in google.sheet_values(
            token, sheet["id"], "%s!%s2:%s%d" % (tab, key, key, MAX_ROWS)) if r}
        new = [values for key, values in candidates if key not in saved]
        if new or not first:
            try:
                google.append_rows(write_token, sheet["id"], tab + "!A1", ([] if first else [HEADER]) + new)
            except google.GoogleError as error:
                if error.status in (401, 403):
                    raise google.GoogleError(
                        "The dashboard can't add rows to “%s”. Share it (or 00 REAL ALL TURN IN) with %s as an "
                        "Editor." % (SHEETS[class_key], google.service_account(config)), error.status) from None
                raise
    return {"configured": True, "url": url, "saved": len(new), "already": len(candidates) - len(new)}
