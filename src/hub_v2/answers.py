"""The research sheet: every Biology student's answer to every coded box,
kept for the teacher to copy and code later.

It is a Google Sheet named "Student answers — Biology", selected by the
explicit HUB_BIOLOGY_ANSWERS_SPREADSHEET_ID when configured, or by exact title
at the top of the turn-in root as a legacy fallback;
the dashboard's account can edit files there but can't own new ones. When
an assignment's two weeks have closed, its on-time answers are final, and
opening it on the dashboard adds one row per student per coded box to the
sheet's first tab. The dashboard also checks by itself, once a day when it's
opened, for closed assignments not yet in the sheet (sweep), so no week has to
be opened by hand. Each row carries a key, so a box already in the sheet is
never added twice; rows are never edited or deleted, and a student who
changes their deck afterwards doesn't change what was saved. Answers are
written as plain text, so nothing a student typed runs as a formula.
"""
import re
import threading
import time
from datetime import date, timedelta, timezone

from . import google, ledger, work

SHEETS = {"biology": "Student answers — Biology"}
HEADER = ["Saved at", "Class", "Section", "Week", "Lesson", "Assignment", "Student", "Slide", "Slide title",
          "Type", "Code", "Answer", "Status", "Turned in", "Deck last changed", "Deck link", "Row key"]
STATUS = {"done": "answered", "blank": "blank", "copied": "copied from the slide", "not found": "code missing"}
MAX_ROWS = 200000
MAX_CELL = 45000  # a Sheets cell holds 50,000 characters
SWEEP_SECONDS = 25  # then the page asks again, well inside the server's request limit
_lock = threading.Lock()
_sweeping = threading.Lock()


def _plain_name(name):
    return " ".join(re.sub(r"[_\-–—]+", "-", str(name or "")).lower().split())


def setup_note(class_key):
    return ("To keep %s answers for research, configure HUB_%s_ANSWERS_SPREADSHEET_ID with the ID of "
            "the school-owned Google Sheet named “%s”." %
            (class_key.capitalize(), class_key.upper(), SHEETS[class_key]))


def find(token, config, class_key):
    """The class's research sheet, or None.

    An explicit ID is preferred and is verified against the expected title and
    spreadsheet MIME type before any read/write. The exact-title root search is
    retained for installations that have not configured a dedicated ID.
    """
    explicit_id = str(config.get("HUB_%s_ANSWERS_SPREADSHEET_ID" % class_key.upper(), "") or "").strip()
    wanted = _plain_name(SHEETS[class_key])
    if explicit_id:
        sheet = google.drive_file(token, explicit_id)
        if sheet.get("id") != explicit_id or sheet.get("mimeType") != google.SHEET \
                or _plain_name(sheet.get("name")) != wanted or sheet.get("trashed"):
            raise google.GoogleError(
                "The configured %s research file must be an untrashed Google Sheet named “%s”." %
                (class_key.capitalize(), SHEETS[class_key]))
        return sheet
    root = str(config.get("HUB_SUBMISSIONS_ROOT_FOLDER", "") or "")
    if not root:
        return None
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


def _saved(token, sheet, class_key):
    """(tab range name, heading row, the row keys already in the sheet)."""
    _, tabs = google.sheet_tabs(token, sheet["id"])
    title = next(iter(tabs), None)
    if not title:
        raise google.GoogleError("The “%s” sheet has no tab to write to." % SHEETS[class_key])
    tab = "'%s'" % title.replace("'", "''")
    first = (google.sheet_values(token, sheet["id"], tab + "!1:1") or [[]])[0]
    # Find the keys by their heading, in case a column was added in front.
    key = _column(first.index("Row key") if "Row key" in first else HEADER.index("Row key"))
    keys = {r[0] for r in google.sheet_values(
        token, sheet["id"], "%s!%s2:%s%d" % (tab, key, key, MAX_ROWS)) if r}
    return tab, first, keys


def _add(token, write_token, config, class_key, sheet, candidates):
    """Append the candidate rows the sheet doesn't have yet: (added, already)."""
    with _lock:
        tab, first, keys = _saved(token, sheet, class_key)
        new = [values for key, values in candidates if key not in keys]
        if new or not first:
            try:
                google.append_rows(write_token, sheet["id"], tab + "!A1", ([] if first else [HEADER]) + new)
            except google.GoogleError as error:
                if error.status in (401, 403):
                    raise google.GoogleError(
                        "The dashboard can't add rows to “%s”. Share it (or 00 REAL ALL TURN IN) with %s as an "
                        "Editor." % (SHEETS[class_key], google.service_account(config)), error.status) from None
                raise
    return len(new), len(candidates) - len(new)


def save(token, write_token, config, class_key, context, lesson, form, scored, now=None):
    """Add this assignment's final answers to the class's research sheet."""
    if class_key not in SHEETS:
        raise ValueError("class")
    if not closed(form, now):
        return {"saved": 0, "already": 0, "waiting": (form.get("window") or {}).get("label") or ""}
    sheet = find(token, config, class_key)
    if not sheet:
        return {"configured": False, "setup": setup_note(class_key)}
    added, already = _add(token, write_token, config, class_key, sheet, rows_for(context, lesson, scored))
    return {"configured": True, "url": "https://docs.google.com/spreadsheets/d/%s/edit" % sheet["id"],
            "saved": added, "already": already}


def sweep(token, write_token, config, agenda, class_key, load_work, score_rows, now=None, clock=time.monotonic):
    """Add every closed assignment of this class that has no rows in the sheet yet.

    Weeks that haven't started, and weeks whose Forms all have rows already,
    are skipped without reading Drive. load_work(week) is the Student work
    result for a week, score_rows(week, form, found) its scored students.
    Stops after SWEEP_SECONDS with more=True, so the page asks again. "next"
    is the open assignment that closes soonest (its answers come next)."""
    if class_key not in SHEETS:
        raise ValueError("class")
    sheet = find(token, config, class_key)
    if not sheet:
        return {"configured": False, "setup": setup_note(class_key)}
    if not _sweeping.acquire(blocking=False):
        return {"configured": True, "busy": True, "saved": 0, "more": True}
    try:
        now = now or work._now()
        today = work.school_day(now)
        _, _, keys = _saved(token, sheet, class_key)
        done = {key.split("|", 1)[0] for key in keys}  # Forms with rows already
        start, added, forms, more, seen, upcoming = clock(), 0, [], False, set(), None
        for option in agenda.week_options(class_key):
            if not option["forms"] or not option.get("monday") or date.fromisoformat(option["monday"]) > today:
                continue
            ids = {f["form_id"] for lesson in agenda.lessons(class_key, option["label"])
                   for f in lesson["forms"] if f["form_id"]} - done - seen
            if not ids:
                continue
            if clock() - start > SWEEP_SECONDS:
                more = True
                break
            found = load_work(option["label"])
            for lesson in found.get("lessons", []):
                for form in lesson["forms"]:
                    if form.get("form_id") not in ids:
                        continue
                    if not closed(form, now):
                        # Still open: the soonest to close is the next to be added.
                        if form.get("window") and (not upcoming or form["window"]["at"] < upcoming["at"]):
                            upcoming = {"week": option["label"], "assignment": lesson.get("topic") or form["label"],
                                        "label": form["window"]["label"], "at": form["window"]["at"]}
                        continue
                    seen.add(form["form_id"])  # a Form listed in two weeks is saved once
                    context = {"class": agenda.classes[class_key]["name"], "week": option["label"],
                               "assignment": form["label"], "form_id": form["form_id"]}
                    candidates = rows_for(context, lesson.get("topic", ""), score_rows(option["label"], form, found))
                    new, _ = _add(token, write_token, config, class_key, sheet, candidates)
                    if new:
                        added += new
                        forms.append({"week": option["label"], "assignment": form["label"], "saved": new})
        return {"configured": True, "url": "https://docs.google.com/spreadsheets/d/%s/edit" % sheet["id"],
                "saved": added, "forms": forms, "more": more, "next": upcoming}
    finally:
        _sweeping.release()
