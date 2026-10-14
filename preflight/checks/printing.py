"""Find everything that needs paper this week and build the Monday print list."""

from __future__ import annotations

import re

from preflight.context import CourseContext, WeekContext
from preflight.models import Finding, PrintItem, Severity
from preflight.sources.gdrive import DOCX_MIME, PDF_MIME, parse_google_url

# Section headings in teacher slides whose following lines are things to print.
PRINT_HEADINGS = re.compile(r"^\s*(TO PRINT|PRINT\s*/\s*HAND OUT|PRINT|HANDOUTS?)\s*:?\s*$", re.I)
SECTION_END = re.compile(r"^\s*[A-Z][A-Z &/—-]{3,}\s*$")  # next ALL-CAPS heading
PRINT_LINE = re.compile(r"^\s*[•\-*]?\s*Print (?:or adapt )?(?:the )?(.+)$", re.I)
# Student-facing tasks that assume paper.
PAPER_TASK = re.compile(r"\bcircle one\b|\bon paper\b|\bown paper\b|\bprinted cards?\b|\bcut (?:out|apart)\b|"
                        r"\bhalf[- ]sheet\b|\bexit ticket\b", re.I)
PER_GROUP = re.compile(r"per group|1 per lab|per station|set of cards", re.I)
PER_STUDENT = re.compile(r"per student|each student|1 each", re.I)
PRINTABLE_TYPES = re.compile(r"worksheet|lab|handout|guide|reading|notes|card|rubric|assessment|quiz|exam", re.I)


def _copies(cc: CourseContext, text: str) -> str:
    if PER_GROUP.search(text):
        return f"{cc.cfg.groups} (per group)" if cc.cfg.groups else "1 per group"
    if re.search(r"as needed|support copies|if additional", text, re.I):
        return "a few (as needed)"
    return f"{cc.cfg.students}" if cc.cfg.students else "1 per student"


_STOP = {"the", "and", "of", "a", "an", "for", "to", "cycle", "week", "wk", "docx", "pdf", "per", "student",
         "students", "group", "copy", "copies", "print", "1", "06", "08", "as", "needed", "sheet", "if"}


def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in _STOP and not re.fullmatch(r"\d{1,2}", t)}


def _same(a: str, b: str) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) >= 0.8


def _norm_title(s: str) -> str:
    s = re.sub(r"\.(docx|pptx|pdf)$", "", s, flags=re.I)
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _clean(item: str) -> str:
    item = re.sub(r"\s*·\s*1 per (student|group).*$", "", item, flags=re.I)
    return item.strip(" .•-")


def collect(ctx: WeekContext) -> tuple[list[PrintItem], list[Finding]]:
    items: list[PrintItem] = []
    findings: list[Finding] = []
    for cc in ctx.courses:
        if cc.row is None or not ctx.meeting_days(cc.cfg):
            continue
        first_day = ctx.meeting_days(cc.cfg)[0]
        day = f"{['Mon', 'Tue', 'Wed', 'Thu', 'Fri'][first_day.weekday()]} {first_day.month}/{first_day.day}"
        mine: list[PrintItem] = []
        folder_by_title = {_norm_title(f.name): f for f in cc.folder_files}

        def add(item: str, source: str, link: str = "", file_id: str = "", copies_hint: str = ""):
            if not _tokens(item):
                return
            if file_id and ctx.drive.get(file_id) is None:
                twin = folder_by_title.get(_norm_title(item))
                if twin is not None:  # the Agenda's copy is gone; print the one in the week folder
                    file_id, link = twin.id, twin.web_link or f"https://drive.google.com/file/d/{twin.id}/view"
            for prev in mine:
                if _same(prev.item, item):
                    if file_id and not prev.file_id:
                        prev.file_id, prev.link = file_id, link
                    return
            mine.append(PrintItem(course=cc.name, day=day, item=item, copies=_copies(cc, copies_hint or item),
                                  source=source, link=link, file_id=file_id))

        for fid, deck in cc.lesson_decks().items():
            for s in deck.slides:
                lines = s.text.splitlines()
                in_section = False
                for line in lines:
                    if PRINT_HEADINGS.match(line):
                        in_section = True
                        continue
                    if in_section and SECTION_END.match(line):
                        in_section = False
                    if in_section and line.strip():
                        add(_clean(line), f"deck slide {s.number}", copies_hint=line)
                    m = PRINT_LINE.match(line)
                    if m and not in_section:
                        add(_clean(m.group(1)), f"deck slide {s.number}", copies_hint=line)
                if not re.search(r"TEACHER|do not project", s.text, re.I):
                    pm = PAPER_TASK.search(s.text)
                    if pm and "[[NOTES:" not in s.text.split(pm.group(0))[0][-200:]:
                        add(f"Paper for slide {s.number} ('{s.title[:50]}'): {pm.group(0)}",
                            f"deck slide {s.number}", copies_hint="per student")

        for w in cc.worksheets:
            if PRINTABLE_TYPES.search(w["type"] or w["document"]):
                link = w["link"] if w["link"].startswith("http") else (
                    f"https://drive.google.com/file/d/{w['file_id']}/view" if w["file_id"] else "")
                add(w["document"], "Worksheets tab", link=link, file_id=w["file_id"])

        print_set_url = next((u for u in (cc.row.print_set, cc.row.extra_url) if parse_google_url(u or "")), "")
        if cc.row.print_set and print_set_url:
            fid = parse_google_url(print_set_url)[1]
            meta = ctx.drive.get(fid)
            add(meta.name if meta else "Print set (Agenda)", "Agenda print-set column", link=print_set_url, file_id=fid)

        for f in cc.folder_files:
            if f.mime_type in (DOCX_MIME, PDF_MIME) and PRINTABLE_TYPES.search(f.name):
                add(re.sub(r"\.(docx|pdf)$", "", f.name), "week folder", link=f.web_link, file_id=f.id)
        items.extend(mine)

    for it in items:
        # Dead Worksheets-tab links are already reported by the links check.
        if it.file_id and it.source != "Worksheets tab" and ctx.drive.get(it.file_id) is None:
            findings.append(Finding(Severity.FIX, it.course, "print",
                                    f"Print item '{it.item}' links a file that does not open.", where=it.link))
    return items, findings
