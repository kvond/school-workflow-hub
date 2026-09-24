"""The teacher's own "Scores entered" sheet: which scores she has entered in
Infinite Campus and who has had feedback, so the next list shows only what is
new or changed (a resubmission with revisions updates the same grade).

She makes a Google Sheet whose name starts with "Scores entered" (for example
"Scores entered (school workflow-hub)") inside the turn-in root, which is
shared with the dashboard's account as an Editor. The dashboard adds rows to
its first tab when she clicks "I entered these" or "Mark feedback done"; it
never edits or deletes a row, so the sheet is also her history.
"""
from datetime import datetime, timedelta, timezone

from . import google, roster, work

NAME = "Scores entered"
HEADER = ["Recorded at", "Kind", "Class", "Week", "Assignment", "Form ID", "Section", "Last name",
          "First name", "Notes /10", "Revision /10", "File ID", "File updated"]
KINDS = {"ic": "Entered in IC", "feedback": "Feedback made"}
MAX_ROWS = 20000


def score_text(value):
    return "N/A" if value is None else "%g" % value


def shown_scores(score, closed):
    """(Notes, Revision) as they go into IC. While an assignment's two weeks
    are open an empty result stays blank; once they close it is 0. No work
    turned in on time (score None) is blank, then 0."""
    if score is None:
        return ("0", "0") if closed else ("", "")
    out = []
    for value in (score.get("notes_score"), score.get("revision_score")):
        out.append("" if value == 0 and not closed else score_text(value))
    return tuple(out)


def student_key(section, last, first):
    return "|".join(roster.plain(v) for v in (section, last, first))


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


def read(token, config, form_id):
    """What the sheet says about one assignment: the latest entered scores per
    student, and who has had feedback."""
    out = {"configured": False, "url": None, "entered": {}, "feedback": {}, "problem": None,
           "setup": "To track what you've entered, make a Google Sheet whose name starts with “%s” inside "
                    "00 REAL ALL TURN IN (it needs the dashboard's account as an Editor there)." % NAME}
    try:
        sheet = find(token, config)
        if not sheet:
            return out
        out["configured"], out["url"] = True, "https://docs.google.com/spreadsheets/d/%s/edit" % sheet["id"]
        tab = _first_tab(token, sheet["id"])
        rows = google.sheet_values(token, sheet["id"], "%s!A1:M%d" % (tab, MAX_ROWS)) if tab else []
    except google.GoogleError as error:
        out["problem"] = "The “%s” sheet couldn't be read (%s)." % (NAME, error)
        return out
    for row in rows:
        row = list(row) + [""] * (len(HEADER) - len(row))
        if row[5] != form_id:
            continue
        key = student_key(row[6], row[7], row[8])
        if row[1] == KINDS["ic"]:
            out["entered"][key] = {"notes": row[9], "revision": row[10], "at": row[0], "file_id": row[11]}
        elif row[1] == KINDS["feedback"]:
            out["feedback"][key] = {"at": row[0], "file_id": row[11]}
    return out


def eastern_now():
    now = datetime.now(timezone.utc)
    naive = now.replace(tzinfo=None)
    local = naive - timedelta(hours=4)
    if work.eastern_offset(local) == 5:
        local = naive - timedelta(hours=5)
    return local.strftime("%Y-%m-%d %H:%M")


def record(token, write_token, config, kind, context, entries):
    """Add one row per entry; returns how many were added."""
    if kind not in KINDS or not entries:
        raise ValueError("kind")
    sheet = find(token, config)
    if not sheet:
        raise google.GoogleError("Make a Google Sheet whose name starts with “%s” inside "
                                 "00 REAL ALL TURN IN first." % NAME)
    tab = _first_tab(token, sheet["id"])
    if not tab:
        raise google.GoogleError("The “%s” sheet has no tab to write to." % NAME)
    rows = [] if google.sheet_values(token, sheet["id"], tab + "!A1:B1") else [HEADER]
    stamp = eastern_now()
    for entry in entries:
        rows.append([stamp, KINDS[kind], context["class"], context["week"], context["assignment"],
                     context["form_id"], entry["section"], entry["last"], entry["first"],
                     entry["notes"], entry["revision"], entry["file_id"], entry["file_updated"]])
    try:
        google.append_rows(write_token, sheet["id"], tab + "!A1", rows)
    except google.GoogleError as error:
        if error.status in (401, 403):
            raise google.GoogleError("The dashboard can't add rows to “%s”. Share it (or 00 REAL ALL TURN IN) "
                                     "with %s as an Editor." % (NAME, google.service_account(config)),
                                     error.status) from None
        raise
    return len(entries)
