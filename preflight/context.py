"""Gather everything one preflight run looks at, once, so the checks stay pure functions."""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from preflight.config import Config, CourseConfig
from preflight.models import CourseWeek, Deck, DriveFile, SchoolWeek, SchoologyItem
from preflight.sources import agenda as ag
from preflight.sources.gdrive import (
    FOLDER_MIME,
    PPTX_MIME,
    SLIDES_MIME,
    DriveSource,
    parse_google_url,
)
from preflight.sources.schoology import SchoologySource
from preflight.sources.slides import parse_pptx

log = logging.getLogger(__name__)


@dataclass
class CourseContext:
    cfg: CourseConfig
    row: CourseWeek | None
    decks: dict[str, Deck] = field(default_factory=dict)  # file id -> parsed deck
    deck_roles: dict[str, str] = field(default_factory=dict)  # file id -> "teacher deck", "student copy", ...
    linked: dict[str, DriveFile | None] = field(default_factory=dict)  # url -> metadata (None = dead)
    folder: DriveFile | None = None
    folder_files: list[DriveFile] = field(default_factory=list)
    week_folders_by_name: list[DriveFile] = field(default_factory=list)
    worksheets: list[dict[str, str]] = field(default_factory=list)
    turnin: list[dict[str, str]] = field(default_factory=list)
    turnin_folders: dict[str, DriveFile | None] = field(default_factory=dict)
    schoology: list[SchoologyItem] = field(default_factory=list)
    schoology_error: str = ""
    neighbors: dict[int, CourseWeek] = field(default_factory=dict)  # adjacent weeks' rows

    @property
    def name(self) -> str:
        return self.cfg.name

    def lesson_decks(self) -> dict[str, Deck]:
        """Decks the Agenda links for teaching; if none of those open, the decks found in the week folder."""
        linked = {f: d for f, d in self.decks.items()
                  if self.deck_roles.get(f) in ("teacher deck", "student copy", "linked in plan", "extra")}
        if linked:
            return linked
        return {f: d for f, d in self.decks.items() if self.deck_roles.get(f) == "in week folder"}


@dataclass
class WeekContext:
    config: Config
    week_no: int
    week: SchoolWeek
    weeks: dict[int, SchoolWeek]
    courses: list[CourseContext]
    drive: DriveSource

    @property
    def label(self) -> str:
        return self.week.label

    def meeting_days(self, course: CourseConfig) -> list[dt.date]:
        days = []
        for d in self.week.dates:
            wd = ag.WEEKDAYS[d.weekday()]
            if not self.week.is_open(d):
                continue
            if wd == "Fri":
                if self.week.friday_side and self.week.friday_side == course.friday_side:
                    days.append(d)
            elif wd in course.meets:
                days.append(d)
        return days


def _load_deck(drive: DriveSource, meta: DriveFile) -> Deck | None:
    if meta.mime_type not in (SLIDES_MIME, PPTX_MIME):
        return None
    data = drive.export(meta.id, PPTX_MIME)
    if not data:
        return None
    deck = parse_pptx(data, file_id=meta.id, title=meta.name)
    deck.modified = meta.modified
    return deck


def build_context(
    config: Config,
    workbook: ag.AgendaWorkbook,
    drive: DriveSource,
    schoology: SchoologySource | None,
    week_no: int,
) -> WeekContext:
    weeks = ag.parse_weeks(workbook.tab(config.weeks_tab))
    if week_no not in weeks:
        raise ValueError(f"Week {week_no} is not in the {config.weeks_tab} tab")
    week = weeks[week_no]
    try:
        ws_grid = workbook.tab(config.worksheets_tab)
    except KeyError:
        ws_grid = []
    try:
        ti_grid = workbook.tab(config.turnin_tab)
    except KeyError:
        ti_grid = []

    courses: list[CourseContext] = []
    for ccfg in config.courses:
        row = ag.parse_course_week(workbook.tab(ccfg.tab), ccfg.name, week_no, week)
        cc = CourseContext(cfg=ccfg, row=row)
        for n in (week_no - 1, week_no + 1):
            if n in weeks:
                other = ag.parse_course_week(workbook.tab(ccfg.tab), ccfg.name, n, weeks[n])
                if other is not None:
                    cc.neighbors[n] = other
        cc.worksheets = ag.worksheets_for_week(ws_grid, ccfg.name, week_no)
        cc.turnin = ag.turnin_for_week(ti_grid, ccfg.name, week_no)
        for t in cc.turnin:
            if t["folder_id"]:
                cc.turnin_folders[t["folder_id"]] = drive.get(t["folder_id"])

        if row is not None:
            roles = [
                ("teacher deck", row.teacher_url),
                ("student copy", row.student_url),
                ("week folder", row.folder_url),
                ("extra", row.extra_url),
                ("turn-in form", row.turn_in_url),
            ] + [("linked in plan", u) for u in row.other_urls]
            for role, url in roles:
                if not url:
                    continue
                parsed = parse_google_url(url)
                if not parsed:
                    continue
                meta = drive.get(parsed[1])
                cc.linked[url] = meta
                if meta is None:
                    continue
                if meta.mime_type == FOLDER_MIME and role == "week folder":
                    cc.folder = meta
                elif meta.id not in cc.decks:
                    deck = _load_deck(drive, meta)
                    if deck is not None:
                        cc.decks[meta.id] = deck
                        cc.deck_roles[meta.id] = role

        cc.week_folders_by_name = [
            f for f in drive.search_folders(f"Week {week_no:02d}")
            if not ccfg.drive_root or ccfg.drive_root in f.parents
        ]
        if cc.folder is not None:
            cc.folder_files = drive.children(cc.folder.id)
        elif ccfg.drive_root and cc.week_folders_by_name:
            # The Agenda's folder link is dead; check what is in the folders named for this week instead.
            for f in cc.week_folders_by_name:
                cc.folder_files.extend(drive.children(f.id))
        # Decks that sit in the week folder but are not linked from the Agenda still get checked.
        for f in cc.folder_files:
            if f.id not in cc.decks and f.mime_type in (SLIDES_MIME, PPTX_MIME):
                deck = _load_deck(drive, f)
                if deck is not None:
                    cc.decks[f.id] = deck
                    cc.deck_roles[f.id] = "in week folder"

        if schoology is not None:
            for section in ccfg.sections:
                try:
                    cc.schoology.extend(schoology.items(section, week.monday, week.monday + dt.timedelta(days=4)))
                except Exception as err:  # network/auth problems become one exception line, not a crash
                    log.warning("Schoology read failed for %s: %s", section, err)
                    cc.schoology_error = str(err)
        courses.append(cc)

    return WeekContext(config=config, week_no=week_no, week=week, weeks=weeks, courses=courses, drive=drive)
