"""Class rosters from the roster workbook, and matching a submission to a student.

Each section tab lists students as Last, First (a "Last name / First name"
header row, a "Last, First" cell, or a section heading row are all accepted).
Rows that can't be read are counted and reported, not fatal.
"""
import json
import re
import unicodedata

from . import google

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


def aliases(student):
    return {compact(f + " " + student["last"]) for f in first_names(student["first"])}


def parse_tab(cells, class_key, section):
    students, skipped, order = [], 0, "last_first"
    names = {plain(s): s for tabs in SECTIONS.values() for s in tabs}
    for r in range(1, 201):
        a = str(cells.get((r, 1), {}).get("formattedValue", "")).strip()
        b = str(cells.get((r, 2), {}).get("formattedValue", "")).strip()
        if not a and not b:
            continue
        if plain(a) in names and not b:
            section = names[plain(a)]
            continue
        if {plain(a), plain(b)} == {"last name", "first name"}:
            order = "last_first" if plain(a) == "last name" else "first_last"
            continue
        if a and not b and "," in a:
            last, first = [part.strip() for part in a.split(",", 1)]
        elif order == "first_last":
            first, last = a, b
        else:
            last, first = a, b
        if not plain(last) or not plain(first):
            skipped += 1
            continue
        students.append({"class_key": class_key, "section": section, "first": first, "last": last})
    return students, skipped


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
    try:
        grids = google.sheet_grid(token, roster_id, ["'%s'!A1:B200" % t.replace("'", "''")
                                                     for t in titles])
    except google.GoogleError as error:
        return {}, problems + ["The roster tabs couldn't be read: %s" % error]
    roster, seen = {}, set()
    # A tab that holds several sections (with heading rows) is parsed once,
    # starting in the first section mapped to it.
    first_section = {}
    for section, (class_key, title) in wanted.items():
        first_section.setdefault(title, (class_key, section))
    for title, (class_key, section) in sorted(first_section.items()):
        students, skipped = parse_tab(grids.get(title, {}), class_key, section)
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
