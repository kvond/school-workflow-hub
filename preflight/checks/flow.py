"""Does each meeting day fit a 90-minute block?

The estimate is deliberately simple so the teacher can audit it: explicit minutes
written on a slide win; otherwise each student response box, Concept Bank term,
content slide and outside activity gets the time set in config.yaml (pacing).
Teacher-only slides and revision ("DRAFT") boxes are not counted toward the first
pass, because revision happens on its own day.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from preflight.config import Pacing
from preflight.checks.calendar import _DOES_NOT_MEET as DOES_NOT_MEET
from preflight.context import CourseContext, WeekContext
from preflight.models import Deck, Finding, Severity, Slide
from preflight.sources.agenda import WEEKDAYS

DAY_HEADER = re.compile(r"\b(?:Day|MEETING)\s+(\d)\s+OF\s+(\d)\b", re.I)
EXPLICIT_MIN = re.compile(r"(\d{1,3})\s*(?:[–-]\s*(\d{1,3}))?\s*(?:min|minutes)\b", re.I)
TEACHER_ONLY = re.compile(r"TEACHER (REFERENCE|NOTE|PREP|NAVIGATION)|do not project|not projected|image credits",
                          re.I)
OUTSIDE = re.compile(r"\b(sim|simulation|interactive|webquest|game|virtual|lab|station|video)\b", re.I)
FULL_BLOCK_TASK = re.compile(r"\b(exam|test|quiz|assessment|revise|revision|lab|investigation)\b", re.I)
SEPARATORS = re.compile(r"\s\+\s|\s·\s")


@dataclass
class Segment:
    label: str
    slides: list[Slide]
    minutes: float


def slide_minutes(s: Slide, p: Pacing) -> float:
    if TEACHER_ONLY.search(s.text):
        return 0.0
    explicit = [max(int(a), int(b or 0)) for a, b in EXPLICIT_MIN.findall(s.text) if 0 < int(a) <= 120]
    if explicit:
        return float(max(explicit))
    notes = s.text.count("[[NOTES:")
    bank = s.text.count("[[BANK:")
    minutes = notes * p.minutes_per_response_box + bank * p.minutes_per_bank_term
    if OUTSIDE.search(s.title) or re.search(r"https?://", s.text) and OUTSIDE.search(s.text):
        minutes += p.minutes_per_external_activity
    return max(minutes, p.minutes_per_content_slide if s.text.strip() else 0.0)


def segments(deck: Deck, p: Pacing) -> list[Segment]:
    """Split the deck at slides headed 'Day N of M' / 'MEETING N OF M'."""
    segs: list[Segment] = [Segment(label="", slides=[], minutes=0.0)]
    for s in deck.slides:
        m = None if TEACHER_ONLY.search(s.text) else DAY_HEADER.search(s.text[:80])
        if m:
            label = f"Day {m.group(1)} of {m.group(2)}"
            if label != segs[-1].label:
                segs.append(Segment(label=label, slides=[], minutes=0.0))
        segs[-1].slides.append(s)
        segs[-1].minutes += slide_minutes(s, p)
    return [seg for seg in segs if seg.slides]


def check(ctx: WeekContext) -> list[Finding]:
    out: list[Finding] = []
    for cc in ctx.courses:
        if cc.row is not None:
            out.extend(_course(ctx, cc))
    return out


def _course(ctx: WeekContext, cc: CourseContext) -> list[Finding]:
    out: list[Finding] = []
    p = ctx.config.pacing
    budget = p.block_minutes - p.overhead_minutes
    meets = ctx.meeting_days(cc.cfg)
    taught_days = [d for d in cc.row.days if d.date in meets and d.plan and not DOES_NOT_MEET.search(d.plan)]

    # Too many separate activities crammed into one block.
    for day in taught_days:
        first_line_dropped = day.plan.split("\n", 1)[-1]
        parts = [x for x in SEPARATORS.split(first_line_dropped) if x.strip()]
        if len(parts) >= 4:
            out.append(Finding(Severity.CHECK, cc.name, "flow",
                               f"{day.weekday} {day.date.month}/{day.date.day}: {len(parts)} separate activities "
                               f"planned for one {p.block_minutes}-minute block.",
                               fix="Split them across meeting days or mark which are homework.",
                               day=day.weekday))

    for fid, deck in cc.lesson_decks().items():
        segs = segments(deck, p)
        where = f"https://docs.google.com/presentation/d/{fid}/edit"
        if len(segs) > 1 and segs[0].label == "" and segs[0].minutes < 15:
            segs = segs[1:]  # cover/teacher slides before Day 1
        if all(not s.label for s in segs):
            total = sum(s.minutes for s in segs)
            days = max(1, len(taught_days))
            if total > days * (budget + p.over_tolerance):
                out.append(Finding(Severity.FIX, cc.name, "flow",
                                   f"'{deck.title}' holds about {total:.0f} minutes of student work; the Agenda gives it "
                                   f"{days} meeting day{'s' if days > 1 else ''} ({days * p.block_minutes} min).",
                                   fix="Mark which slides are class, homework or a later day, or add meeting days.",
                                   where=where))
            continue
        for seg in segs:
            if seg.minutes > budget + p.over_tolerance:
                out.append(Finding(Severity.FIX, cc.name, "flow",
                                   f"'{deck.title}' {seg.label or 'opening'} runs about {seg.minutes:.0f} min "
                                   f"(slides {seg.slides[0].number}–{seg.slides[-1].number}); the block has {budget}.",
                                   fix="Move a task to the next day or to homework.", where=where))
            elif seg.label and seg.minutes < p.under_minutes and not FULL_BLOCK_TASK.search(
                    " ".join(s.text for s in seg.slides)):
                out.append(Finding(Severity.CHECK, cc.name, "flow",
                                   f"'{deck.title}' {seg.label} has only about {seg.minutes:.0f} min planned.",
                                   fix="Check it is meant to share the block with something else.", where=where))
    return out


def weekday_label(d) -> str:
    return f"{WEEKDAYS[d.weekday()]} {d.month}/{d.day}"
