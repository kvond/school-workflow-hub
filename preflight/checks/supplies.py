"""Pull BUY / PULL / on-order items out of the Agenda and the decks into one shopping list."""

from __future__ import annotations

import re

from preflight.context import WeekContext
from preflight.models import SupplyItem

# Agenda lab/prep cell: "BUY: Carrots · PULL: Trays · ✔ on order — yeast, balloons; 🛒 sugar"
_AGENDA_PART = re.compile(r"(BUY|PULL|✔\s*on order|🛒)\s*[:—–-]?\s*([^·;|]+)", re.I)
_DECK_HEADINGS = {
    "BUY AHEAD": "buy",
    "BUY": "buy",
    "PUT OUT FOR STUDENTS": "pull",
    "PUT OUT": "pull",
    "PULL": "pull",
}
_HEADING_RE = re.compile(r"^\s*(" + "|".join(map(re.escape, sorted(_DECK_HEADINGS, key=len, reverse=True))) + r")\s*:?\s*$")
_END_RE = re.compile(r"^\s*[A-Z][A-Z &/—-]{3,}\s*$")
_NOTHING = re.compile(r"^\s*(nothing|none|n/a)\b", re.I)
_SPLIT = re.compile(r",\s*|\s+and\s+(?=[a-z])")
_NOT_SUPPLY = re.compile(r"worksheet|handout|slide|sheet|printout|copies", re.I)
_STATUS_ORDER = {"buy": 0, "on-order": 1, "pull": 2}


def _status(tag: str) -> str:
    t = tag.lower()
    if t.startswith("buy") or "🛒" in tag:
        return "buy"
    if "order" in t:
        return "on-order"
    return "pull"


def _items(text: str) -> list[str]:
    text = re.sub(r"\(.*?\)", "", text)
    text = text.split(" — ")[0] if " — " in text and len(text.split(" — ")[0]) > 3 else text
    out = []
    for part in _SPLIT.split(text):
        part = part.strip(" .•-–— ")
        part = re.sub(r"^((and|or|a|an|the|\d+)\s+)+", "", part, flags=re.I)
        if 1 < len(part) <= 60 and not _NOTHING.match(part) and not _NOT_SUPPLY.search(part):
            out.append(part)
    return out


def _key(item: str) -> str:
    k = re.sub(r"[^a-z ]", "", item.lower()).strip()
    k = re.sub(r"\b(small|large|clear|plastic|active|dry)\b", "", k)
    return re.sub(r"s\b", "", " ".join(k.split()))


def collect(ctx: WeekContext) -> list[SupplyItem]:
    found: dict[tuple[str, str], SupplyItem] = {}

    def add(course: str, item: str, status: str, source: str, needed_by: str = ""):
        key = (course, _key(item))
        if not key[1]:
            return
        prev = found.get(key)
        # Agenda status wins over deck status; "on order" beats "buy".
        if prev is None or (prev.source != "Agenda" and source == "Agenda") or (
                prev.source == source and _STATUS_ORDER[status] > _STATUS_ORDER[prev.status]):
            found[key] = SupplyItem(course=course, item=item, status=status, needed_by=needed_by, source=source)

    for cc in ctx.courses:
        if cc.row is None or not ctx.meeting_days(cc.cfg):
            continue
        prep = cc.row.lab_prep
        needed = ""
        m = re.search(r"PREP by\s+([A-Za-z]{3}\s+\d{1,2}/\d{1,2})", prep)
        if m:
            needed = m.group(1)
        for tag, body in _AGENDA_PART.findall(prep):
            for it in _items(body):
                add(cc.name, it, _status(tag), "Agenda", needed)

        for fid, deck in cc.lesson_decks().items():
            for s in deck.slides:
                status = None
                for line in s.text.splitlines():
                    h = _HEADING_RE.match(line)
                    if h:
                        status = _DECK_HEADINGS[h.group(1)]
                        continue
                    if status and _END_RE.match(line):
                        status = None
                    if status and line.strip().startswith(("•", "-", "*")):
                        body = line.split(" — ")[0]
                        for it in _items(body):
                            add(cc.name, it, status, f"deck slide {s.number}")
    return sorted(found.values(), key=lambda i: (_STATUS_ORDER[i.status], i.course, i.item.lower()))
