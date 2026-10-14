"""Is the same lesson represented the same way in the Agenda, Schoology and the decks?"""

from __future__ import annotations

import re

from preflight.context import CourseContext, WeekContext
from preflight.models import Finding, Severity
from preflight.sources.agenda import WEEKDAYS
from preflight.sources.gdrive import parse_google_url, url_mode

_OTHER_LMS = ["Google Classroom", "Canvas", "Blackboard", "Moodle"]


def check(ctx: WeekContext) -> list[Finding]:
    out: list[Finding] = []
    for cc in ctx.courses:
        out.extend(_lms_wording(ctx, cc))
        if cc.row is not None and cc.cfg.sections:
            out.extend(_schoology(ctx, cc))
    return out


def _lms_wording(ctx: WeekContext, cc: CourseContext) -> list[Finding]:
    out = []
    for fid, deck in cc.lesson_decks().items():
        for lms in _OTHER_LMS:
            if lms.lower() == ctx.config.lms_name.lower():
                continue
            slides = [s.number for s in deck.slides if lms.lower() in s.text.lower()]
            if slides:
                out.append(Finding(Severity.FIX, cc.name, "sync",
                                   f"'{deck.title}' tells students to submit in {lms} (slides "
                                   f"{', '.join(map(str, slides))}); this class uses {ctx.config.lms_name}.",
                                   fix=f"Change the wording to {ctx.config.lms_name}.",
                                   where=f"https://docs.google.com/presentation/d/{fid}/edit"))
    return out


def _in_week(ctx: WeekContext, cc: CourseContext, item) -> bool:
    if item.due is not None:
        return ctx.week.monday <= item.due.date() <= ctx.week.dates[-1]
    title = item.title.lower()
    return (ctx.label.lower() in title or f"week {ctx.week_no}" in title
            or (cc.row.topic and cc.row.topic.lower()[:20] in title))


def _schoology(ctx: WeekContext, cc: CourseContext) -> list[Finding]:
    out: list[Finding] = []
    name, row = cc.name, cc.row
    if cc.schoology_error:
        return [Finding(Severity.CHECK, name, "setup", f"Schoology could not be read: {cc.schoology_error}")]

    expected: dict[str, str] = {}
    for label, url in (("student deck", row.student_url or row.teacher_url), ("turn-in form", row.turn_in_url)):
        parsed = parse_google_url(url or "")
        if parsed:
            expected[parsed[1]] = label

    week_items = [i for i in cc.schoology if _in_week(ctx, cc, i)]
    seen_ids: set[str] = set()
    deck_titles = {fid: d.title for fid, d in cc.decks.items()}
    for item in week_items:
        for url in item.urls:
            parsed = parse_google_url(url)
            if not parsed:
                continue
            kind, fid = parsed
            seen_ids.add(fid)
            if fid in expected:
                if expected[fid] == "student deck" and url_mode(url) == "edit":
                    out.append(Finding(Severity.FIX, name, "sync",
                                       f"Schoology '{item.title}' gives students the edit link to the master deck.",
                                       fix="Use the /copy link (same as the Agenda's _studentURL).", where=url))
                continue
            if kind == "presentation":
                meta = ctx.drive.get(fid)
                if meta is None:
                    out.append(Finding(Severity.BLOCKER, name, "sync",
                                       f"Schoology '{item.title}' links a deck that does not open.",
                                       fix="Relink to the Agenda's student copy.", where=url))
                else:
                    out.append(Finding(Severity.FIX, name, "sync",
                                       f"Schoology '{item.title}' links '{meta.name}', not the deck in the Agenda"
                                       f" ('{deck_titles.get(next(iter(expected), ''), row.topic)}').",
                                       fix="Make Schoology and the Agenda point at the same copy.", where=url))
            elif kind == "form":
                out.append(Finding(Severity.FIX, name, "sync",
                                   f"Schoology '{item.title}' uses a different turn-in form than the Agenda.",
                                   fix="Use the Agenda's turn-in form so work is filed.", where=url))
        if not item.published and item.due is not None:
            out.append(Finding(Severity.FIX, name, "sync",
                               f"Schoology '{item.title}' is unpublished but due {item.due:%a %m/%d}.",
                               fix="Publish it before class."))
        if item.due is not None:
            due = item.due.date()
            if not ctx.week.is_open(due):
                out.append(Finding(Severity.FIX, name, "sync",
                                   f"Schoology '{item.title}' is due {WEEKDAYS[due.weekday()]} {due.month}/{due.day}, "
                                   f"a closed day.", fix="Move the due date."))

    for fid, label in expected.items():
        if fid not in seen_ids:
            out.append(Finding(Severity.FIX, name, "sync", f"Schoology has no {label} for {ctx.label} ('{row.topic}').",
                               fix=f"Post the Agenda's {label} link in Schoology."))

    for item in week_items:
        if re.search(r"\bcopy of\b", item.title, re.I):
            out.append(Finding(Severity.CHECK, name, "sync", f"Schoology item '{item.title}' looks like a stray copy."))
    return out
