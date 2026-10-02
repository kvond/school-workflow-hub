"""What the Routine page shows is actually running, read only: the newest
"Monday run — <date>" Doc that school Claude leaves at the top of the
turn-in root, and the last row of the Scores entered sheet.
A problem with one of them is shown on its own line, never a failed page.
"""
from . import google, ledger

MONDAY_RUN = "Monday run"


def _sheet_url(sheet_id):
    return "https://docs.google.com/spreadsheets/d/%s/edit" % sheet_id


def last_row_date(token, sheet_id):
    """The last filled cell in column A of the sheet's first tab ('' if none)."""
    _, tabs = google.sheet_tabs(token, sheet_id)
    title = next(iter(tabs), None)
    if not title:
        return ""
    rows = google.sheet_values(token, sheet_id, "'%s'!A2:A" % title.replace("'", "''"))
    return next((row[0] for row in reversed(rows) if row and row[0]), "")


def monday_run(token, config):
    root = str(config.get("HUB_SUBMISSIONS_ROOT_FOLDER", "") or "")
    if not root:
        return None
    docs = [f for f in google.files_named_in(token, MONDAY_RUN, google.DOC, root, prefix=True,
                                              order="createdTime desc")
            if f.get("name", "").strip().lower().startswith(MONDAY_RUN.lower())]
    if not docs:
        return None
    newest = max(docs, key=lambda f: f.get("createdTime", ""))
    return {"name": newest["name"], "at": newest.get("createdTime", ""),
            "url": "https://docs.google.com/document/d/%s/edit" % newest["id"]}


def status(token, config):
    out = {}
    checks = (("monday_run", lambda: monday_run(token, config)),
              ("scores_entered", lambda: ledger.find(token, config)))
    for key, check in checks:
        try:
            found = check()
            if found and key != "monday_run":
                found = {"name": found["name"], "url": _sheet_url(found["id"]),
                         "last": last_row_date(token, found["id"])}
            out[key] = found
        except google.GoogleError as error:
            out[key] = {"problem": str(error)}
    return out
