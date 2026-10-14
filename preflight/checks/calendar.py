"""Does each course have a lesson on every day it meets, and nothing on days it does not?"""

from __future__ import annotations

import re

from preflight.context import CourseContext, WeekContext
from preflight.models import Finding, Severity
from preflight.sources.agenda import WEEKDAYS, parse_month_day

# Words the Agenda already uses to mark unfinished planning.
_UNRESOLVED = [
    (re.compile(r"NEEDS CONTENT", re.I), "marked NEEDS CONTENT"),
    (re.compile(r"VERIFY DAY", re.I), "marked VERIFY DAY"),
    (re.compile(r"check the day", re.I), "marked 'Check the day'"),
    (re.compile(r"has to move", re.I), "says the plan has to move"),
    (re.compile(r"\[DRAFT\b|\bDRAFT —", re.I), "still marked DRAFT"),
]
_DOES_NOT_MEET = re.compile(r"does not meet|no class|not a .* meeting day|schools? closed", re.I)
_SIDE_CLAIM = re.compile(r"\b([AB])\s+Friday\b")
_INSERVICE = re.compile(r"in-service|schools? closed|holiday", re.I)


def _d(day) -> str:
    return f"{WEEKDAYS[day.weekday()]} {day.month}/{day.day}"


def check(ctx: WeekContext) -> list[Finding]:
    out: list[Finding] = []
    for cc in ctx.courses:
        out.extend(_course(ctx, cc))
    return out


def _course(ctx: WeekContext, cc: CourseContext) -> list[Finding]:
    out: list[Finding] = []
    name = cc.name
    if cc.row is None:
        return [Finding(Severity.BLOCKER, name, "calendar",
                        f"The {cc.cfg.tab} tab has no {ctx.label} row.",
                        fix=f"Add the {ctx.label} row to the Agenda.")]

    meets = set(ctx.meeting_days(cc.cfg))
    known_closures = {d for w in ctx.weeks.values() for d in w.closures}
    for day in cc.row.days:
        text = f"{day.plan}\n{day.note}".strip()
        label = _d(day.date)
        if not ctx.week.is_open(day.date):
            if day.plan and not _DOES_NOT_MEET.search(day.plan):
                out.append(Finding(Severity.BLOCKER, name, "calendar",
                                   f"{label} is closed ({ctx.week.closures[day.date]}) but has a lesson planned.",
                                   fix="Move this lesson to the next meeting day.", day=label))
            continue

        if day.date in meets:
            if not day.plan or _DOES_NOT_MEET.search(day.plan) or re.search(r"NEEDS CONTENT", day.plan, re.I):
                why = ""
                if day.weekday == "Fri":
                    why = f" ({ctx.week.friday_side} Friday)"
                out.append(Finding(Severity.BLOCKER, name, "calendar",
                                   f"{label}: {name} meets{why}, but no lesson is planned.",
                                   fix="Plan this day or record that it is a catch-up day.", day=label))
        elif day.plan and not _DOES_NOT_MEET.search(day.plan):
            out.append(Finding(Severity.BLOCKER, name, "calendar",
                               f"{label}: a lesson is planned but {name} does not meet that day.",
                               fix="Move the lesson to a meeting day.", day=label))

        if day.weekday == "Fri" and ctx.week.friday_side:
            for m in _SIDE_CLAIM.finditer(text):
                if m.group(1) != ctx.week.friday_side:
                    out.append(Finding(Severity.BLOCKER, name, "calendar",
                                       f"{label}: the Agenda says '{m.group(1)} Friday' but the calendar says "
                                       f"{ctx.week.friday_side} Friday.",
                                       fix="Rewrite the Friday plan for the real A/B day.", day=label))
                    break

        for line in text.splitlines():
            if _INSERVICE.search(line):
                for d in parse_month_day(line, ctx.config.school_year_start):
                    if d not in known_closures and ctx.week.monday <= d <= ctx.week.dates[-1]:
                        out.append(Finding(Severity.FIX, name, "calendar",
                                           f"{label}: the plan treats {_d(d)} as a closure, but the calendar shows school open.",
                                           fix="The plan was shifted for a closure that is not happening; check what moved.",
                                           day=label))

        lead = parse_month_day(day.plan.split("\n", 1)[0], ctx.config.school_year_start)
        if lead and day.date not in lead and day.date in meets:
            out.append(Finding(Severity.FIX, name, "calendar",
                               f"{label}: the plan is dated {lead[0].month}/{lead[0].day}.",
                               fix="Update the date or move the plan to the right column.", day=label))

        for pattern, what in _UNRESOLVED:
            if pattern.search(text) and day.date in meets:
                out.append(Finding(Severity.FIX, name, "calendar", f"{label}: the Agenda is {what}.",
                                   fix="Resolve it and clear the marker.", day=label))
                break
    return out
