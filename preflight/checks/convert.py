"""Which paper tasks would work better in the deck, and a ready-to-paste digital version.

The rule is to move the student's writing, not to remove it: every question on a
worksheet comes back as a response box with an ASCII feedback label, in order.
Tasks that need hands (drawing, labeling a diagram, cutting cards, lab data at the
bench) stay on paper.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from preflight.context import CourseContext, WeekContext
from preflight.models import ConversionIdea, PrintItem
from preflight.sources.gdrive import DOCS_MIME, DOCX_MIME

HANDS_ON = re.compile(r"\b(draw|sketch|label the|diagram|color|colour|cut|glue|fold|measure|observe|"
                      r"record (?:your )?data|data table|graph)\b", re.I)
QUESTION = re.compile(r"^\s*(?:\d{1,2}[.)]|[a-h][.)]|Q\d+[.:])?\s*(.{8,}\?)\s*$")
NUMBERED = re.compile(r"^\s*(\d{1,2})[.)]\s+(.{6,})$")
BLANK = re.compile(r"_{4,}")


@dataclass
class WorksheetShape:
    questions: list[str] = field(default_factory=list)
    blanks: int = 0
    tables: int = 0
    hands_on: list[str] = field(default_factory=list)


def read_docx(data: bytes) -> WorksheetShape:
    from docx import Document

    doc = Document(io.BytesIO(data))
    shape = WorksheetShape(tables=len(doc.tables))
    texts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                texts.extend(p.text for p in cell.paragraphs)
    for raw in texts:
        t = raw.strip()
        if not t or re.match(r"big question", t, re.I):
            continue
        shape.blanks += len(BLANK.findall(t))
        m = QUESTION.match(t) or NUMBERED.match(t)
        if m and m.groups()[-1].strip() not in shape.questions:
            shape.questions.append(m.groups()[-1].strip())
        for h in HANDS_ON.findall(t):
            if h.lower() not in shape.hands_on:
                shape.hands_on.append(h.lower())
    return shape


def _ids(question: str, n: int) -> str:
    words = [w for w in re.findall(r"[A-Za-z]+", question.upper()) if len(w) > 3][:3]
    return "-".join(words) or f"Q{n}"


def slide_spec(code: str, title: str, shape: WorksheetShape) -> str:
    """Slide-ready text: one response box per question, labels in plain ASCII, four boxes per slide."""
    lines = [f"Digital version of: {title}", "Paste each block onto a slide; type the [[...]] labels exactly as shown.", ""]
    used: set[str] = set()
    for i, q in enumerate(shape.questions, start=1):
        if (i - 1) % 4 == 0:
            lines.append(f"--- Slide {(i - 1) // 4 + 1}: {title} ({i}–{min(i + 3, len(shape.questions))})")
        qid = _ids(q, i)
        while qid in used:
            qid += f"-{i}"
        used.add(qid)
        lines += [f"{i}. {q}", f"[[NOTES:{code}:{qid}]]", "Type here.", ""]
    return "\n".join(lines)


def collect(ctx: WeekContext, prints: list[PrintItem]) -> tuple[list[ConversionIdea], dict[str, str]]:
    ideas: list[ConversionIdea] = []
    specs: dict[str, str] = {}
    by_course = {cc.name: cc for cc in ctx.courses}
    for it in prints:
        cc = by_course[it.course]
        idea, spec = _one(ctx, cc, it)
        if idea:
            ideas.append(idea)
        if spec:
            specs[f"{it.course} — {it.item}"] = spec
    return ideas, specs


def _deck_code(cc: CourseContext) -> str:
    from preflight.checks.markers import scan

    for deck in cc.decks.values():
        s = scan(deck)
        if s.inventory:
            return s.inventory.code
        if s.markers:
            return s.markers[0][1]
    return f"{cc.cfg.marker_prefix}00"


def _deck_overlap(cc: CourseContext, shape: WorksheetShape) -> str | None:
    """Most of the worksheet's questions already appear in a deck that has typed response boxes."""
    if len(shape.questions) < 3:
        return None
    for deck in cc.lesson_decks().values():
        if deck.text.count("[[NOTES:") < 3:
            continue
        low = deck.text.lower()
        hits = 0
        for q in shape.questions:
            words = re.findall(r"[a-z]{5,}", q.lower())[:6]
            if words and sum(w in low for w in words) >= max(2, int(len(words) * 0.7)):
                hits += 1
        if hits / len(shape.questions) >= 0.6:
            return f"{hits} of its {len(shape.questions)} questions are already typed response boxes in '{deck.title}'"
    return None


def _one(ctx: WeekContext, cc: CourseContext, it: PrintItem) -> tuple[ConversionIdea | None, str]:
    text = it.item
    if re.search(r"^Paper for slide", text):
        if re.search(r"circle one", text, re.I):
            return ConversionIdea(cc.name, text, "Make it a typed choice on the slide (a text box with GUILTY / NOT "
                                  "GUILTY) so the vote is saved with the reasoning; keep the hand vote for the tally."), ""
        return None, ""
    if re.search(r"card|lab|data|rubric|exam|test|quiz|labeling|coloring", text, re.I):
        return ConversionIdea(cc.name, text, "Keep on paper (hands-on, bench or assessment work).",
                              keep_on_paper=True, link=it.link), ""

    meta = ctx.drive.get(it.file_id) if it.file_id else None
    shape = None
    if meta is not None and meta.mime_type in (DOCX_MIME, DOCS_MIME):
        data = ctx.drive.export(meta.id, DOCX_MIME)
        if data:
            try:
                shape = read_docx(data)
            except Exception:
                shape = None

    if shape is None:
        return None, ""
    overlap = _deck_overlap(cc, shape)
    if overlap:
        return ConversionIdea(cc.name, text, f"Skip printing: {overlap}.", link=it.link), ""
    if shape.hands_on:
        return ConversionIdea(cc.name, text, f"Keep on paper ({', '.join(shape.hands_on[:3])}).", keep_on_paper=True,
                              link=it.link), ""
    if shape.tables and len(shape.questions) <= 2:
        return ConversionIdea(cc.name, text, "Make it a table on a slide (or a linked Sheet) so answers stay with "
                              "the deck.", link=it.link), ""
    if 0 < len(shape.questions) <= 16:
        spec = slide_spec(_deck_code(cc), text, shape)
        n = len(shape.questions)
        return ConversionIdea(cc.name, text, f"Move the {n} question{'s' if n > 1 else ''} into the deck as response "
                              "boxes (slide text prepared below); nothing is dropped.", link=it.link), spec
    return None, ""
