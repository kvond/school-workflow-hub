"""Student work for one lesson: its Google Form responses plus everything in
the lesson's turn-in folders.

The Form link(s) in the lesson's Agenda row name the assignments. Each response
says who submitted (their school email) and which files they uploaded. The
lesson's turn-in folders in 00 ALL TURN IN / <class> (any folder named for the
lesson's week, and each Form's own '(File responses)' folder) also hold files
that arrived another way, such as uploads moved from an earlier Form; those
are matched to students by file name and shown as found in the folder.

Files are sorted to an assignment by where they sit, or, when one folder holds
several assignments, by the deck they were copied from. A student's current
work is their latest usable Form upload (not trashed, not named for a different
week), else their latest usable folder file; everything else stays visible as
an earlier version. Nothing is dropped: a file that can't be matched to the
roster or to an assignment is still listed.

Each assignment (Form) stays open for two weeks from the first turn-in by
anyone: until the end of that day two weeks later, school time. Anything
turned in after that is marked Late, listed on its own and left off the grade
sheet's graded rows.

Every load also runs a roster check: work from someone who is on another
class's roster, or on no roster at all, is flagged, and the Rosters sheet's
last update is shown.
"""
import os
import json
import re
import threading
import time
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from . import google, roster
from .agenda import lesson_kind, norm

STUDENT_WORK = ("application/vnd.google-apps.presentation", "application/vnd.google-apps.document",
                "application/pdf", "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/vnd.ms-powerpoint", "image/jpeg", "image/png", "image/heic")
# Documents the teacher's system makes and keeps beside the work (feedback,
# growth records, class growth summaries, teacher notes, summary ratings,
# Monday run logs) are never student work. Words may be joined by spaces,
# underscores or hyphens: Teacher Claude sometimes saves "Growth_record_…".
_JOIN = r"[\s_-]+"
NOT_STUDENT_WORK = re.compile(
    r"growth{0}(?:report|record)|feedback|class{0}(?:growth{0})?summary|teaching{0}summary|summary{0}ratings|"
    r"monday{0}run|teacher{0}notes|template|answer{0}key".format(_JOIN), re.I)
MAX_FOLDERS = 60
# Turn-ins through the wrong Form: lessons this many teaching weeks either side
# of the one on the page are checked, and their Forms' responses kept briefly.
NEARBY_LESSONS = 2
RESPONSES_SECONDS = 120
# Words every deck or Form name has, which say nothing about which lesson it is.
GENERIC_WORDS = {"copy", "live", "week", "cycle", "forensics", "biology", "anatomy", "slides", "google",
                 "markered", "presentation", "untitled", "lesson", "student", "final", "draft", "notes"}
LATE_AFTER_DAYS = 14
ROSTER_STALE_DAYS = 7
ROSTER_FLAG_DAYS = 7  # roster-change flags about older work are dropped


def week_number(label):
    match = re.search(r"(\d+)", str(label or ""))
    return int(match.group(1)) if match else None


def _weeks(value):
    """A week number, or a set of them, as a set without None."""
    found = set(value) if isinstance(value, (set, frozenset, list, tuple)) else {value}
    found.discard(None)
    return found


def named_week(file_name, lesson_week):
    """The week a file's name says it's for, when that is none of the lesson's
    weeks: its Agenda week, or the week its own deck is named for (a lesson
    moved to another week keeps its deck's name)."""
    allowed = _weeks(lesson_week)
    weeks = [int(n) for n in re.findall(r"\b(?:week|wk)\s*0*(\d{1,2})\b", str(file_name or ""), re.I)]
    if not allowed or not weeks or allowed & set(weeks):
        return None
    return weeks[0]


def submissions(responses, source_form_id=None):
    found = []
    for response in responses:
        files, typed = [], []
        for answer in (response.get("answers") or {}).values():
            for upload in ((answer.get("fileUploadAnswers") or {}).get("answers") or []):
                if upload.get("fileId"):
                    files.append({"id": upload["fileId"], "name": upload.get("fileName", ""),
                                  "mime": upload.get("mimeType", ""), **({
                                      "source_form_id": source_form_id,
                                      "source_response_id": response.get("responseId", ""),
                                      "source_submitted_at": response.get("lastSubmittedTime") or response.get("createTime") or ""
                                  } if source_form_id else {})})
            for value in ((answer.get("textAnswers") or {}).get("answers") or []):
                value = str(value.get("value", "")).strip()
                if value and len(value) <= 120:
                    typed.append(value)
        found.append({"source": "form", "event": True, "email": str(response.get("respondentEmail") or "").lower(),
                      "key": response.get("responseId", ""),
                      "submitted_at": response.get("lastSubmittedTime") or response.get("createTime") or "",
                      "files": files, "typed": typed[:4]})
    return found


def folder_submission(item):
    created = item.get("createdTime", "")
    stamped = name_time(item.get("name"), created)
    # Only a turn-in time written into the name is a real turn-in event; a
    # file's creation time may be when the student made their copy.
    return {"source": "folder", "event": bool(stamped), "email": "", "key": item["id"],
            "submitted_at": stamped or created,
            "files": [{"id": item["id"], "name": item.get("name", ""), "mime": item.get("mimeType", "")}],
            "typed": []}


def identify(submission, students, domains, prefix):
    """(student, how): school email, then the username or uploader name in a
    file's name, then a name typed into the Form."""
    # Only the read-only exact historical adapter sets this internal binding,
    # after native IC email/ID proof. Never fall back to a typed/name guess.
    if '_historical_identity' in submission:
        identity = submission['_historical_identity']
        matches = [s for s in students if s.get('section') == identity.get('section')
                   and all(roster.plain(s.get(k)) == roster.plain(identity.get(k)) for k in ('first', 'last'))]
        return (matches[0], 'email') if len(matches) == 1 else (None, None)
    student = roster.match_email(submission["email"], students, domains, prefix)
    if student:
        return student, "email"
    for upload in submission["files"]:
        student = roster.match_username(roster.file_username(upload["name"], prefix), students, prefix) \
            if prefix else None
        student = student or roster.match_name(roster.uploader_name(upload["name"]), students)
        if student:
            return student, "file name"
    for value in submission["typed"]:
        student = roster.match_name(value, students)
        if student:
            return student, "name typed in the Form"
    return None, None


def student_key(student):
    return (student["section"], roster.plain(student["last"]), roster.plain(student["first"]))


def _usable(submission):
    return not submission["files"] or any(
        not f.get("trashed") and not f.get("other_week") for f in submission["files"])


def group(found, students, domains=(), prefix="s.", lesson_week=None, with_missing=True, cutoff=None):
    """([one row per student], [roster students with nothing yet]).

    With a cutoff, work turned in after it is late: a student's current work is
    their latest on-time version, and a row is late only when all of it is."""
    for submission in found:
        moment = when(submission["submitted_at"])
        submission["late"] = bool(cutoff and moment and moment > cutoff)
        for f in submission["files"]:
            f["other_week"] = (None if submission.get('_historical_target') == f.get('assignment_form_id')
                               and f.get('historical_intake_sha256') else named_week(f.get("name"), lesson_week))
            f["from_folder"] = submission["source"] == "folder"
            f["from_shared"] = bool(submission.get("shared"))
            f["late"] = submission["late"]
    groups = {}
    for submission in found:
        student, how = identify(submission, students, domains, prefix)
        key = ("roster",) + student_key(student) if student else (
            "unmatched", submission["email"] or submission["key"])
        entry = groups.setdefault(key, {"student": student, "how": how, "items": []})
        entry["items"].append(submission)
    rows = []
    for entry in groups.values():
        items = sorted(entry["items"], key=lambda s: (not s["late"], _usable(s), s["source"] == "form",
                                                      s["submitted_at"]), reverse=True)
        student, current = entry["student"], items[0]
        rows.append({"matched": bool(student),
                     "last": student["last"] if student else "",
                     "first": student["first"] if student else "",
                     "section": student["section"] if student else "",
                     "matched_by": identify(current, students, domains, prefix)[1], "email": current["email"],
                     "typed": [] if student else current["typed"],
                     "source": current["source"], "submitted_at": current["submitted_at"],
                     "files": current["files"], "late": current["late"],
                     "earlier": [{"submitted_at": s["submitted_at"], "source": s["source"], "files": s["files"],
                                  "email": s['email'], "matched_by": identify(s, students, domains, prefix)[1],
                                  "late": s["late"]}
                                 for s in sorted(items[1:], key=lambda s: s["submitted_at"], reverse=True)]})
        problem = next((s['_lesson_selection_problem'] for s in items if s.get('_lesson_selection_problem')), None)
        if problem:
            rows[-1]['selection_problem'] = problem
    rows.sort(key=lambda r: (not r["matched"], r["section"], roster.plain(r["last"]),
                             roster.plain(r["first"]), r["email"], r["files"][0]["name"] if r["files"] else ""))
    if not with_missing:
        return rows, []
    done = {(r["section"], roster.plain(r["last"]), roster.plain(r["first"])) for r in rows if r["matched"]}
    missing = [{"last": s["last"], "first": s["first"], "section": s["section"], "added": s.get("added", "")}
               for s in students if student_key(s) not in done]
    missing.sort(key=lambda s: (s["section"], roster.plain(s["last"]), roster.plain(s["first"])))
    return rows, missing


def excuse_new(missing, began):
    """(still missing, excused): a student added to the roster after an
    assignment began (their "Added" date is later than its first day) is
    excused from it rather than given a 0."""
    if not began:
        return missing, []
    late_joiners = [m for m in missing if m.get("added") and m["added"] > began.isoformat()]
    return [m for m in missing if m not in late_joiners], late_joiners


def folder_week(name):
    """The week number in a folder name: '📥 Wk 03 — …', 'A&P — Wk 03 — …', '… Week 03 …'."""
    match = re.search(r"\b(?:wk|week)\s*0*(\d+)\b", str(name or ""), re.I)
    return int(match.group(1)) if match else None


def _upload_folder(name, numbers):
    name = str(name or "")
    return name.endswith("(File responses)") or folder_week(name) in _weeks(numbers)


def assignment_title(label):
    """'📤 Eyewitness Testimony — Turn in' → 'Eyewitness Testimony'."""
    text = re.sub(r"^[^\w(]+", "", str(label or ""))
    text = re.sub(r"\s*[—–-]+\s*turn\s*(it\s*)?in\s*$", "", text, flags=re.I)
    return text.strip() or "Turn in"


def lesson_folder_name(week_label, label):
    """The one folder per section per lesson: '📥 Wk 03 — Observation Skills'."""
    return "📥 %s — %s" % (week_label, assignment_title(label))


def _lesson_folder_title(name):
    return norm(re.sub(r"^\W*(?:wk|week)\s*\d+\s*[—–-]+\s*", "", str(name or ""), flags=re.I))


def lesson_folders(token, config, class_name, week_label, form_names, response_folders,
                   sections=(), titles=(), other_titles=()):
    """(roots, note): the lesson's turn-in folders inside 00 ALL TURN IN / <class>.

    A folder belongs to the lesson when its name carries the lesson's Agenda
    week number, or its title is the lesson's assignment title (so a lesson
    moved to another week keeps its folder under its old number), or it
    is one of the lesson's Forms' own '(File responses)' upload folders (found
    by the Form's title, or by where its responses were saved). A folder whose
    title names another lesson of the class is never taken for its week number
    alone. The same holds inside each section folder ('A2 Forensics').
    """
    root = config.get("HUB_SUBMISSIONS_ROOT_FOLDER", "")
    roots = {folder_id: "" for folder_id in response_folders}
    if not root:
        return roots, "No turn-in root folder is configured, so only Form uploads were read."
    number = week_number(week_label)
    wanted = {name + " (File responses)" for name in form_names if name}
    wanted_titles = {norm(assignment_title(t)) for t in titles if t}
    own = {t for t in wanted_titles if len(t) >= 6}
    others = {t for t in (norm(assignment_title(t)) for t in other_titles if t) if len(t) >= 6} - wanted_titles

    def titled(name, titles_set):
        title = _lesson_folder_title(name)
        return any(title.startswith(t) for t in titles_set)

    def ours(name):
        if titled(name, own):
            return True
        return number is not None and folder_week(name) == number and not titled(name, others)
    section_names = {norm(s) for s in sections}
    try:
        classes = [f for f in google.folder_children(token, root)
                   if f.get("mimeType") == google.FOLDER and norm(f["name"]) == norm(class_name)]
        if len(classes) != 1:
            return roots, "No single '%s' folder in the turn-in root, so only Form uploads were read." % class_name
        children = [f for f in google.folder_children(token, classes[0]["id"]) if f.get("mimeType") == google.FOLDER]
        for f in children:
            if f["name"] in wanted or (norm(f["name"]) not in section_names and ours(f["name"])):
                roots[f["id"]] = f["name"]
        for section in [f for f in children if norm(f["name"]) in section_names]:
            for f in google.folder_children(token, section["id"]):
                if f.get("mimeType") == google.FOLDER and (
                        ours(f["name"]) or _lesson_folder_title(f["name"]) in wanted_titles):
                    roots[f["id"]] = f["name"]
        # A Form's upload folder may also sit inside another week's folder.
        inside = {classes[0]["id"]} | {f["id"] for f in children}
        for name in wanted - set(roots.values()):
            for f in google.folders_named(token, name):
                if set(f.get("parents") or []) & inside:
                    roots[f["id"]] = f["name"]
    except google.GoogleError as error:
        return roots, "The %s turn-in folder couldn't be read (%s)." % (class_name, error)
    if not roots:
        return roots, "No turn-in folder for %s %s was found in %s." % (class_name, week_label, class_name)
    return roots, None


def list_tree(token, roots, *, require_complete=False):
    """(items, parents): everything inside the given folders, subfolders included."""
    items, parents, queue, seen = [], {}, list(roots), set()
    while queue and len(seen) < MAX_FOLDERS:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        for item in google.folder_children(token, current):
            if item["id"] in parents:
                continue
            parents[item["id"]] = current
            items.append(item)
            if item.get("mimeType") == google.FOLDER:
                queue.append(item["id"])
    if require_complete and queue:
        raise google.GoogleError("The declared Form upload folders exceeded the complete-read limit.")
    return items, parents


def name_time(name, created):
    """The submission time earlier routing wrote into copies' names
    ('s.first.last — 09-16 11.29 — …', Eastern time), as UTC ISO."""
    match = re.search(r"[—-]\s*(\d{2})-(\d{2})\s+(\d{2})\.(\d{2})\s*[—-]", str(name or ""))
    if not match or not re.match(r"\d{4}", str(created or "")):
        return None
    year = int(created[:4])
    month, day, hour, minute = (int(g) for g in match.groups())
    try:
        local = datetime(year, month, day, hour, minute)
    except ValueError:
        return None
    return (local + timedelta(hours=eastern_offset(local))).strftime("%Y-%m-%dT%H:%M:00Z")


def eastern_offset(local):
    """Hours behind UTC for a US Eastern local time: daylight time runs from
    the 2nd Sunday of March to the 1st Sunday of November."""
    year = local.year
    march = datetime(year, 3, 8) + timedelta(days=(6 - datetime(year, 3, 8).weekday()) % 7)
    november = datetime(year, 11, 1) + timedelta(days=(6 - datetime(year, 11, 1).weekday()) % 7)
    return 4 if march <= local < november else 5


def when(value):
    """A Google timestamp ('2026-09-22T14:00:00.123Z') as an aware datetime, or None."""
    match = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})$",
                     str(value or "").strip())
    if not match:
        return None
    base, fraction, zone = match.groups()
    try:
        return datetime.fromisoformat("%s.%s%s" % (base, (fraction or "0").ljust(6, "0")[:6],
                                                   "+00:00" if zone == "Z" else zone))
    except ValueError:
        return None


def school_day(moment):
    """The US Eastern calendar date of an aware UTC datetime."""
    naive = moment.astimezone(timezone.utc).replace(tzinfo=None)
    local = naive - timedelta(hours=4)
    if eastern_offset(local) == 5:
        local = naive - timedelta(hours=5)
    return local.date()


def _day_label(day):
    return "%s %d" % (day.strftime("%a %b"), day.day)


def window(found, not_before=None):
    """({opened, at, label} | None): an assignment stays open until the end of
    the day two weeks after the first turn-in (by a student on the class's
    roster; lesson_work passes only those), school time — but the
    two weeks never start before not_before, the Monday of the first Agenda
    week the assignment is in, so a student turning in early doesn't start
    the clock for everyone."""
    times = [t for t in (when(s["submitted_at"]) for s in found if s.get("event", True)) if t]
    if not times:
        return None
    first = school_day(min(times))
    if not_before and first < not_before:
        first = not_before
    last = first + timedelta(days=LATE_AFTER_DAYS)
    local = datetime(last.year, last.month, last.day, 23, 59, 59)
    at = (local + timedelta(hours=eastern_offset(local))).replace(tzinfo=timezone.utc)
    start = datetime(first.year, first.month, first.day)
    begins = (start + timedelta(hours=eastern_offset(start))).replace(tzinfo=timezone.utc)
    return {"opened": _day_label(first), "at": at, "label": _day_label(last) + ", 11:59 PM", "from": begins}


def week_monday(agenda, week_label):
    week = next((w for w in getattr(agenda, "weeks", []) if norm(w["label"]) == norm(week_label)), None)
    return date.fromisoformat(week["monday"]) if week else None


def _school_year(agenda, week_label):
    weeks = [w for w in getattr(agenda, "weeks", []) if w.get("label") == week_label]
    if len(weeks) != 1 or not weeks[0].get("monday"):
        return None
    monday = date.fromisoformat(weeks[0]["monday"])
    first = monday.year if monday.month >= 7 else monday.year - 1
    return "%d-%02d" % (first, (first + 1) % 100)


def _scoped_config(config, name, required, optional=()):
    raw = config.get(name)
    if not raw:
        return []
    try:
        values = json.loads(raw)
        if (not isinstance(values, list) or len(values) > 500
                or any(not isinstance(v, dict) or not required <= set(v) <= required | set(optional)
                       or not isinstance(v.get("class_key"), str)
                       or not isinstance(v.get("school_year"), str)
                       or not re.fullmatch(r"20\d\d-\d\d", v["school_year"])
                       or int(v["school_year"][-2:]) != (int(v["school_year"][:4]) + 1) % 100
                       for v in values)):
            raise ValueError
        return values
    except (ValueError, TypeError, KeyError):
        raise ValueError("The teacher's year-specific submission settings need checking before grading.") from None


def week_policy(config, agenda, class_key, week_label):
    """A teaching-year exclusion blocks assessment, never source discovery or filing.

    Only an exact Agenda week label in the named class and academic year applies.
    Week numbers in deck names and cycle names are not policy aliases.
    """
    out = {"excluded": False, "school_year": None, "reason": "", "problem": None}
    try:
        values = _scoped_config(config, "HUB_WEEK_EXCLUSIONS", {"school_year", "class_key", "weeks", "reason"})
        if not values:
            return out
        year = _school_year(agenda, week_label)
        for value in values:
            if (value["class_key"] not in agenda.classes or not isinstance(value["weeks"], list)
                    or not value["weeks"] or any(not isinstance(w, str) for w in value["weeks"])
                    or len(value["weeks"]) != len(set(value["weeks"]))
                    or not isinstance(value["reason"], str) or not value["reason"].strip()
                    or len(value["reason"]) > 300 or any(ord(c) < 32 for c in value["reason"])):
                raise ValueError
            labels = {label for _, label in (agenda.classes[value["class_key"]].get("layout") or {"rows": []})["rows"]}
            if value["school_year"] == year and any(w not in labels for w in value["weeks"]):
                raise ValueError
        out["school_year"] = year
        labels = {label for _, label in (agenda.classes[class_key].get("layout") or {"rows": []})["rows"]}
        if year is None and any(v["class_key"] == class_key and week_label in v["weeks"] for v in values):
            raise ValueError
        hits = [v for v in values if v["class_key"] == class_key and v["school_year"] == year
                and week_label in v["weeks"] and week_label in labels]
        if len(hits) > 1:
            raise ValueError
        if hits:
            out.update(excluded=True, reason=hits[0]["reason"].strip())
    except (ValueError, TypeError, KeyError):
        out["problem"] = "The teacher's year-specific week exclusions need checking before grading."
    return out



def _form_setting(config, name, extra, agenda, class_key, week_label, form_id):
    """One private declaration bound to a unique current Agenda Form."""
    fields = {"school_year", "agenda_id", "class_key", "week", "form_id"} | extra
    entries = _scoped_config(config, name, fields)
    keys = set()
    for entry in entries:
        if (entry["class_key"] not in agenda.classes
                or not isinstance(entry["week"], str) or not 1 <= len(entry["week"]) <= 100
                or any(not isinstance(entry[k], str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", entry[k])
                       for k in ("agenda_id", "form_id"))):
            raise ValueError("Exact Form settings need checking before grading.")
        key = tuple(entry[k] for k in ("school_year", "agenda_id", "class_key", "week", "form_id"))
        if key in keys:
            raise ValueError("Duplicate exact Form settings need checking before grading.")
        keys.add(key)
    matches = [e for e in entries if (e["agenda_id"], e["class_key"], e["week"], e["form_id"])
               == (agenda.spreadsheet_id, class_key, week_label, form_id)]
    if not matches:
        return None
    year = _school_year(agenda, week_label)
    if year is None:
        raise ValueError("The exact Form's school year could not be verified.")
    matches = [e for e in matches if e["school_year"] == year]
    if not matches:
        return None
    forms = [(lesson, f) for lesson in agenda.lessons(class_key, week_label)
             for f in lesson["forms"] if f.get("form_id") == form_id]
    if len(matches) != 1 or len(forms) != 1:
        raise ValueError("The exact Form's current Agenda scope is not unique.")
    return dict(matches[0])


def form_teacher_sources(config, agenda, class_key, week_label, form_id):
    """Exact registered teacher IDs, not a student's filename or row title."""
    entry = _form_setting(config, "HUB_FORM_TEACHER_SOURCES", {"source_folder_id", "teacher_ids"},
                          agenda, class_key, week_label, form_id)
    if entry and (not isinstance(entry["source_folder_id"], str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", entry["source_folder_id"])
            or not isinstance(entry["teacher_ids"], list) or not 1 <= len(entry["teacher_ids"]) <= 4
            or any(not isinstance(fid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", fid)
                   for fid in entry["teacher_ids"])
            or len(entry["teacher_ids"]) != len(set(entry["teacher_ids"]))):
        raise ValueError("The exact Form's teacher-source declaration is invalid.")
    return entry


def assignment_policy(config, agenda, class_key, week_label, form_id):
    """Named exact-Form exclusions leave other lessons and original history intact."""
    out = week_policy(config, agenda, class_key, week_label)
    if out.get("problem") or out.get("excluded"):
        return out
    try:
        entry = _form_setting(config, "HUB_ASSIGNMENT_EXCLUSIONS", {"purpose", "reason"},
                              agenda, class_key, week_label, form_id)
        if not entry:
            return out
        if entry["purpose"] == "reasoning_dealt_with":
            if (entry["school_year"] != "2026-27" or class_key != "forensics" or week_number(week_label) != 3
                    or not isinstance(entry["reason"], str) or not entry["reason"].strip()
                    or len(entry["reason"]) > 300 or any(ord(c) < 32 for c in entry["reason"])):
                raise ValueError()
            _, form = next((l, f) for l in agenda.lessons(class_key, week_label)
                           for f in l["forms"] if f.get("form_id") == form_id)
            if (form.get("problem") or not re.fullmatch(r"inductive\s+and\s+deductive\s+reasoning",
                    assignment_title(form.get("label", "")), re.I)):
                raise ValueError()
            return {**out, "excluded": True, "school_year": entry["school_year"],
                    "reason": entry["reason"].strip(), "purpose": entry["purpose"], "form_id": form_id}
        if (entry["purpose"] != "week01_cases" or class_key != "forensics" or week_number(week_label) != 1
                or not isinstance(entry["reason"], str) or not entry["reason"].strip()
                or len(entry["reason"]) > 300 or any(ord(c) < 32 for c in entry["reason"])):
            raise ValueError()
        lesson, form = next((l, f) for l in agenda.lessons(class_key, week_label)
                            for f in l["forms"] if f.get("form_id") == form_id)
        title = assignment_title(form.get("label", ""))
        if title.lower() == "turn in" or re.match(r"https?://", title, re.I):
            title = str(lesson.get("topic") or "").strip()
        if (re.search(r"\blab\s+safety\b", title, re.I)
                or not re.fullmatch(r"(?:work\s+a\s+case|(?:.*\b)?sheppard(?:\b.*)?)", title, re.I)):
            raise ValueError()
        return {**out, "excluded": True, "school_year": entry["school_year"],
                "reason": entry["reason"].strip(), "purpose": entry["purpose"], "form_id": form_id}
    except (ValueError, TypeError, KeyError, StopIteration):
        return {**out, "problem": "The teacher's exact assignment exclusion needs checking before grading."}


def _bind_form_teachers(token, config, agenda, class_key, week_label, forms):
    """Resolve configured teachers for discovery; never guess a replacement."""
    from . import completion
    for form in forms:
        if not form.get("form_id"):
            continue
        form.pop("_teacher_source_verified", None)
        form.pop('_teacher_source_resolution', None)
        form["grade_policy"] = assignment_policy(config, agenda, class_key, week_label, form["form_id"])
        try:
            binding = form_teacher_sources(config, agenda, class_key, week_label, form["form_id"])
            if not binding:
                continue
            form["teacher_source_binding"] = binding
            lesson = next(l for l in agenda.lessons(class_key, week_label)
                          if any(f.get("form_id") == form["form_id"] for f in l["forms"]))
            source = completion.lesson_sources(token, lesson, binding=binding)
            if source["problem"]:
                form["problem"] = source["problem"]
                continue
            form["teacher_signature"] = {deck_stem(d["title"]) for d in source["decks"] if len(deck_stem(d["title"])) >= 8}
            form["signature"] = set(form["teacher_signature"])
            if not form.get("problem"):
                form["_teacher_source_verified"] = True
                form['_teacher_source_resolution'] = deepcopy(source)
        except (completion.Unreadable, ValueError, TypeError, KeyError, StopIteration) as error:
            form["problem"] = str(error) or "The exact Form's teacher sources could not be verified."


def _teacher_ids(form):
    return ((form.get('teacher_source_binding') or {}).get('teacher_ids')
            or (form.get('teacher_source_direct') or {}).get('source_ids') or [])


def _direct_routing_source(token, config, agenda, cls, week, fid):
    """Reuse ordinary exact Agenda teacher ownership for routing, never grading."""
    from . import completion
    ownership = completion.form_source_ownership(config, agenda, cls, week, fid)
    lesson = completion.form_source_scope(agenda, cls, week, fid, ownership=ownership)
    resolved = completion.lesson_sources(token, lesson, metadata_fields=google.FILE_FIELDS + ',version')
    decks = resolved.get('decks', [])
    if resolved.get('problem') or len(decks) != 1:
        raise completion.Unreadable('The other lesson needs one exact readable Agenda teacher source.')
    meta = decks[0].get('metadata') or {}
    if (meta.get('id') != decks[0]['id'] or meta.get('mimeType') not in (completion.SLIDES, google.PPTX)
            or meta.get('trashed') is not False or not meta.get('version') or not meta.get('modifiedTime')):
        raise completion.Unreadable('The exact other-lesson teacher metadata is unavailable.')
    return {'source_ids': [decks[0]['id']], 'ownership': ownership}, resolved


def accepted_form_routes(config, agenda, class_key, week_label, forms):
    """Exact approved alternate intake Forms, separate from assignment identity.

    ``week`` is the assignment's Agenda week. Optional ``source_week`` names
    an explicitly approved other-week intake; it never changes a Form event.
    """
    try:
        values = _scoped_config(config, "HUB_ACCEPTED_FORM_ROUTES",
                                {"school_year", "class_key", "week", "source_form_id", "assignment_form_id"},
                                {"source_week"})
        year = _school_year(agenda, week_label) if values else None
        for value in values:
            if (value["class_key"] not in agenda.classes or not isinstance(value["week"], str)
                    or ("source_week" in value and not isinstance(value["source_week"], str))
                    or any(not isinstance(value[k], str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", value[k])
                           for k in ("source_form_id", "assignment_form_id"))
                    or value["source_form_id"] == value["assignment_form_id"]):
                raise ValueError
            labels = {label for _, label in (agenda.classes[value["class_key"]].get("layout") or {"rows": []})["rows"]}
            if value["school_year"] == year:
                source_week = value.get("source_week", value["week"])
                if value["week"] not in labels or source_week not in labels:
                    raise ValueError
                if source_week != value["week"]:
                    # A cross-week declaration must name two unique actual
                    # Agenda Forms in the same class and teaching year.
                    for fid, expected in ((value["source_form_id"], source_week),
                                          (value["assignment_form_id"], value["week"])):
                        scopes = [(cls, label) for cls, entry in agenda.classes.items()
                                  for label in dict.fromkeys(label for _, label in (entry.get("layout") or {"rows": []})["rows"])
                                  if _school_year(agenda, label) == year
                                  for lesson in agenda.lessons(cls, label) for form in lesson["forms"]
                                  if form.get("form_id") == fid]
                        if (scopes.count((value["class_key"], expected)) != 1
                                or any(cls != value["class_key"] for cls, _ in scopes)):
                            raise ValueError
        if not values:
            return set(), None
        ids = [form["form_id"] for form in forms if form.get("form_id")]
        relevant = [v for v in values if v["class_key"] == class_key and v["school_year"] == year
                    and week_label in (v["week"], v.get("source_week", v["week"]))]
        if year is None and any(v["class_key"] == class_key and
                               week_label in (v["week"], v.get("source_week", v["week"])) for v in values):
            raise ValueError
        routes = [(v["source_form_id"], v["assignment_form_id"]) for v in relevant]
        if len(routes) != len(set(routes)):
            raise ValueError
        for value in relevant:
            source_week = value.get("source_week", value["week"])
            current_ids = ([value["source_form_id"], value["assignment_form_id"]]
                           if source_week == value["week"] else
                           [value["source_form_id"] if week_label == source_week else value["assignment_form_id"]])
            if any(ids.count(fid) != 1 for fid in current_ids):
                raise ValueError
            if source_week != value["week"] and sum(
                    v["class_key"] == class_key and v["school_year"] == year
                    and v["source_form_id"] == value["source_form_id"] for v in values) != 1:
                raise ValueError
        return set(routes), None
    except (ValueError, TypeError, KeyError):
        return set(), "The teacher-approved alternate submission Form mapping needs checking before grading."


def _accepted_route_move(routes, entries, source_index, target_index, submission, students, domains, prefix, *, class_key, school_year, config):
    """Accept explicit cross-week intake only after the existing unique move.

    This flag suppresses a false source-week missing-work review. Target
    assessment, source/history guards and the original Form event stay intact.
    """
    source, target = entries[source_index], entries[target_index]
    source_id = source.get("form", {}).get("form_id") or source.get("form_id")
    target_id = target.get("form", {}).get("form_id") or target.get("form_id")
    if (source_id, target_id) not in routes:
        return False
    if source["week"] == target["week"]:
        return True  # Preserve the established same-week route contract.
    declarations = [v for v in json.loads(config["HUB_ACCEPTED_FORM_ROUTES"])
                    if (v["class_key"], v["school_year"], v["source_form_id"], v["assignment_form_id"])
                    == (class_key, school_year, source_id, target_id)]
    if (len(declarations) != 1 or declarations[0].get("source_week") != source["week"]
            or declarations[0]["week"] != target["week"]):
        return False
    files = submission.get("files") or []
    at = when(submission.get("submitted_at"))
    student, how = identify(submission, students, domains, prefix)
    if (not student or how != "email" or submission.get("source") != "form"
            or not submission.get("event") or not submission.get("key") or not at
            or not files or len({f.get("id") for f in files}) != len(files)
            or any(not e.get("current") and (e.get("class_key") != class_key
                   or e.get("school_year") != school_year) for e in (source, target))
            or source.get("problem") or target.get("problem")
            or source.get("form", {}).get("problem") or target.get("form", {}).get("problem")
            or any(e.get("teacher_source_binding") and not e.get("teacher_source_verified")
                   for e in (source, target))
            or any(sum((e.get("form", {}).get("form_id") or e.get("form_id")) == fid for e in entries) != 1
                   for fid in (source_id, target_id))):
        return False
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", str(f.get("id") or ""))
           or f.get("source_form_id") != source_id or f.get("source_response_id") != submission["key"]
           or when(f.get("source_submitted_at")) != at or f.get("trashed") is not False or f.get("problem")
           or not (any(len(known) >= 16 and known in deck_stem(f.get("name", ""))
                       for known in target.get("teacher_signature", ()))
                   or _corroborates_assignment_title(f, target)) for f in files):
        return False
    return any(i == source_index and s is submission and j == target_index
               for i, s, j in wrong_form_moves(entries))


def form_weeks(agenda, class_key):
    """{form id: the Monday of the first Agenda week whose row links it}."""
    entry = agenda.classes[class_key]
    starts = {}
    for label in dict.fromkeys(label for _, label in (entry["layout"] or {"rows": []})["rows"]):
        monday = week_monday(agenda, label)
        if not monday:
            continue
        for lesson in agenda.lessons(class_key, label):
            for form in lesson["forms"]:
                if form["form_id"] and (form["form_id"] not in starts or monday < starts[form["form_id"]]):
                    starts[form["form_id"]] = monday
    return starts


def deck_words(name):
    """The telling words of the deck a file was copied from, not the student's name:
    's.x — 09-16 11.29 — Copy of ▶ LIVE — Week 04 — Eyewitness Accounts (VT) - Ana B.pptx'
    → {'eyewitness', 'accounts'}."""
    text = re.sub(r"^\s*s\.[a-z0-9.\-]+\s+[—-]\s+\d{2}-\d{2}\s+\d{2}\.\d{2}\s+[—-]\s*", "", str(name or ""), flags=re.I)
    text = re.sub(r"\.(pptx?|pdf|docx?|key)$", "", text, flags=re.I)
    if " - " in text:
        text = text.rsplit(" - ", 1)[0]
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text).replace("_", " ").lower()
    return set(re.findall(r"[a-z]{5,}", text)) - GENERIC_WORDS


def lesson_words(topic, label, deck_names, found):
    """(base, words): what a lesson is called (its topic, its Form's label and its
    decks' titles), and that plus the words most of its matching turn-ins share.
    Only files that already name the lesson teach it words, so a pile of another
    lesson's decks turned in through its Form can't."""
    base = deck_words(topic) | deck_words(label)
    for name in deck_names:
        base |= deck_words(name)
    matching = [words for words in ({w for f in s["files"] for w in deck_words(f.get("name"))} for s in found)
                if words & base]
    counts = {}
    for words in matching:
        for word in words:
            counts[word] = counts.get(word, 0) + 1
    return base, base | {w for w, n in counts.items() if n >= 2 and 2 * n >= len(matching)}


def wrong_form_moves(entries, held=None):
    """[(from, submission, to)] for turn-ins that are another lesson's deck.

    entries are this week's Forms ("current": True) and nearby lessons' Forms,
    each with its "base" and "words" (see lesson_words) and "found". Every
    attached file must name one assignment's complete core title or match its
    paired teacher deck. One shared word is not enough. Only moves out of this
    week's Forms, or into them, are returned. Rejected former one-word imports
    go to held, when supplied, so they stay visible and do not become zeros.
    """
    seen = {}
    for entry in entries:
        for word in entry["words"]:
            seen[word] = seen.get(word, 0) + 1
    telling = [{w for w in entry["words"] if seen[w] == 1} for entry in entries]

    def confirms(submission, entry):
        files = submission.get("files") or []
        signatures = entry.get("teacher_signature") or ()
        return bool(files) and all(
            any(len(known) >= 8 and known in deck_stem(file.get("name", "")) for known in signatures)
            or _corroborates_assignment_title(file, entry)
            for file in files)

    moves = []
    for i, entry in enumerate(entries):
        # An explicit multi-deck Form is selected later from authenticated
        # cycles and exact teachers. Titles cannot move its events elsewhere.
        if entry.get('form', {}).get('_combined_scope'):
            continue
        for submission in entry["found"]:
            if submission.get('_historical_target'):
                continue  # Exact current target was verified from original event/content.
            words = {w for f in submission["files"] for w in deck_words(f.get("name"))}
            # Fresh exact Form teachers outrank nearby heuristic deck pairings.
            # Every attachment must agree; conflicting or partial claims stay put.
            claims = [[j for j, other in enumerate(entries)
                       if not other.get('form', {}).get('_combined_scope')
                       and other.get("teacher_source_verified") and _teacher_ids(other)
                       and any(len(known) >= 16 and known in deck_stem(file.get("name", ""))
                               for known in other.get("teacher_signature", ()))]
                      for file in submission.get("files", [])]
            if any(claims):
                if all(len(claim) == 1 for claim in claims) and len({claim[0] for claim in claims}) == 1:
                    target = claims[0][0]
                    if target != i and (entry["current"] or entries[target]["current"]):
                        moves.append((i, submission, target))
                elif any(entries[j].get('routing_context_only') for claim in claims for j in claim):
                    # A known other lesson is not a replacement merely because
                    # its event shares this Form. Mixed/tied claims stay local.
                    submission['_lesson_selection_problem'] = (
                        'This upload has conflicting or mixed lesson-source matches. '
                        'Its original files are retained; no replacement was selected.')
                continue
            if not words or confirms(submission, entry):
                continue
            hits = [j for j, other in enumerate(entries) if j != i
                    and not other.get('form', {}).get('_combined_scope') and confirms(submission, other)]
            if len(hits) == 1 and (entry["current"] or entries[hits[0]]["current"]):
                moves.append((i, submission, hits[0]))
            elif held is not None and not entry["current"]:
                weak = [j for j, other in enumerate(telling) if j != i
                        and not entries[j].get('form', {}).get('_combined_scope') and words & other]
                if len(weak) == 1 and entries[weak[0]]["current"]:
                    held.append((i, submission, weak[0]))
    return moves


_responses_cache = {}
_responses_lock = threading.Lock()


def recent_submissions(token, form_id):
    """A nearby Form's turn-ins, kept for RESPONSES_SECONDS."""
    with _responses_lock:
        hit = _responses_cache.get(form_id)
        if hit and time.time() - hit[0] < RESPONSES_SECONDS:
            return deepcopy(hit[1])
    found = submissions(google.form_responses(token, form_id), form_id)
    with _responses_lock:
        _responses_cache[form_id] = (time.time(), found)
    return deepcopy(found)


def nearby_forms(token, agenda, class_key, week_label, skip, *, config=None, current_forms=()):
    """[{week, label, topic, form_id, found}] for the Forms of the lessons up to
    NEARBY_LESSONS teaching weeks before and after this one."""
    layout = agenda.classes[class_key]["layout"] or {"rows": []}
    labels = [label for label in dict.fromkeys(label for _, label in layout["rows"])
              if any(lesson_kind(lesson["topic"]) == "lesson" for lesson in agenda.lessons(class_key, label))]
    here = next((i for i, label in enumerate(labels) if norm(label) == norm(week_label)), None)
    if here is None:
        return []
    out = []
    already_read = set(skip)
    for label in labels[max(0, here - NEARBY_LESSONS):here] + labels[here + 1:here + 1 + NEARBY_LESSONS]:
        for lesson in agenda.lessons(class_key, label):
            paired = [{"form_id": form["form_id"], "label": form["label"], "name": "", "signature": set()}
                      for form in lesson["forms"] if form["form_id"]]
            deck_ids = {m.group(1) for material in lesson["materials"]
                        for m in [re.search(r"/presentation/d/([A-Za-z0-9_-]{20,})", material["url"])] if m}
            names = [info.get("name", "") for info in _metadata(token, deck_ids).values()
                     if not info.get("trashed") and not info.get("problem")]
            if config is not None:
                _bind_form_teachers(token, config, agenda, class_key, label, paired)
            pair_decks(paired, [deck_stem(name) for name in names], names)
            for form in lesson["forms"]:
                fid = form['form_id']
                if not fid:
                    continue
                matched = next((f for f in paired if f['form_id'] == fid), {})
                context_only = fid in already_read
                if context_only:
                    from . import completion
                    current = [f for f in current_forms if f.get('form_id') == fid]
                    existing = [f for l in agenda.lessons(class_key, label) for f in l['forms']
                                if f.get('form_id') == fid]
                    if (len(current) == 1 and current[0].get('_teacher_source_verified')
                            and not matched.get('teacher_source_binding') and not matched.get('problem')):
                        try:
                            direct, resolved = _direct_routing_source(token, config, agenda, class_key, label, fid)
                            matched.update(teacher_source_direct=direct, _teacher_source_resolution=resolved,
                                _teacher_source_verified=True,
                                teacher_signature={deck_stem(d['title']) for d in resolved['decks']})
                        except (completion.Unreadable, google.GoogleError, ValueError, TypeError, KeyError):
                            pass
                    teachers = _teacher_ids(matched)
                    current_teachers = _teacher_ids(current[0]) if len(current) == 1 else []
                    year = _school_year(agenda, week_label)
                    if (len(current) != 1 or len(existing) != 1 or not year or _school_year(agenda, label) != year
                            or current[0].get('_combined_scope') or current[0].get('problem') or matched.get('problem')
                            or not current[0].get('_teacher_source_verified') or not matched.get('_teacher_source_verified')
                            or not teachers or not current_teachers
                            or set(teachers) & set(current_teachers)
                            or not any(len(stem) >= 16 for stem in matched.get('teacher_signature', ()))):
                        continue
                    # Resolve the existing lesson context, not another response
                    # collection or output Form. Reuse the current raw events.
                    found = []
                elif fid in skip:
                    continue
                else:
                    try:
                        found = recent_submissions(token, fid)
                    except google.GoogleError:
                        continue
                skip = skip | {fid}
                out.append({"week": label, "label": form["label"],
                            "class_key": class_key, "school_year": _school_year(agenda, label),
                            "problem": matched.get('problem'),
                            "topic": lesson["topic"] if len(lesson["forms"]) == 1 else "",
                            "form_id": fid, "found": found,
                            **({'routing_context_only': True} if context_only else {}),
                            'teacher_source_direct': matched.get('teacher_source_direct'),
                            'teacher_source_resolution': matched.get('_teacher_source_resolution'),
                            "teacher_signature": next((f.get("teacher_signature", set()) for f in paired
                                                       if f["form_id"] == form["form_id"]), set()),
                            "teacher_source_binding": next((f.get("teacher_source_binding") for f in paired
                                                             if f["form_id"] == form["form_id"]), None),
                            "teacher_source_verified": any(f.get("_teacher_source_verified") for f in paired
                                                           if f["form_id"] == form["form_id"])})
    return out


def deck_stem(name):
    """The deck a copy was made from: 's.x — 09-16 11.29 — Copy of ▶ LIVE — Deck - Name.pptx' → 'deck'."""
    text = re.sub(r"^\s*s\.[a-z0-9.\-]+\s+[—-]\s+\d{2}-\d{2}\s+\d{2}\.\d{2}\s+[—-]\s*", "", str(name or ""), flags=re.I)
    text = re.sub(r"\.(pptx?|pdf|docx?|key)$", "", text, flags=re.I)
    if " - " in text:
        text = text.rsplit(" - ", 1)[0]
    text = re.sub(r"^(?:\s*(?:copy of|new|live|▶|[—-])\s*)+", "", text, flags=re.I)
    return norm(text)


def _matches(stem, signature):
    """How strongly a file's deck stem matches a set of known deck names: the
    shared length when one contains the other, or a long shared beginning
    (students sometimes rename the end of their copy)."""
    best = 0
    for known in signature:
        if not known or not stem:
            continue
        if known in stem or (len(stem) >= 6 and stem in known):
            best = max(best, min(len(known), len(stem)))
            continue
        shared = len(os.path.commonprefix([known, stem]))
        if shared >= 16:
            best = max(best, shared)
    return best


def assignment_match_score(submission, form):
    """Score evidence that a submitted deck belongs to one Form assignment.

    A shared weekly folder can contain more than one lesson. Prefer the
    teacher deck paired to that Form, then signatures learned from uploads,
    then lesson-label words. Uploads can have arrived through the wrong Form.
    """
    files = submission.get("files") or []
    signatures = set(form.get("signature") or ())
    teacher_signatures = set(form.get("teacher_signature") or ())
    teacher_match, signature_match, label_match = 0, 0, 0
    label_words = deck_words(form.get("label", ""))
    for file in files:
        name = file.get("name", "")
        teacher_match = max(teacher_match, _matches(deck_stem(name), teacher_signatures))
        signature_match = max(signature_match, _matches(deck_stem(name), signatures))
        overlap = deck_words(name) & label_words
        if len(overlap) >= 2:
            label_match = max(label_match, len(overlap))
    # A known deck is stronger evidence than words shared by lesson labels.
    # For example Observation_Deductive_Reasoning shares two words with the
    # Reasoning label, but its full deck signature belongs to Observation.
    if teacher_match:
        return 2000 + min(teacher_match, 999)
    return 1000 + min(signature_match, 999) if signature_match else min(label_match, 999)


def _corroborates_assignment_title(file, form):
    """A complete telling title, beyond a few overlapping label words.

    Parenthetical descriptions such as '(Steps of CSI)' need not appear in
    copies of the deck. At least two core title words must all be present.
    """
    title = re.sub(r"\s*\([^)]*\)", "", assignment_title(form.get("label", "")))
    title_words = deck_words(title)
    return len(title_words) >= 2 and title_words <= deck_words(file.get("name", ""))


def assignment_for_submission(submission, forms):
    """The one assignment every attached file uniquely matches, or None.

    Keep mixed-lesson uploads and unresolved attachments in review rather
    than allowing one strongly matched file to assign the entire upload.
    """
    winner = None
    files = submission.get("files") or []
    if not files:
        return None
    for file in files:
        scores = [assignment_match_score({"files": [file]}, form) for form in forms]
        best = max(scores, default=0)
        matches = [i for i, score in enumerate(scores) if best and score == best]
        if len(matches) > 1 and 1000 <= best < 2000:
            # Swapped Form uploads may have taught both Forms the same deck.
            # Resolve only a uniquely corroborated full title; conflicting
            # teacher-deck evidence and partial label overlaps stay held.
            corroborated = [i for i in matches if _corroborates_assignment_title(file, forms[i])]
            if len(corroborated) == 1:
                matches = corroborated
        if len(matches) != 1 or (winner is not None and winner != matches[0]):
            return None
        winner = matches[0]
    return winner



def _nearby_match_context(agenda, class_key, week, entries, target_index, submission):
    """Describe an existing unique move; never accept intake or choose a grade."""
    target = entries[target_index]
    year = _school_year(agenda, week)
    fid = target.get("form_id")
    files = submission.get("files") or []
    if (target.get("current") or target.get("problem") or not year
            or target.get("class_key") != class_key or target.get("school_year") != year
            or not isinstance(fid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", fid)
            or sum((e.get("form", {}).get("form_id") or e.get("form_id")) == fid for e in entries) != 1
            or not files or any(not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", str(f.get("id") or "")) for f in files)
            or len({f["id"] for f in files}) != len(files)):
        return None
    actual = [f for lesson in agenda.lessons(class_key, target["week"]) for f in lesson["forms"]
              if f.get("form_id") == fid]
    if (_school_year(agenda, target["week"]) != year or len(actual) != 1
            or actual[0].get("label") != target["label"]):
        return None
    if not all(any(len(known) >= 16 and known in deck_stem(f.get("name", ""))
                   for known in target.get("teacher_signature", ()))
               or _corroborates_assignment_title(f, target) for f in files):
        return None
    return {"schema": "hub-nearby-lesson-match/v1", "matching_kind": "canonical_nearby_full_title",
            "class_key": class_key, "school_year": year, "week": target["week"],
            "form_id": fid, "lesson": target["label"], "source_file_ids": [f["id"] for f in files]}


def shared_folder_reviews(forms, roots, parents):
    """Review shared-folder ownership by exact assignment, never display label.

    Distinct Forms may share a name; repeated references to one Form are one
    assignment. Unverified Form entries stay separate and conservatively visible.
    Parent sharing and teacher-source verification alone do not prove a submitted
    artifact's identity or lesson allocation, so they cannot clear this review.
    """
    found = {}
    for index, form in enumerate(forms):
        form_id = form.get("form_id")
        identity = ("form", form_id) if isinstance(form_id, str) and form_id else ("unverified", str(index))
        for submission in form.get("found", []):
            for file in submission.get("files", []):
                file_id = file.get("id")
                if not file_id:
                    continue
                ancestors = set(_ancestors(file_id, parents))
                for folder_id, folder_name in roots.items():
                    if (not folder_name or folder_week(folder_name) is None
                            or "(file responses)" in str(folder_name).lower()):
                        continue
                    if folder_id in ancestors:
                        key = (folder_id, identity)
                        entry = found.setdefault(key, {"label": form.get("label") or "Turn in", "files": set()})
                        entry["files"].add(file_id)
    by_folder = {}
    for (folder_id, identity), entry in found.items():
        by_folder.setdefault(folder_id, {})[identity] = entry
    reviews = []
    for folder_id, assignments in by_folder.items():
        if len(assignments) < 2:
            continue
        ordered = sorted(assignments.items(), key=lambda item: (item[1]["label"], item[0]))
        detail = ["%s (%d file%s)" % (assignment_title(entry["label"]), len(entry["files"]),
                                      "" if len(entry["files"]) == 1 else "s")
                  for _, entry in ordered]
        reviews.append({"folder_name": roots[folder_id],
                        "folder_url": "https://drive.google.com/drive/folders/" + folder_id,
                        "assignments": [assignment_title(entry["label"]) for _, entry in ordered],
                        "message": ("This folder contains submitted work for multiple lessons: %s. "
                                    "Do both lessons belong in this folder? Files were left where they are."
                                    % "; ".join(detail))})
    return sorted(reviews, key=lambda item: item["folder_name"])


def _unreadable(error):
    if error.status in (403, 404):
        return {"problem": "The dashboard can't open this file, but you can."}
    return {"problem": str(error), **({"read_timeout": True} if google.read_timed_out(error) else {})}


def describe_files(token, submissions_list, known):
    """Fill in each file's Drive name, type, trash state and link, reusing
    metadata already read from the folder listing."""
    files = [f for s in submissions_list for f in s["files"]]
    missing = sorted({f["id"] for f in files if f["id"] not in known})

    def fetch(file_id):
        try:
            return google.drive_file(token, file_id)
        except google.GoogleError as error:
            return _unreadable(error)

    with ThreadPoolExecutor(max_workers=8) as pool:
        details = {**known, **dict(zip(missing, pool.map(fetch, missing)))}
    for f in files:
        info = details.get(f["id"], {})
        f["name"] = info.get("name") or f["name"] or "Uploaded file"
        f["shown"] = info.get("shown_name", "")  # its name in Drive, when Tidy has renamed it
        f["mime"] = info.get("mimeType") or f["mime"]
        f["trashed"] = bool(info.get("trashed"))
        f["problem"] = info.get("problem")
        if info.get("read_timeout"):
            f["read_timeout"] = True  # a transient read, never a missing or unavailable original
        else:
            f.pop("read_timeout", None)
        f["url"] = ("https://docs.google.com/presentation/d/%s/edit" % f["id"]
                    if f["mime"] == "application/vnd.google-apps.presentation"
                    else "https://drive.google.com/open?id=%s" % f["id"])


def form_problem(error, account):
    if error.status in (401, 403):
        return ("The dashboard can't read this Form's responses. Open the Form, choose "
                "⋮ → Add collaborators, and add %s as an editor." % account)
    if error.status == 404:
        return "This Form wasn't found. It may be deleted, or not shared with %s." % account
    return "This Form's responses couldn't be read: %s" % error


def _ancestors(item_id, parents):
    chain = []
    while item_id in parents and len(chain) < 20:
        item_id = parents[item_id]
        chain.append(item_id)
    return chain


def _metadata(token, file_ids):
    def fetch(file_id):
        try:
            return google.drive_file(token, file_id)
        except google.GoogleError as error:
            return _unreadable(error)
    ids = sorted(set(file_ids))
    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(zip(ids, pool.map(fetch, ids)))


def read_form(token, form, account):
    """One Form's responses and its Drive title (which names its upload folder)."""
    item = {"label": form["label"], "url": form["url"], "form_id": form["form_id"],
            "problem": form["problem"], "responses": 0, "found": [], "name": "", "folders": set()}
    if not form["form_id"]:
        return item
    try:
        item["found"] = submissions(google.form_responses(token, form["form_id"]), form["form_id"])
    except google.GoogleError as error:
        item["problem"] = form_problem(error, account)
        item["read_timeout"] = google.read_timed_out(error)
        return item
    item["responses"] = len(item["found"])
    try:
        metadata = google.drive_file(token, form["form_id"])
        item["name"] = metadata.get("name", "")
        if (metadata.get("id") == form["form_id"] and metadata.get("mimeType") == "application/vnd.google-apps.form"
                and metadata.get("trashed") is False):
            item["_upload_form_verified"] = True
    except google.GoogleError:
        pass
    return item


def _declared_upload_folders(token, forms):
    """Exact linked Form questions authorize only their own upload roots.

    Used when the existing response/name discovery found no roots. Reading
    a Form's declared folder never authorizes traversal of its parent.
    The same structure and folder metadata are checked again after listing.
    """
    from school_workflow_hub.adapters.assignment_form_intake import upload_folders
    from school_workflow_hub.adapters.hosted_agenda import ConnectionError
    fields = "formId,revisionId,items(questionItem(question(fileUploadQuestion(folderId))))"
    metadata_fields = "id,name,mimeType,trashed,parents,version,modifiedTime"
    basis, owners = {}, {}
    if len(forms) > 20:
        raise google.GoogleError("Too many linked Forms to verify their upload folders completely.")
    if len({f["form_id"] for f in forms}) != len(forms):
        raise google.GoogleError("Duplicate linked Form scopes prevent verified upload-folder ownership.")
    for form in forms:
        fid = form["form_id"]
        if form.get("problem") or not form.get("_upload_form_verified"):
            raise google.GoogleError("An exact linked Form could not be verified for upload-folder discovery.")
        form_metadata = google.drive_file(token, fid, metadata_fields)
        if (not isinstance(form_metadata, dict) or form_metadata.get("id") != fid
                or form_metadata.get("mimeType") != "application/vnd.google-apps.form"
                or form_metadata.get("trashed") is not False or not form_metadata.get("version") or not form_metadata.get("modifiedTime")):
            raise google.GoogleError("The exact linked Form's current metadata could not be verified.")
        info = google.get_json("https://forms.googleapis.com/v1/forms/" + fid, token, {"fields": fields})
        if not isinstance(info, dict) or info.get("formId") != fid or not isinstance(info.get("revisionId"), str) or not info["revisionId"]:
            raise google.GoogleError("The linked Form's current upload-folder declaration could not be verified.")
        try:
            folder_ids = upload_folders(info)
        except ConnectionError:
            raise google.GoogleError("The linked Form's upload-folder declaration is incomplete.") from None
        if not folder_ids:
            raise google.GoogleError("The linked Form has no verified file-upload folder declaration.")
        folders = {}
        for folder_id in folder_ids:
            if folder_id in owners and owners[folder_id] != fid:
                raise google.GoogleError("Multiple linked Forms declare the same upload folder; assignment ownership needs review.")
            metadata = google.drive_file(token, folder_id, metadata_fields)
            if (not isinstance(metadata, dict) or metadata.get("id") != folder_id or metadata.get("mimeType") != google.FOLDER
                    or metadata.get("trashed") is not False or not metadata.get("version") or not metadata.get("modifiedTime")):
                raise google.GoogleError("The exact declared upload folder is unavailable or cannot be verified as current.")
            folders[folder_id] = metadata
            owners[folder_id] = fid
            if len(owners) > MAX_FOLDERS:
                raise google.GoogleError("Too many declared Form upload folders to verify completely.")
        basis[fid] = {"form": info, "form_metadata": form_metadata, "folders": folders}
    return basis


def _declared_roots_for(item_id, parents, roots):
    """Walk only the known declared tree; no provider or parent traversal."""
    owned, seen = set(), set()
    for _ in range(MAX_FOLDERS + 2):
        if item_id in seen:
            raise google.GoogleError("The declared upload-folder hierarchy is cyclic.")
        seen.add(item_id)
        if item_id in roots:
            owned.add(item_id)
        if item_id not in parents:
            return owned
        item_id = parents[item_id]
    raise google.GoogleError("The declared upload-folder hierarchy exceeded the complete-read limit.")


def _words(text):
    return {w for w in re.findall(r"[a-z]{4,}", str(text or "").lower())} - {"turn", "copy", "live", "week"}


def pair_decks(forms, decks, names=None):
    """Give each lesson deck to the Form it most plausibly belongs to.

    When the lesson has as many decks as readable Forms, every deck is paired
    with a Form at once by the pairing whose names share the most words in
    all (a deck's own name can share words with another Form's, so pairs are
    judged together, not one at a time). Otherwise a deck with one complete
    assignment-title match is paired to it, and remaining unclaimed decks go
    to empty Forms by the shared-words rule. Ambiguous pairings are left
    alone. Paired teacher decks are kept separately from learned signatures
    so wrong-Form uploads cannot outweigh them. names are the decks' Drive
    titles, in the same order, for their words ("Observation_Deductive…").
    """
    import itertools

    def paired(form, deck):
        form["signature"].add(deck)
        form.setdefault("teacher_signature", set()).add(deck)

    names = list(names) if names is not None else list(decks)
    words = {d: _words(n.replace("_", " ")) for d, n in zip(decks, names)}
    readable = [f for f in forms if f["form_id"] and not f.get("teacher_source_binding")]
    if len(readable) == 1:
        for deck in decks:
            paired(readable[0], deck)
        return
    if 1 < len(readable) == len(decks) <= 4:
        scored = sorted(((sum(len(_words(f["label"] + " " + f["name"]) & words[d]) for f, d in zip(readable, order)),
                          order) for order in itertools.permutations(decks)), key=lambda s: s[0], reverse=True)
        if scored[0][0] > 0 and scored[0][0] > scored[1][0]:
            for form, deck in zip(readable, scored[0][1]):
                paired(form, deck)
            return
    # A lesson can link fewer teacher decks than Forms, and wrong-Form
    # uploads can leave every learned signature populated. A full unique
    # title still identifies a teacher deck without relying on an empty set.
    for deck, name in zip(decks, names):
        owners = [f for f in readable if _corroborates_assignment_title({"name": name}, f)]
        if len(owners) == 1:
            paired(owners[0], deck)
    empty = [f for f in readable if not f["signature"]]
    unclaimed = [d for d in decks if not any(_matches(d, f["signature"]) for f in forms)]
    if not empty or not unclaimed:
        return
    if len(empty) == 1 and len(unclaimed) == 1:
        paired(empty[0], unclaimed[0])
        return
    if len(empty) != len(unclaimed) or len(empty) > 4:
        return
    scored = []
    for order in itertools.permutations(unclaimed):
        scored.append((sum(len(_words(f["label"] + " " + f["name"]) & words[d])
                           for f, d in zip(empty, order)), order))
    scored.sort(key=lambda s: s[0], reverse=True)
    if scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
        for form, deck in zip(empty, scored[0][1]):
            paired(form, deck)


SHARED_KEY = re.compile(r"^\s*(.+?)\s*·\s*wk\s*0*(\d+)\b", re.I)
SHARED_CACHE_SECONDS = 60
_shared_cache = {}


def shared_submissions(token, config, class_name, number):
    """([submission], note): responses to the earlier shared turn-in Form that
    name this course and week in their assignment field
    ('🔬 Forensics · Wk 03 — …'). Their files count here wherever a script
    later moved them, and their respondent email identifies the student."""
    form_id = str(config.get("HUB_TURNIN_FORM_ID", "") or "").strip()
    if not form_id or number is None:
        return [], None
    import time
    cached = _shared_cache.get(form_id)
    if cached and time.time() - cached[0] < SHARED_CACHE_SECONDS:
        responses = cached[1]
    else:
        try:
            responses = google.form_responses(token, form_id)
        except google.GoogleError as error:
            if error.status == 404:
                return [], None  # the Form was deleted; its script had already filed its uploads
            return [], "The earlier shared turn-in Form couldn't be read (%s), so its uploads may be missing." % error
        _shared_cache[form_id] = (time.time(), responses)
    found = []
    for submission in submissions(responses, form_id):
        for text in submission["typed"]:
            match = SHARED_KEY.match(text)
            if match and norm(match.group(1)) == norm(class_name) and int(match.group(2)) == number:
                submission["shared"] = True
                submission["typed"] = [t for t in submission["typed"] if t != text]
                if submission["files"]:
                    found.append(submission)
                break
    return found, None


def _now():
    return datetime.now(timezone.utc)


def roster_check(token, config, by_class, class_key, lessons, other_rows, domains=(), prefix="s."):
    """The roster pass run with every load. It flags work turned in during the
    last week from someone who is on another class's roster (turned in to the
    wrong class?) or on no roster (new, left, or a visitor), anyone listed
    twice, and says how many students each section has and when the Rosters
    sheet last changed. Flags about older work are dropped."""
    students = by_class.get(class_key, [])
    sections = {name: 0 for name in roster.SECTIONS[class_key]}
    listed = {}
    for student in students:
        sections[student["section"]] = sections.get(student["section"], 0) + 1
        listed.setdefault((roster.plain(student["last"]), roster.plain(student["first"])), []).append(student)
    flags = [{"kind": "twice", "who": "%s, %s" % (same[0]["last"], same[0]["first"]),
              "section": " and ".join(sorted({s["section"] for s in same})), "assignment": "", "file": "", "url": ""}
             for same in listed.values() if len(same) > 1]
    places = [(form["label"], row) for lesson in lessons for form in lesson["forms"]
              for row in form["students"] + form["late"]] + [("Not sorted", row) for row in other_rows]
    recent = _now() - timedelta(days=ROSTER_FLAG_DAYS)
    for label, row in places:
        if row["matched"]:
            continue
        turned_in = when(row["submitted_at"])
        if not turned_in or turned_in < recent:
            continue
        probe = {"email": row["email"], "files": row["files"], "typed": row["typed"]}
        elsewhere = next((student for key, others in by_class.items() if key != class_key
                          for student in [identify(probe, others, domains, prefix)[0]] if student), None)
        first = row["files"][0] if row["files"] else {}
        if elsewhere:
            row["roster_note"] = "On the %s roster" % elsewhere["section"]
            who = "%s, %s" % (elsewhere["last"], elsewhere["first"])
        else:
            row["roster_note"] = "Not on any class roster"
            who = row["email"] or first.get("name", "") or "Unknown"
        flags.append({"kind": "other_class" if elsewhere else "no_roster", "who": who,
                      "section": elsewhere["section"] if elsewhere else "", "assignment": label,
                      "file": first.get("name", ""), "url": first.get("url", "")})
    updated, stale = None, False
    sheet_id = str(config.get("HUB_ROSTER_SPREADSHEET_ID", "") or "")
    if sheet_id:
        try:
            modified = when(google.drive_file(token, sheet_id, "id,modifiedTime").get("modifiedTime"))
        except google.GoogleError:
            modified = None
        if modified:
            updated = modified.strftime("%Y-%m-%dT%H:%M:%SZ")
            stale = datetime.now(timezone.utc) - modified > timedelta(days=ROSTER_STALE_DAYS)
    return {"sections": sections, "flags": flags, "updated_at": updated, "stale": stale,
            "stale_days": ROSTER_STALE_DAYS}


def _calendar_only(lesson):
    """Narrow filing metadata from the trusted Agenda, never a scoring gate."""
    if lesson.get("forms") or lesson.get("materials") or any(str(lesson.get(k) or "").strip() for k in ("todo", "prep")):
        return False
    if any(str(p.get("note") or "").strip() for p in lesson.get("plans", [])):
        return False
    plans = [" ".join(str(p.get("plan") or "").split()) for p in lesson.get("plans", [])]
    plans = [p for p in plans if p]
    topic = str(lesson.get("topic") or "").strip()
    if topic:
        return (norm(topic) in {"30minclass", "30minclasses"} and not plans
                and norm(lesson.get("dates")) in {"dayone9thonly", "day2allgrades"})
    calendar = re.compile(
        r"(?:\d{1,2}/\d{1,2}\s*)?(?:OFF\s*[—–-]\s*(?:Schools Closed|In-Service)|A&P does not meet\.)"
        r"|\d{1,2}/\d{1,2}\s*[—–-]\s*A Friday, Bio \+ Forensics have it\. A&P does not meet\. "
        r"\(Not a real closure\s*[—–-]\s*only Mon \d{1,2}/\d{1,2} was closed\.\)", re.I)
    return all(calendar.fullmatch(p) for p in plans)


def lesson_work(token, config, agenda, class_key, week_label, *, review_only=False, roster_snapshot=None):
    from . import completion
    if roster_snapshot is not None:
        if not review_only or not isinstance(roster_snapshot, tuple) or len(roster_snapshot) != 2:
            raise ValueError('review roster snapshot')
        # Discovery may orient its local rows. Never mutate the shared overview
        # snapshot or lend this hook to the normal grading path.
        by_class, problems = deepcopy(roster_snapshot)
    else:
        by_class, problems = roster.load(token, config)
    if review_only and (problems or not by_class.get(class_key)):
        return {'class_key': class_key, 'week': week_label, 'lessons': [],
                'review_unavailable': problems or ['The current class roster could not be verified.'],
                'other_files': [], 'problems': problems}
    students = by_class.get(class_key, [])
    domains = {d.strip().lower() for d in config.get("HUB_SCHOOL_EMAIL_DOMAINS", "").split(",") if d.strip()}
    prefix = config.get("HUB_EMAIL_USERNAME_PREFIX", "s.")
    # Typed read-only GET timeouts, so a review can say "timed out, retry"
    # instead of treating a slow provider as missing or unavailable work.
    read_timeouts = set()
    account = google.service_account(config)
    class_name = agenda.classes[class_key]["name"]
    lessons = agenda.lessons(class_key, week_label)
    policy = week_policy(config, agenda, class_key, week_label)
    if policy["problem"]:
        problems.append(policy["problem"])
    number = week_number(week_label)
    if not lessons:
        problems.append("No %s row labelled '%s' was found in the Agenda." % (class_name, week_label))
        return {"class_key": class_key, "week": week_label, "week_policy": policy, "lessons": [], "other_files": [],
                "roster_check": None,
                "folder_reviews": [],
                "folders": [], "folder_files": 0, "folder_note": None,
                "sections": list(roster.SECTIONS[class_key]), "roster_size": len(students), "problems": problems}

    forms = [read_form(token, form, account) for lesson in lessons for form in lesson["forms"]]
    _bind_form_teachers(token, config, agenda, class_key, week_label, forms)
    accepted_routes, route_problem = accepted_form_routes(config, agenda, class_key, week_label, forms)
    from . import combined_assessment
    try:
        for form in forms:
            scope = combined_assessment.definition(config, agenda, class_key, week_label, form['form_id'])
            if scope:
                form['_combined_scope'] = scope
    except combined_assessment.Held as error:
        route_problem = str(error)
    if route_problem:
        problems.append(route_problem)
    from . import historical_intake
    historical_reserved = set()
    try:
        historical_reserved = historical_intake.attach(token, config, agenda, class_key, week_label,
            forms, students, roster_problems=problems)
    except historical_intake.Unverified as error:
        route_problem = str(error)
        problems.append(route_problem)
    # A lesson moved to another week keeps its deck's name ("… Week 04 …"), so
    # its weeks are its Agenda week and the week its own decks are named for.
    deck_ids = {m.group(1) for lesson in lessons for material in lesson["materials"]
                for m in [re.search(r"/presentation/d/([A-Za-z0-9_-]{20,})", material["url"])] if m}
    numbers = _weeks(number) | {folder_week(info.get("name", "")) for info in _metadata(token, deck_ids).values()
                              if not info.get("trashed") and not info.get("problem")}
    numbers.discard(None)
    layout = agenda.classes[class_key]["layout"] or {"rows": []}
    other_titles = [f["label"] for label in {lbl for _, lbl in layout["rows"] if norm(lbl) != norm(week_label)}
                    for lesson in agenda.lessons(class_key, label) for f in lesson["forms"]]
    response_ids = [f["id"] for form in forms for s in form["found"] for f in s["files"]]
    known = _metadata(token, response_ids)
    # The earlier shared Form's uploads for this course and week. Their folders
    # are never read as lesson folders: that Form's folder holds every week.
    shared, shared_note = shared_submissions(token, config, class_name, number)
    shared_ids = [f["id"] for s in shared for f in s["files"] if f["id"] not in set(response_ids)]
    shared = [s for s in shared if any(f["id"] in shared_ids for f in s["files"])]
    # Folders holding the Form's uploads count only when they are Form upload
    # folders or named for this lesson's week — never the class folder itself,
    # even when uploads were moved loose into it or into the Form's folder.
    holders = {fid: info for fid, info in _metadata(
        token, [p for info in known.values() for p in info.get("parents") or []]).items()
        if _upload_folder(info.get("name"), numbers)}
    outer_ids = {p for info in holders.values() if info.get("name", "").endswith("(File responses)")
                 for p in info.get("parents") or []} - set(holders)
    outer = {fid: info for fid, info in _metadata(token, outer_ids).items()
             if info.get("name", "").endswith("(File responses)")}
    upload_folders = {**holders, **outer}
    response_folders = set(upload_folders)

    roots, folder_note = lesson_folders(token, config, class_name, week_label,
                                        [f["name"] for f in forms], response_folders,
                                        roster.SECTIONS[class_key], [f["label"] for f in forms],
                                        other_titles)
    declared, declared_members = {}, {}
    if not roots and forms and any(f.get("_upload_form_verified") for f in forms):
        try:
            declared = _declared_upload_folders(token, forms)
            roots = {fid: info.get("name", "") for entry in declared.values() for fid, info in entry["folders"].items()}
            folder_note = None
        except google.GoogleError as error:
            folder_note = "The linked Forms' declared upload folders couldn't be verified (%s). Only readable Form uploads were read." % error
            if google.read_timed_out(error):
                read_timeouts.add("declared_upload_folders")
    try:
        items, parents = list_tree(token, roots, require_complete=bool(declared))
        if declared:
            for entry in declared.values():
                for fid, info in entry["folders"].items():
                    if fid not in parents and info.get("parents"):
                        parents[fid] = info["parents"][0]
            declared_members = {fid: owned for fid in set(roots) | {i["id"] for i in items}
                                if (owned := _declared_roots_for(fid, parents, roots))}
            if any(len(owned) != 1 for owned in declared_members.values()):
                raise google.GoogleError("Linked Forms' declared upload trees overlap; assignment ownership needs review.")
        if declared and _declared_upload_folders(token, forms) != declared:
            raise google.GoogleError("The Form declaration or upload-folder metadata changed during discovery.")
    except google.GoogleError as error:
        items, parents = [], {}
        folder_note = "The turn-in folders couldn't be read (%s), so only Form uploads were read." % error
        if google.read_timed_out(error):
            read_timeouts.add("turn_in_folders")
        if declared:
            roots, declared, declared_members = {}, {}, {}
    for fid, info in list(known.items()) + list(upload_folders.items()):
        if fid not in parents and info.get("parents"):
            parents[fid] = info["parents"][0]
    names = {i["id"]: i["name"] for i in items if i.get("mimeType") == google.FOLDER}
    names.update({fid: name for fid, name in roots.items() if name})
    names.update({fid: info.get("name", "") for fid, info in upload_folders.items()})
    known = {**{i["id"]: i for i in items}, **known}
    known.update(_metadata(token, [fid for fid in shared_ids if fid not in known]))
    for fid in shared_ids:
        if fid not in parents and known.get(fid, {}).get("parents"):
            parents[fid] = known[fid]["parents"][0]

    # Which folders hold each Form's uploads: its titled folder, and wherever its
    # responses were saved (plus the Form folder around that question folder).
    # A section's lesson folder named for one assignment belongs to that Form.
    declared_tree = set(declared_members)
    for fid, name in roots.items():
        if fid in declared_tree:
            continue
        title = _lesson_folder_title(name)
        owners = [f for f in forms if f["form_id"] and title and title == norm(assignment_title(f["label"]))]
        if len(owners) == 1:
            owners[0].setdefault("lesson_folders", set()).add(fid)
    for form in forms:
        mine = {fid for fid, name in names.items() if fid not in declared_tree
                and form["name"] and name == form["name"] + " (File responses)"}
        mine |= set(declared.get(form["form_id"], {}).get("folders", {}))
        mine |= form.get("lesson_folders", set())
        questions = {p for s in form["found"] for f in s["files"]
                     for p in known.get(f["id"], {}).get("parents") or []} & set(holders)
        mine |= questions
        mine |= {p for q in questions for p in holders[q].get("parents") or [] if p in outer}
        form["folders"] = mine

    # Preserve response_ids for folder de-duplication, but remove proven
    # teacher material before names/signatures can affect student routing.
    from . import teacher_material
    material_problem = teacher_material.classify_entries(token, config, agenda, class_key,
        [{'current':True, 'form':f, 'week':week_label, 'found':f['found']} for f in forms])
    if material_problem:
        problems.append(material_problem)
        route_problem = material_problem

    # Settle each roster section's first/last column order from the school
    # usernames in this lesson's submissions (only where it was assumed).
    usernames = {roster.username_key(s["email"], prefix) for form in forms for s in form["found"] + shared
                 if s["email"]}
    usernames |= {roster.username_key(u, prefix) for i in items
                  for u in [roster.file_username(i.get("name"), prefix)] if u}
    roster.orient(students, usernames - {""})

    referenced = set(response_ids) | set(shared_ids) | historical_reserved
    readable = [form for form in forms if form["form_id"]]
    pending, other = [], []
    loose = [(entry["id"], folder_submission(entry)) for entry in items
             if entry["id"] not in referenced and entry.get("mimeType") in STUDENT_WORK
             and not NOT_STUDENT_WORK.search(entry.get("name", ""))]
    loose += [(s["files"][0]["id"], s) for s in shared]
    for file_id, submission in loose:
        chain = set(_ancestors(file_id, parents))
        owners = ([form for form in forms if set(declared.get(form["form_id"], {}).get("folders", {})) & declared_members[file_id]]
                  if file_id in declared_members else [form for form in forms if form["folders"] & chain])
        if len(owners) == 1:
            owners[0]["found"].append(submission)
        elif len(readable) == 1:
            readable[0]["found"].append(submission)
        else:
            pending.append(submission)

    if pending:
        # Several Forms and files in shared folders: sort by the deck each file
        # was copied from, learned from each Form's own files and the lesson's deck links.
        for form in forms:
            form["signature"] = (set(form.get("teacher_signature", ())) if form.get("teacher_source_binding") else
                                 {stem for s in form["found"] for f in s["files"]
                                  for stem in [deck_stem(known.get(f["id"], {}).get("name") or f["name"])]
                                  if len(stem) >= 8})
        index = 0
        for lesson in lessons:
            lesson_forms = forms[index:index + len(lesson["forms"])]
            index += len(lesson["forms"])
            deck_ids = {m.group(1) for material in lesson["materials"]
                        for m in [re.search(r"/presentation/d/([A-Za-z0-9_-]{20,})", material["url"])] if m}
            named = [(deck_stem(info.get("name", "")), info.get("name", "")) for info in _metadata(token, deck_ids).values()
                     if not info.get("trashed") and not info.get("problem")]
            named = [(stem, name) for stem, name in named if len(stem) >= 8]
            pair_decks(lesson_forms, [stem for stem, _ in named], [name for _, name in named])
        for submission in pending:
            for file in submission.get("files", []):
                file["name"] = known.get(file.get("id"), {}).get("name") or file.get("name", "")
            assignment = assignment_for_submission(submission, forms)
            if assignment is not None:
                forms[assignment]["found"].append(submission)
            else:
                other.append(submission)

    # A turn-in that is another lesson's deck (through the wrong Form, or in the
    # wrong week's folder) isn't counted here; one made through a nearby
    # lesson's Form that is this lesson's deck is counted here, marked "via".
    entries, index = [], 0
    for lesson in lessons:
        deck_ids = {m.group(1) for material in lesson["materials"]
                    for m in [re.search(r"/presentation/d/([A-Za-z0-9_-]{20,})", material["url"])] if m}
        titles = [info.get("name", "") for info in _metadata(token, deck_ids).values()
                  if not info.get("trashed") and not info.get("problem")]
        lesson_forms = forms[index:index + len(lesson["forms"])]
        for form in lesson_forms:
            form.setdefault("signature", set())
        pair_decks(lesson_forms, [deck_stem(title) for title in titles], titles)
        alone = len(lesson["forms"]) == 1  # a row's topic and decks describe its Forms only when it has one
        for form in lesson_forms:
            base, words = lesson_words(lesson["topic"] if alone else "", form["label"], titles if alone else [],
                                       form["found"])
            entries.append({"current": True, "form": form, "week": week_label, "label": form["label"],
                            "found": form["found"], "base": base, "words": words,
                            "teacher_signature": form.get("teacher_signature", set()),
                            "teacher_source_binding": form.get("teacher_source_binding"),
                            'teacher_source_resolution': form.get('_teacher_source_resolution'),
                            "teacher_source_verified": bool(form.get("_teacher_source_verified"))})
        index += len(lesson["forms"])
    for near in nearby_forms(token, agenda, class_key, week_label, {f["form_id"] for f in forms if f["form_id"]},
                             config=config, current_forms=forms):
        material_problem = teacher_material.classify_entries(token, config, agenda, class_key,
            [{**near, 'current':False}])
        if material_problem:
            problems.append(material_problem)
            route_problem = material_problem
        base, words = lesson_words(near["topic"], near["label"], [], near["found"])
        entries.append({"current": False, "week": near["week"], "label": near["label"], "found": near["found"],
                        "base": base, "words": words,
                        "teacher_signature": near.get("teacher_signature", set()),
                        "teacher_source_binding": near.get("teacher_source_binding"),
                        'teacher_source_direct': near.get('teacher_source_direct'),
                        'teacher_source_resolution': near.get('teacher_source_resolution'),
                        "teacher_source_verified": bool(near.get("teacher_source_verified")),
                        "form_id": near["form_id"], "class_key": near["class_key"],
                        "school_year": near["school_year"], "problem": near.get("problem"),
                        **({'routing_context_only': True} if near.get('routing_context_only') else {})})
    held_imports = []
    moves = wrong_form_moves(entries, held_imports)
    routing_contexts = {index for i, _, j in moves if entries[j].get('routing_context_only') for index in (i, j)}
    changed_contexts = set()
    for index in routing_contexts:
        entry = entries[index]
        try:
            fid = entry.get('form', {}).get('form_id') or entry.get('form_id')
            if entry.get('teacher_source_direct'):
                direct, fresh = _direct_routing_source(token, config, agenda, class_key, entry['week'], fid)
                if direct != entry['teacher_source_direct']:
                    changed_contexts.add(index)
            else:
                lesson = completion.form_source_scope(agenda, class_key, entry['week'], fid,
                    binding=entry.get('teacher_source_binding'))
                fresh = completion.scoped_lesson_sources(token, lesson, binding=entry.get('teacher_source_binding'))
            if fresh.get('problem') or fresh != entry.get('teacher_source_resolution'):
                changed_contexts.add(index)
        except (completion.Unreadable, google.GoogleError, ValueError, TypeError, KeyError):
            changed_contexts.add(index)
    for i, submission, j in moves:
        if entries[j].get('routing_context_only') and ({i, j} & changed_contexts):
            submission['_lesson_selection_problem'] = (
                'The exact lesson sources changed or could not be verified during selection. '
                'Original submissions are retained; no replacement was selected.')
            continue
        source, target = entries[i], entries[j]
        if submission not in source["found"]:
            continue
        pair = (source.get("form", {}).get("form_id") or source.get("form_id"),
                target.get("form", {}).get("form_id") or target.get("form_id"))
        if pair in accepted_routes and source["week"] != target["week"]:
            known.update(_metadata(token, [f["id"] for f in submission["files"] if f["id"] not in known]))
            describe_files(token, [submission], known)
        accepted = _accepted_route_move(accepted_routes, entries, i, j, submission, students, domains, prefix,
                                        class_key=class_key, school_year=_school_year(agenda, week_label), config=config)
        source["found"].remove(submission)
        if source["current"]:
            source["form"].setdefault("moved_out", []).append(
                {"submission": submission, "to": "%s · %s" % (target["week"], target["label"]),
                 "accepted_intake": accepted, "source_form_id": source["form"]["form_id"],
                 "assignment_form_id": target.get("form", {}).get("form_id") or target.get("form_id"),
                 "matched_other_lesson": _nearby_match_context(agenda, class_key, week_label, entries, j, submission),
                 **({'lesson_context': {**{k: deepcopy(target[k]) for k in ('class_key', 'school_year', 'week', 'form_id', 'label')},
                                        'teacher_ids': deepcopy(_teacher_ids(target))},
                     'routing_context_only': True} if target.get('routing_context_only') else {})})
        if target["current"]:
            via = source["label"] if source["current"] else "%s · %s" % (source["week"], source["label"])
            target["found"].append(dict(submission, files=[dict(f, via=via, accepted_intake=accepted,
                assignment_form_id=target["form"]["form_id"]) for f in submission["files"]]))
    for i, submission, j in held_imports:
        source, target = entries[i], entries[j]
        target["form"].setdefault("held_elsewhere", []).append({
            "submission": submission,
            "to": "Submitted through %s · %s; lesson match not confirmed" % (source["week"], source["label"])})

    # Each Form's two weeks start at its first turn-in; files not sorted to a
    # Form use the week's earliest-opened one.
    starts = form_weeks(agenda, class_key)
    # Only a student on this class's roster starts the clock: a stray file (another
    # class's student, the teacher's own test) mustn't give everyone a 0.
    def on_roster(found):
        return [sub for sub in found if identify(sub, students, domains, prefix)[0]]
    windows = [window(on_roster(form["found"]), starts.get(form["form_id"])) for form in forms]
    opened = [w for w in windows if w]
    earliest = min(opened, key=lambda w: w["at"]) if opened else None
    describe_files(token, other, known)
    other_rows, _ = group(other, students, domains, prefix, numbers, with_missing=False,
                          cutoff=earliest["at"] if earliest else None)
    unsorted = {(r["section"], roster.plain(r["last"]), roster.plain(r["first"])) for r in other_rows if r["matched"]}
    result, index = [], 0
    for lesson in lessons:
        out_forms = []
        for form, open_for in zip(forms[index:index + len(lesson["forms"])],
                                  windows[index:index + len(lesson["forms"])]):
            historical_intake.bookend(form)
            describe_files(token, form["found"], known)
            rows, missing = group(form["found"], students, domains, prefix, numbers,
                                  cutoff=open_for["at"] if open_for else None)
            for m in missing:
                m["unsorted"] = (m["section"], roster.plain(m["last"]), roster.plain(m["first"])) in unsorted
            began = school_day(open_for["from"]) if open_for else None
            missing, excused = excuse_new(missing, began)
            moved = form.get("moved_out", []) + form.get("held_elsewhere", [])
            describe_files(token, [m["submission"] for m in moved], known)
            elsewhere = []
            for m in moved:
                student, _ = identify(m["submission"], students, domains, prefix)
                first = m["submission"]["files"][0] if m["submission"]["files"] else {}
                elsewhere.append({"who": "%s, %s" % (student["last"], student["first"]) if student else
                                  (m["submission"]["email"] or first.get("name", "")),
                                  "section": student["section"] if student else "", "to": m["to"],
                                  "source": m["submission"]["source"], "file": first.get("name", ""),
                                  "url": first.get("url", ""), "submitted_at": m["submission"]["submitted_at"],
                                  "accepted_intake": bool(m.get("accepted_intake")),
                                  "source_form_id": m.get("source_form_id"),
                                  "assignment_form_id": m.get("assignment_form_id"),
                                  **({'original_submission': deepcopy(m['submission']),
                                      'lesson_context': deepcopy(m['lesson_context'])}
                                     if m.get('routing_context_only') else {}),
                                  **({"matched_other_lesson": m["matched_other_lesson"],
                                      "matched_source_files": [{k: f.get(k) for k in ("id", "name", "url")} for f in m["submission"]["files"]]}
                                     if m.get("matched_other_lesson") else {})})
            out_forms.append({"label": form["label"], "url": form["url"], "form_id": form["form_id"],
                              "problem": form["problem"], "responses": form["responses"],
                              "grade_policy": form.get("grade_policy", {}),
                              **({"teacher_source_binding": form["teacher_source_binding"]} if form.get("teacher_source_binding") else {}),
                              "folder_files": sum(1 for s in form["found"] if s["source"] == "folder"),
                              "shared_files": sum(1 for s in form["found"] if s.get("shared")),
                              **({"teacher_uploads": form["teacher_uploads"]} if form.get("teacher_uploads") else {}),
                              "students": [r for r in rows if not r["late"]],
                              "late": [r for r in rows if r["late"]], "missing": missing,
                              "elsewhere": elsewhere, "excused": excused,
                              "window": {"opened": open_for["opened"], "label": open_for["label"],
                                         "from": open_for["from"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                                         "at": open_for["at"].strftime("%Y-%m-%dT%H:%M:%SZ")}
                              if open_for else None})
        index += len(lesson["forms"])
        result.append({"row": lesson["row"], "topic": lesson["topic"], "edit_url": lesson["edit_url"],
                       "calendar_only": _calendar_only(lesson),
                       "forms": out_forms,
                       "problem": None if lesson["forms"] else
                       "This lesson has no Form link in its 'Turn it in' column."})
    if shared_note:
        problems.append(shared_note)
    # Folders renamed with '_' in front were emptied by Tidy; still read, but
    # only listed if something has landed in them since.
    holding = set(parents.values())
    folder_list = [{"name": name or names.get(fid, "") or "Form upload folder",
                    "url": "https://drive.google.com/drive/folders/" + fid}
                   for fid, name in roots.items() if (not _ancestors(fid, parents) or not
                   set(_ancestors(fid, parents)) & set(roots))
                   and not (str(name).startswith("_") and fid not in holding)]
    check = None if review_only else roster_check(token, config, by_class, class_key, result, other_rows, domains, prefix)
    if any(f.get("read_timeout") for f in forms):
        read_timeouts.add("form_responses")
    folder_reviews = shared_folder_reviews(forms, roots, parents)
    # Read-only grading context is separate from discovery/filing eligibility.
    # It also covers an empty Form, where no score_file call could block zeros.
    if not review_only:
        for lesson in result:
            for form in lesson["forms"]:
                form["grade_readiness"] = completion.assignment_readiness(token, config, agenda, class_key, week_label, form["form_id"])
                original = next((f for f in forms if f['form_id'] == form['form_id']), {})
                if original.get('_combined_scope'):
                    combined_assessment.bind_form(token, form, original['_combined_scope'])
    return {"class_key": class_key, "week": week_label, "week_policy": policy, "assessment_problem": route_problem,
            "lessons": result, "other_files": other_rows,
            "roster_check": check,
            "folder_reviews": folder_reviews,
            "folders": folder_list,
            "folder_files": sum(1 for i in items if i.get("mimeType") != google.FOLDER),
            "folder_note": folder_note,
            "shared_form": {"configured": bool(config.get("HUB_TURNIN_FORM_ID")), "submissions": len(shared),
                            "problem": shared_note},
            "sections": list(roster.SECTIONS[class_key]), "roster_size": len(students),
            "problems": problems,
            **({"read_timeouts": sorted(read_timeouts)} if read_timeouts else {})}
