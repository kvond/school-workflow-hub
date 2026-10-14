"""Turn a Slides deck (exported as .pptx) into plain slide text plus speaker notes.

We read the .pptx export rather than the Slides API so the same parser works for
Google Slides, uploaded PowerPoint files and offline snapshots.
"""

from __future__ import annotations

import io

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from preflight.models import Deck, Slide


def _shape_text(shape) -> list[str]:
    out: list[str] = []
    if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
        for child in shape.shapes:
            out.extend(_shape_text(child))
        return out
    if getattr(shape, "has_table", False) and shape.has_table:
        for row in shape.table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    out.append(cell.text)
        return out
    if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
        if shape.text_frame.text.strip():
            out.append(shape.text_frame.text)
    return out


def parse_pptx(data: bytes, file_id: str = "", title: str = "") -> Deck:
    prs = Presentation(io.BytesIO(data))
    slides: list[Slide] = []
    for number, slide in enumerate(prs.slides, start=1):
        parts: list[str] = []
        # Document order, not screen position: Slides writes each heading just before its own
        # body, so two-column teacher slides ("PREPARE" | "PRINT / HAND OUT") stay grouped.
        for shape in slide.shapes:
            parts.extend(_shape_text(shape))
        notes = ""
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text or ""
        slides.append(Slide(number=number, text="\n".join(parts), notes=notes))
    return Deck(file_id=file_id, title=title, slides=slides)


def parse_plain_text(text: str, file_id: str = "", title: str = "") -> Deck:
    """Fallback for Slides' text/plain export, which separates slides with a blank line.

    Speaker notes are not in that export, so notes-based checks are skipped for these decks.
    """
    chunks = [c for c in text.replace("\r\n", "\n").split("\n\n")]
    slides = [Slide(number=i, text=c.strip()) for i, c in enumerate(chunks, start=1) if c.strip()]
    return Deck(file_id=file_id, title=title, slides=slides)
