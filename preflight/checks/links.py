"""Every link the Agenda hands out opens, points at this week's material, and agrees with the folders."""

from __future__ import annotations

import re

from preflight.context import CourseContext, WeekContext
from preflight.models import Finding, Severity
from preflight.sources.agenda import week_number
from preflight.sources.gdrive import FOLDER_MIME, parse_google_url, url_mode

STALE_NAME = re.compile(r"^(zz|~)|\b(wrong|obsolete|delete|old|unused|backup)\b", re.I)
_CRITICAL_ROLES = {"teacher deck", "student copy", "turn-in form", "week folder"}


def _role_of(cc: CourseContext, url: str) -> str:
    row = cc.row
    for role, value in (("teacher deck", row.teacher_url), ("student copy", row.student_url),
                        ("week folder", row.folder_url), ("extra", row.extra_url),
                        ("turn-in form", row.turn_in_url)):
        if value == url:
            return role
    return "link in the plan"


def _norm_title(s: str) -> str:
    s = re.sub(r"\.(docx|pptx|pdf)$", "", s, flags=re.I)
    s = re.sub(r"^(copy of|▶\s*live\s*—)\s*", "", s, flags=re.I)
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def check(ctx: WeekContext) -> list[Finding]:
    out: list[Finding] = []
    for cc in ctx.courses:
        if cc.row is not None:
            out.extend(_course(ctx, cc))
    return out


def _course(ctx: WeekContext, cc: CourseContext) -> list[Finding]:
    out: list[Finding] = []
    name, row = cc.name, cc.row

    if not row.teacher_url and not row.student_url:
        out.append(Finding(Severity.BLOCKER, name, "links", f"{ctx.label} has no lesson deck linked in the Agenda.",
                           fix="Paste the teacher deck URL into _teacherURL."))

    for url, meta in cc.linked.items():
        role = _role_of(cc, url)
        if meta is None:
            sev = Severity.BLOCKER if role in _CRITICAL_ROLES else Severity.FIX
            hint = ""
            if role == "week folder" and cc.week_folders_by_name:
                hint = " A folder named " + ", ".join(f"'{f.name}'" for f in cc.week_folders_by_name[:3]) + " exists."
            out.append(Finding(sev, name, "links", f"The {role} link does not open (deleted, or not shared with you).{hint}",
                               fix=f"Replace the {role} URL in the Agenda.", where=url))
        elif meta.trashed:
            out.append(Finding(Severity.BLOCKER, name, "links", f"The {role} is in the trash: '{meta.name}'.",
                               fix="Restore it or relink.", where=url))

    # Students should get a copy (or a view) of the master, never edit access to it.
    if row.student_url and url_mode(row.student_url) == "edit":
        out.append(Finding(Severity.FIX, name, "links",
                           "The student link opens the deck in edit mode; students will request access or edit the master.",
                           fix="Use the /copy link.", where=row.student_url))
    t, s = parse_google_url(row.teacher_url or ""), parse_google_url(row.student_url or "")
    if t and s and t[1] != s[1]:
        tm, sm = cc.linked.get(row.teacher_url), cc.linked.get(row.student_url)
        if tm and sm and _norm_title(tm.name) != _norm_title(sm.name):
            out.append(Finding(Severity.FIX, name, "links",
                               f"Teacher deck '{tm.name}' and student copy '{sm.name}' are different lessons.",
                               fix="Point both at the same lesson.", where=row.student_url))

    # Deck titled for another week.
    for fid, deck in cc.decks.items():
        if cc.deck_roles.get(fid) not in ("teacher deck", "student copy"):
            continue
        n = week_number(deck.title)
        if n is not None and n != ctx.week_no and re.search(r"\bweek\b", deck.title, re.I):
            out.append(Finding(Severity.CHECK, name, "links",
                               f"The {ctx.label} row links '{deck.title}'.",
                               fix="Confirm this is the deck you mean to teach, or link this week's deck.",
                               where=f"https://docs.google.com/presentation/d/{fid}/edit"))

    # Same deck linked as two different weeks.
    tid = t[1] if t else None
    for n, other in cc.neighbors.items():
        ot = parse_google_url(other.teacher_url or "")
        if tid and ot and ot[1] == tid and other.topic != row.topic:
            out.append(Finding(Severity.FIX, name, "links",
                               f"{ctx.label} ('{row.topic}') and {other.week_label} ('{other.topic}') link the same deck.",
                               fix=f"Give {other.week_label} its own deck link, or fix this week's.",
                               where=row.teacher_url))

    # Folder label in the display column names another week.
    folder_label = next((v for h, v in row.row_cells.items() if "folder" in h.lower() and not h.startswith("_")), "")
    n = week_number(folder_label)
    if n is not None and n != ctx.week_no:
        out.append(Finding(Severity.FIX, name, "links",
                           f"The folder button for {ctx.label} says '{folder_label.strip()}'.",
                           fix="Point the folder link at this week's folder."))

    if cc.folder is not None:
        if cc.folder.mime_type != FOLDER_MIME:
            out.append(Finding(Severity.FIX, name, "links", "The week-folder link is not a folder.", where=row.folder_url))
        elif not cc.folder_files:
            out.append(Finding(Severity.FIX, name, "links", f"The week folder '{cc.folder.name}' is empty.",
                               where=row.folder_url))
        n = week_number(cc.folder.name)
        if n is not None and n != ctx.week_no:
            out.append(Finding(Severity.FIX, name, "links",
                               f"The {ctx.label} folder link opens '{cc.folder.name}'.", where=row.folder_url))

    # Any linked file titled for a different week (a reused form or folder sends work to the wrong place).
    for url, meta in cc.linked.items():
        if meta is None or meta.mime_type == FOLDER_MIME:
            continue
        n = week_number(meta.name)
        if n is not None and n != ctx.week_no and re.search(r"\bw(ee)?k\b", meta.name, re.I):
            role = _role_of(cc, url)
            if role in ("teacher deck", "student copy"):
                continue  # reported above with the deck
            out.append(Finding(Severity.FIX, name, "links",
                               f"The {ctx.label} {role} is '{meta.name}'.",
                               fix="Link this week's file, or rename it if it really covers both weeks.", where=url))

    # Files in the week folder that students could open by mistake.
    titles: dict[str, list[str]] = {}
    for f in cc.folder_files:
        if STALE_NAME.search(f.name):
            out.append(Finding(Severity.CHECK, name, "links", f"Leftover file in the week folder: '{f.name}'.",
                               fix="Move it to an archive folder so students and the preflight ignore it."))
            continue
        titles.setdefault(_norm_title(f.name), []).append(f.name)
    for names in titles.values():
        if len(names) > 1:
            out.append(Finding(Severity.CHECK, name, "links",
                               f"Two versions in the week folder: {' / '.join(repr(n) for n in names[:3])}.",
                               fix="Keep the one you teach from; archive the other."))

    # Worksheets tab vs the files that are actually in the folder.
    folder_by_title = {_norm_title(f.name): f for f in cc.folder_files}
    dead_with_twin: list[str] = []
    for w in cc.worksheets:
        if not w["file_id"]:
            if w["type"]:
                out.append(Finding(Severity.CHECK, name, "links",
                                   f"Worksheets tab lists '{w['document']}' with no file.",
                                   fix="Link the file or remove the row."))
            continue
        meta = ctx.drive.get(w["file_id"])
        twin = folder_by_title.get(_norm_title(w["document"])) or (
            folder_by_title.get(_norm_title(meta.name)) if meta else None)
        if meta is None and twin is not None:
            dead_with_twin.append(w["document"])
        elif meta is None:
            out.append(Finding(Severity.FIX, name, "links",
                               f"Worksheets tab link for '{w['document']}' does not open, and no file by that name "
                               "is in the week folder.", fix="Find the file and relink the row."))
        elif twin is not None and twin.id != meta.id:
            out.append(Finding(Severity.FIX, name, "links",
                               f"Two different copies of '{w['document']}': the Worksheets tab and the week folder disagree.",
                               fix="Keep one copy and relink."))
    if dead_with_twin:
        out.append(Finding(Severity.FIX, name, "links",
                           f"{len(dead_with_twin)} Worksheets-tab link{'s' if len(dead_with_twin) > 1 else ''} "
                           f"no longer open: {'; '.join(dead_with_twin)}. Each has a file with the same name in the "
                           "week folder (the packet uses those).",
                           fix="Relink the Worksheets rows to the week-folder files."))

    # Student work routing.
    if not cc.turnin and row.turn_in_url:
        out.append(Finding(Severity.FIX, name, "links", f"No TurnIn Map row for {ctx.label}; submissions will not be filed.",
                           fix="Add the TurnIn Map row."))
    for t_row in cc.turnin:
        folder = cc.turnin_folders.get(t_row["folder_id"])
        if t_row["folder_id"] and folder is None:
            out.append(Finding(Severity.BLOCKER, name, "links",
                               f"Turn-in folder for '{t_row['key']}' does not open; student work cannot be filed.",
                               fix="Fix the folder id in the TurnIn Map."))
        topic_words = set(_norm_title(row.topic).split()) - {"and", "the", "of", "unit", "1", "cont"}
        if topic_words and not topic_words & set(_norm_title(t_row["key"]).split()):
            out.append(Finding(Severity.FIX, name, "links",
                               f"Student work for '{row.topic}' is routed as '{t_row['key']}'.",
                               fix="Update the TurnIn Map key so submissions land with this lesson."))
    return out
