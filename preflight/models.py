"""Plain data objects shared by sources, checks and the report."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import IntEnum


class Severity(IntEnum):
    """How urgently an exception needs the teacher.

    BLOCKER  class cannot run as planned (dead link, no deck, content on a day the class does not meet)
    FIX      something will go wrong later (feedback markers, Schoology drift, stale dates)
    CHECK    needs a human judgment call (VT sequence, pacing estimate)
    """

    CHECK = 1
    FIX = 2
    BLOCKER = 3


@dataclass
class Finding:
    severity: Severity
    course: str
    area: str  # links, sync, calendar, markers, vt, flow, print, convert, supplies
    message: str
    fix: str = ""
    where: str = ""  # a link or "deck slide 12"
    day: str = ""

    def key(self) -> tuple:
        return (self.course, self.area, self.message)


@dataclass
class Slide:
    number: int
    text: str
    notes: str = ""

    @property
    def title(self) -> str:
        for line in self.text.splitlines():
            if line.strip():
                return line.strip()
        return ""


@dataclass
class Deck:
    file_id: str
    title: str
    slides: list[Slide]
    modified: dt.datetime | None = None

    @property
    def text(self) -> str:
        return "\n\n".join(s.text for s in self.slides)

    @property
    def all_text(self) -> str:
        return "\n\n".join(s.text + "\n" + s.notes for s in self.slides)


@dataclass
class DriveFile:
    id: str
    name: str
    mime_type: str
    parents: list[str] = field(default_factory=list)
    trashed: bool = False
    modified: dt.datetime | None = None
    web_link: str = ""
    shared_with_domain: bool | None = None  # None when permissions were not readable

    @property
    def is_google_native(self) -> bool:
        return self.mime_type.startswith("application/vnd.google-apps.")


@dataclass
class DayPlan:
    date: dt.date
    weekday: str  # Mon, Tue, ...
    plan: str
    note: str = ""


@dataclass
class CourseWeek:
    """One course's row for one week of the Agenda."""

    course: str  # "Forensics", "A&P", "Biology"
    week_label: str  # "Wk 08"
    topic: str
    dates_cell: str
    days: list[DayPlan]
    todo: str = ""
    lab_prep: str = ""
    print_set: str = ""
    student_url: str = ""
    teacher_url: str = ""
    folder_url: str = ""
    extra_url: str = ""
    turn_in_url: str = ""
    other_urls: list[str] = field(default_factory=list)
    row_cells: dict[str, str] = field(default_factory=dict)


@dataclass
class SchoolDay:
    date: dt.date
    closed: bool = False
    reason: str = ""


@dataclass
class SchoolWeek:
    label: str  # "Wk 08"
    monday: dt.date
    friday_side: str  # "A" or "B" or ""
    closures: dict[dt.date, str] = field(default_factory=dict)
    raw: str = ""

    @property
    def dates(self) -> list[dt.date]:
        return [self.monday + dt.timedelta(days=i) for i in range(5)]

    def is_open(self, d: dt.date) -> bool:
        return d not in self.closures


@dataclass
class SchoologyItem:
    section: str
    kind: str  # assignment, document, page, link
    title: str
    urls: list[str]
    due: dt.datetime | None = None
    published: bool = True
    item_id: str = ""


@dataclass
class PrintItem:
    course: str
    day: str
    item: str
    copies: str
    source: str
    link: str = ""
    file_id: str = ""


@dataclass
class SupplyItem:
    course: str
    item: str
    status: str  # buy, pull, on-order
    needed_by: str = ""
    source: str = ""


@dataclass
class ConversionIdea:
    course: str
    item: str
    suggestion: str
    keep_on_paper: bool = False
    link: str = ""
