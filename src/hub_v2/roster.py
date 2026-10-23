"""Class rosters from the roster workbook, and matching a submission to a student.

Each section tab lists students as Last, First (a "Last name / First name"
header row, a "Last, First" cell, or a section heading row are all accepted).
A column headed "Added" (or "Date added" / "Joined") holds the date a student
joined the class; assignments that began before it are excused for them.
Rows that can't be read are counted and reported, not fatal.
"""
import json
import re
import unicodedata
from datetime import datetime
from urllib.parse import quote

from . import google
from school_workflow_hub.adapters import roster_status
from school_workflow_hub.adapters.hosted_agenda import ConnectionError

SECTIONS = {"forensics": ("A2 Forensics", "A3 Forensics"),
            "biology": ("A4 Biology",),
            "anatomy": ("B3 A&P",)}


def plain(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def compact(value):
    return plain(value).replace(" ", "")


def first_names(first):
    """'Jonathan (Jon) Paul' -> {'jonathan paul', 'jonathan', 'jon'}."""
    legal = plain(re.sub(r"\([^)]*\)", " ", first))
    names = {legal, legal.split()[0] if legal else ""}
    names.update(plain(n) for n in re.findall(r"\(([^)]+)\)", first))
    return {n for n in names if n}


SUFFIX = re.compile(r"\s+(?:jr|sr|ii|iii|iv|v)$")


def last_names(last):
    """'Harrigan Jr.' -> {'harrigan jr', 'harrigan'}: students often omit a suffix."""
    full = plain(last)
    return {n for n in (full, SUFFIX.sub("", full)) if n}


def aliases(student):
    return {compact(f + " " + l) for f in first_names(student["first"]) for l in last_names(student["last"])}


LAYOUTS = {"first_last_columns": "first_last", "last_first_columns": "last_first"}
ADDED = {"added", "date added", "joined", "date joined", "added on"}


def added_date(value):
    """'9/28/2026', '09/28/26' or '2026-09-28' → '2026-09-28'; anything else → ''."""
    value = str(value or "").strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return ""
_ORIENTATION = {}


def parse_tab(cells, class_key, section, layouts=None, status_columns=None):
    """Students in one roster tab. Each student records how their column order
    was known: a header row, a 'Last, First' cell, the configured layout, or
    only assumed (Last | First), which orient() can correct."""
    layouts = layouts or {}
    status_columns = status_columns or {}
    status_column = status_columns.get(section)
    if status_column:
        roster_status.require_header(cells.get((1, status_column), {}).get("formattedValue"))
    names = {plain(s): s for tabs in SECTIONS.values() for s in tabs}

    def start(name, first_row=1):
        configured = LAYOUTS.get(layouts.get(name, ""))
        if configured:
            return configured, "config"
        # A sort can move the name header below student rows. Use it for the
        # whole section block only when every header in that block agrees.
        # Never borrow a later section's layout or resolve conflicting headers.
        headers = set()
        for row in range(first_row, 201):
            a = plain(cells.get((row, 1), {}).get("formattedValue", ""))
            b = plain(cells.get((row, 2), {}).get("formattedValue", ""))
            if a in names and not b:
                break
            if {a, b} == {"last name", "first name"}:
                headers.add("last_first" if a == "last name" else "first_last")
        return (headers.pop(), "header") if len(headers) == 1 else ("last_first", "assumed")

    students, skipped = [], 0
    order, basis = start(section)
    added_col = next((c for r in range(1, 201) for c in range(3, 11)
                      if plain(cells.get((r, c), {}).get("formattedValue", "")) in ADDED), None)
    if added_col is None:
        # The canonical IC mirror uses its explicit membership Start date.
        # Existing teacher Added/Joined columns retain precedence unchanged.
        added_col = next((c for r in range(1, 201) for c in range(3, 11)
                          if plain(cells.get((r, c), {}).get("formattedValue", "")) == "start date"), None)
    for r in range(1, 201):
        a = str(cells.get((r, 1), {}).get("formattedValue", "")).strip()
        b = str(cells.get((r, 2), {}).get("formattedValue", "")).strip()
        if not a and not b:
            continue
        if plain(a) in names and not b:
            section = names[plain(a)]
            if status_columns.get(section) != status_column:
                raise ConnectionError("roster_status_mixed_section_layout")
            order, basis = start(section, r + 1)
            continue
        if {plain(a), plain(b)} == {"last name", "first name"}:
            order, basis = ("last_first" if plain(a) == "last name" else "first_last"), "header"
            continue
        if a and not b and "," not in a and not students:
            continue  # a title above the list, not a student
        if a and not b and "," in a:
            (last, first), how = [part.strip() for part in a.split(",", 1)], "comma"
        elif order == "first_last":
            first, last, how = a, b, basis
        else:
            last, first, how = a, b, basis
        if not plain(last) or not plain(first):
            skipped += 1
            continue
        if status_column and not roster_status.active_status(
                cells.get((r, status_column), {}).get("formattedValue", "")):
            continue
        added = added_date(cells.get((r, added_col), {}).get("formattedValue", "")) if added_col else ""
        students.append({"class_key": class_key, "section": section, "first": first, "last": last,
                         "order": how, "added": added})
    return students, skipped


def orient(students, usernames):
    """Swap first and last names for any section whose column order was only
    assumed, when the school usernames seen in its submissions (s.first.last)
    match clearly better the other way round. A decision is remembered."""
    by_section = {}
    for student in students:
        if student.get("order") == "assumed":
            by_section.setdefault((student["class_key"], student["section"]), []).append(student)
    for key, rows in by_section.items():
        flip = _ORIENTATION.get(key)
        if flip is None:
            as_is = sum(1 for s in rows if aliases(s) & usernames)
            flipped = sum(1 for s in rows if aliases({"first": s["last"], "last": s["first"]}) & usernames)
            if max(as_is, flipped) < 2 or as_is == flipped:
                continue
            flip = _ORIENTATION[key] = flipped > as_is
        if flip:
            for s in rows:
                s["first"], s["last"], s["order"] = s["last"], s["first"], "usernames"


def load(token, config):
    """({class_key: [student, ...]}, [problem, ...])."""
    roster_id = config.get("HUB_ROSTER_SPREADSHEET_ID", "")
    if not roster_id:
        return {}, ["No roster workbook is configured (HUB_ROSTER_SPREADSHEET_ID)."]
    try:
        configured = json.loads(config.get("HUB_ROSTER_TABS") or "{}")
    except ValueError:
        configured = {}
    try:
        layouts = json.loads(config.get("HUB_ROSTER_COLUMN_LAYOUTS") or "{}")
    except ValueError:
        layouts = {}
    try:
        status_columns = roster_status.configured_columns(config, [s for v in SECTIONS.values() for s in v])
    except ConnectionError:
        return {}, ["The roster status column configuration could not be verified."]
    dimensions = {}
    try:
        if status_columns:
            # Status-bearing mirrors also carry membership dates. Read their
            # native grid bounds with the usual tab metadata, so the existing
            # A:J date-header parser receives its columns without requesting
            # cells beyond a narrow canonical tab's actual grid.
            metadata = google.get_json(
                "https://sheets.googleapis.com/v4/spreadsheets/" + quote(roster_id), token,
                {"fields": "sheets(properties(sheetId,title,gridProperties(rowCount,columnCount)))"})
            properties = [s.get("properties", {}) for s in metadata.get("sheets", [])]
            tabs = {p.get("title"): p.get("sheetId") for p in properties}
            dimensions = {p.get("title"): p.get("gridProperties", {}) for p in properties}
            if (len(tabs) != len(properties) or len(set(tabs.values())) != len(properties)
                    or any(not isinstance(t, str) or not t or type(i) is not int for t, i in tabs.items())):
                return {}, ["The roster tab metadata could not be verified."]
        else:
            _, tabs = google.sheet_tabs(token, roster_id)
    except google.GoogleError as error:
        return {}, ["The roster workbook couldn't be read: %s" % error]
    by_id = {sheet_id: title for title, sheet_id in tabs.items()}
    wanted, problems = {}, []
    for class_key, sections in SECTIONS.items():
        for section in sections:
            choice = configured.get(section, section)
            title = by_id.get(choice) if isinstance(choice, int) else (
                choice if choice in tabs else next(
                    (t for t in tabs if plain(t) == plain(choice)), None))
            if title:
                wanted[section] = (class_key, title)
            else:
                problems.append("Roster tab '%s' wasn't found." % section)
    if not wanted:
        return {}, problems
    titles = sorted({title for _, title in wanted.values()})
    ranges = []
    for title in titles:
        status_column = max([0] + [status_columns.get(s, 0)
                                  for s, (_, tab) in wanted.items() if tab == title])
        last_column, last_row = max(2, status_column), 200
        if status_column:
            grid = dimensions.get(title, {})
            columns, rows = grid.get("columnCount"), grid.get("rowCount")
            if (type(columns) is not int or type(rows) is not int
                    or columns < last_column or rows < 1):
                return {}, ["Roster grid bounds in '%s' could not be verified." % title]
            last_column, last_row = max(last_column, min(10, columns)), min(200, rows)
        ranges.append("'%s'!A1:%s%d" % (title.replace("'", "''"), chr(64 + last_column), last_row))
    try:
        grids = google.sheet_grid(token, roster_id, ranges)
    except google.GoogleError as error:
        return {}, problems + ["The roster tabs couldn't be read: %s" % error]
    roster, seen = {}, set()
    # A tab that holds several sections (with heading rows) is parsed once,
    # starting in the first section mapped to it.
    first_section = {}
    for section, (class_key, title) in wanted.items():
        first_section.setdefault(title, (class_key, section))
    for title, (class_key, section) in sorted(first_section.items()):
        try:
            students, skipped = parse_tab(grids.get(title, {}), class_key, section,
                layouts if isinstance(layouts, dict) else {}, status_columns)
        except ConnectionError:
            return {}, problems + ["Roster status in '%s' could not be verified; previous roster remains unchanged." % section]
        if skipped:
            problems.append("%d row(s) in roster tab '%s' couldn't be read as Last, First."
                            % (skipped, title))
        for student in students:
            key = (student["class_key"], student["section"], plain(student["last"]), plain(student["first"]))
            if key not in seen:
                seen.add(key)
                roster.setdefault(class_key, []).append(student)
    return roster, problems


def match_email(email, students, domains=(), prefix="s."):
    """The one student whose name is the school email's username, else None."""
    email = str(email or "").strip().lower()
    if email.count("@") != 1:
        return None
    local, domain = email.split("@")
    if domains and domain not in domains:
        return None
    if prefix and local.startswith(prefix):
        local = local[len(prefix):]
    local = compact(local)
    found = [s for s in students if local in aliases(s)]
    return found[0] if len(found) == 1 else None


def username_key(username, prefix="s."):
    """'s.first.last' or 's.first.last@school' → 'firstlast'."""
    local = str(username or "").strip().lower().split("@")[0]
    if prefix and local.startswith(prefix):
        local = local[len(prefix):]
    return compact(local)


def match_username(username, students, prefix="s."):
    """The one student whose name is this school username ('s.first.last')."""
    local = str(username or "").strip().lower()
    if prefix and local.startswith(prefix):
        local = local[len(prefix):]
    local = compact(local)
    found = [s for s in students if local and local in aliases(s)]
    return found[0] if len(found) == 1 else None


def file_username(file_name, prefix="s."):
    """Earlier routing named copies 's.first.last — 09-16 12.40 — Original name'."""
    match = re.match(r"\s*(%s[a-z0-9.\-]+)\s+[—-]" % re.escape(prefix or ""), str(file_name or ""), re.I)
    return match.group(1) if match else ""


def match_name(name, students):
    """The one student whose full name matches, e.g. a Forms upload's ' - Name' suffix."""
    target = compact(name)
    if not target:
        return None
    found = [s for s in students if target in aliases(s)
             or target == compact(s["last"] + " " + s["first"])]
    return found[0] if len(found) == 1 else None


def uploader_name(file_name):
    """Google Forms names uploads 'Original name - Student Name.ext'."""
    stem = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", str(file_name or ""))
    return stem.rsplit(" - ", 1)[1].strip() if " - " in stem else ""
