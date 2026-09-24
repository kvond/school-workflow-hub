"""The dashboard's own notes on each assignment, kept in the teacher's
"Scores entered" sheet: when she last entered its scores in Infinite Campus
and when she last made its feedback. She never needs to open the sheet.

It is a Google Sheet whose name starts with "Scores entered" (for example
"Scores entered (school workflow-hub)") inside the turn-in root, which is
shared with the dashboard's account as an Editor. Each click of "I entered
these in IC" or "Mark feedback done" adds one row for that class and
assignment to its first tab; rows are never edited or deleted.
"""
import re
from datetime import datetime, timedelta, timezone

from . import google, work

NAME = "Scores entered"
HEADER = ["Recorded at", "Kind", "Class", "Week", "Assignment", "Form ID"]
KINDS = {"ic": "Entered in IC", "feedback": "Feedback made"}
MAX_ROWS = 20000


def find(token, config):
    root = str(config.get("HUB_SUBMISSIONS_ROOT_FOLDER", "") or "")
    if not root:
        return None
    found = [f for f in google.files_named_in(token, NAME, google.SHEET, root, prefix=True)
             if f.get("name", "").strip().lower().startswith(NAME.lower())]
    # The exact name first, then the oldest, so a later copy never takes over.
    found.sort(key=lambda f: (f["name"].strip().lower() != NAME.lower(), f.get("createdTime", "")))
    return found[0] if found else None


def _first_tab(token, sheet_id):
    _, tabs = google.sheet_tabs(token, sheet_id)
    title = next(iter(tabs), None)
    return "'%s'" % title.replace("'", "''") if title else None


def eastern_now():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    local = now - timedelta(hours=4)
    if work.eastern_offset(local) == 5:
        local = now - timedelta(hours=5)
    return local.strftime("%Y-%m-%d %H:%M")


def _utc(stamp):
    """'2026-09-24 10:15' (school time) as a UTC ISO time, or None."""
    match = re.fullmatch(r"\s*(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})\s*", str(stamp or ""))
    if not match:
        return None
    local = datetime(*(int(g) for g in match.groups()))
    return (local + timedelta(hours=work.eastern_offset(local))).strftime("%Y-%m-%dT%H:%M:00Z")


def read(token, config, form_id):
    """When this assignment's scores were last entered in IC and its feedback last made."""
    out = {"configured": False, "url": None, "entered_at": None, "feedback_at": None, "problem": None,
           "setup": "To keep track of what you've entered, make a Google Sheet whose name starts with "
                    "“%s” inside 00 REAL ALL TURN IN." % NAME}
    try:
        sheet = find(token, config)
        if not sheet:
            return out
        out["configured"], out["url"] = True, "https://docs.google.com/spreadsheets/d/%s/edit" % sheet["id"]
        tab = _first_tab(token, sheet["id"])
        rows = google.sheet_values(token, sheet["id"], "%s!A1:F%d" % (tab, MAX_ROWS)) if tab else []
    except google.GoogleError as error:
        out["problem"] = "The “%s” sheet couldn't be read (%s)." % (NAME, error)
        return out
    for row in rows:
        row = list(row) + [""] * (len(HEADER) - len(row))
        at = _utc(row[0])
        if row[5] != form_id or not at:
            continue
        key = {KINDS["ic"]: "entered_at", KINDS["feedback"]: "feedback_at"}.get(row[1])
        if key and (not out[key] or at > out[key]["at"]):  # the latest by date, whatever the row order
            out[key] = {"label": row[0].strip(), "at": at}
    return out


def record(token, write_token, config, kind, context):
    """Add one row noting that this assignment was entered in IC or had feedback made."""
    if kind not in KINDS:
        raise ValueError("kind")
    sheet = find(token, config)
    if not sheet:
        raise google.GoogleError("Make a Google Sheet whose name starts with “%s” inside "
                                 "00 REAL ALL TURN IN first." % NAME)
    tab = _first_tab(token, sheet["id"])
    if not tab:
        raise google.GoogleError("The “%s” sheet has no tab to write to." % NAME)
    rows = [] if google.sheet_values(token, sheet["id"], tab + "!A1:B1") else [HEADER]
    rows.append([eastern_now(), KINDS[kind], context["class"], context["week"], context["assignment"],
                 context["form_id"]])
    try:
        google.append_rows(write_token, sheet["id"], tab + "!A1", rows)
    except google.GoogleError as error:
        if error.status in (401, 403):
            raise google.GoogleError("The dashboard can't add rows to “%s”. Share it (or 00 REAL ALL TURN IN) "
                                     "with %s as an Editor." % (NAME, google.service_account(config)),
                                     error.status) from None
        raise
    return 1
