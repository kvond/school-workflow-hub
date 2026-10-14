"""The hidden labels the feedback prompts read: counts, IDs, pairing, ASCII.

Implements the "Check your work" list in 05_Deck_Adaptation_and_Technical_Guide:
count each label, compare with the slide-1 inventory, check every ID both ways,
and flag typographic characters inside labels.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from preflight.context import WeekContext
from preflight.models import Deck, Finding, Severity

MARKER_RE = re.compile(r"\[\[(NOTES|DRAFT|OPTIONAL|BANK):([^:\]\s]+):([^\]]+?)\]\]")
INVENTORY_RE = re.compile(
    r"\[\[MARKER-INVENTORY:([^\]]+)\]\]\s*NOTES=(\d+)\s+DRAFT=(\d+)\s+OPTIONAL=(\d+)\s+BANK_TERMS=(\d+)"
    r"\s*NOTES_IDS=(\S*)\s+DRAFT_IDS=(\S*)\s+OPTIONAL_IDS=(\S*)"
)
LOOSE_LABEL_RE = re.compile(r"\[\[[^\]]*\]\]")
TYPOGRAPHIC = "…–—‘’“” "


@dataclass
class Inventory:
    code: str
    counts: dict[str, int]
    ids: dict[str, list[str]]
    slide: int


@dataclass
class MarkerScan:
    markers: list[tuple[str, str, str, int]] = field(default_factory=list)  # kind, code, id, slide
    inventory: Inventory | None = None
    bad_labels: list[tuple[str, int]] = field(default_factory=list)

    def ids(self, kind: str) -> list[str]:
        return [m[2] for m in self.markers if m[0] == kind]


def _ids(raw: str) -> list[str]:
    return [] if raw in ("", "-") else [x for x in raw.split(",") if x]


def scan(deck: Deck) -> MarkerScan:
    out = MarkerScan()
    for slide in deck.slides:
        text = slide.text
        flat = " ".join(text.split())
        inv = INVENTORY_RE.search(flat)
        if inv and out.inventory is None:
            out.inventory = Inventory(
                code=inv.group(1),
                counts={"NOTES": int(inv.group(2)), "DRAFT": int(inv.group(3)),
                        "OPTIONAL": int(inv.group(4)), "BANK": int(inv.group(5))},
                ids={"NOTES": _ids(inv.group(6)), "DRAFT": _ids(inv.group(7)), "OPTIONAL": _ids(inv.group(8))},
                slide=slide.number,
            )
        for m in MARKER_RE.finditer(text):
            out.markers.append((m.group(1), m.group(2), m.group(3).strip(), slide.number))
        for m in LOOSE_LABEL_RE.finditer(text):
            label = m.group(0)
            if any(ch in label for ch in TYPOGRAPHIC):
                out.bad_labels.append((label, slide.number))
    return out


def _slides(numbers) -> str:
    nums = sorted(set(numbers))
    return ("slide " if len(nums) == 1 else "slides ") + ", ".join(map(str, nums[:8])) + ("…" if len(nums) > 8 else "")


def check(ctx: WeekContext) -> list[Finding]:
    out: list[Finding] = []
    for cc in ctx.courses:
        for deck in cc.lesson_decks().values():
            out.extend(check_deck(cc.name, cc.cfg.marker_prefix, deck))
    return out


def check_deck(course: str, prefix: str, deck: Deck) -> list[Finding]:
    out: list[Finding] = []
    s = scan(deck)
    where = f"https://docs.google.com/presentation/d/{deck.file_id}/edit"

    def add(sev, msg, fix=""):
        out.append(Finding(sev, course, "markers", f"'{deck.title}': {msg}", fix=fix, where=where))

    if not s.markers and s.inventory is None:
        return out  # not a feedback-workflow deck
    if s.inventory is None:
        add(Severity.FIX, "has response labels but no [[MARKER-INVENTORY]] line on slide 1.",
            "Run Prompt 0 to embed the inventory before students copy the deck.")
    inv = s.inventory

    codes = Counter(m[1] for m in s.markers)
    if inv:
        other = {c for c in codes if c != inv.code}
        if other:
            add(Severity.FIX, f"labels use code {', '.join(sorted(other))} but the inventory says {inv.code}.")
        if prefix and not inv.code.upper().startswith(prefix.upper()):
            add(Severity.CHECK, f"inventory code {inv.code} does not look like a {course} code ({prefix}..).")

    for label, num in s.bad_labels:
        add(Severity.FIX, f"typographic character inside label {label} ({_slides([num])}); the prompts match ASCII only.",
            "Retype the label in plain ASCII.")

    count_problems: list[str] = []
    missing: dict[str, list[str]] = {}
    extra: dict[str, list[str]] = {}
    for kind in ("NOTES", "DRAFT", "OPTIONAL", "BANK"):
        found = s.ids(kind)
        dupes = [i for i, n in Counter(found).items() if n > 1]
        if dupes:
            add(Severity.FIX, f"{kind} ID used twice: {', '.join(dupes[:5])}.", "Give each box its own ID.")
        if inv:
            if inv.counts[kind] != len(found):
                count_problems.append(f"{kind} {inv.counts[kind]}→{len(found)}")
            for i in inv.ids.get(kind, []):
                if i not in found:
                    missing.setdefault(i, []).append(kind)
            if kind in inv.ids:
                for i in found:
                    if i not in inv.ids[kind]:
                        extra.setdefault(i, []).append(kind)
    if count_problems or missing or extra:
        parts = []
        if count_problems:
            parts.append("counts " + ", ".join(count_problems))
        if missing:
            parts.append("in the inventory but not the deck: " + ", ".join(f"{i} ({'/'.join(k)})" for i, k in missing.items()))
        if extra:
            parts.append("in the deck but not the inventory: " + ", ".join(f"{i} ({'/'.join(k)})" for i, k in extra.items()))
        add(Severity.FIX, "the slide-1 inventory does not match the deck — " + "; ".join(parts) + ".",
            "Fix the inventory (or restore the deleted boxes) before running feedback prompts.")

    notes_ids = set(s.ids("NOTES"))
    unpaired = [i for i in s.ids("DRAFT") if i not in notes_ids]
    if unpaired:
        add(Severity.FIX, f"revised-answer boxes with no first-answer box: {', '.join(unpaired)}.",
            "Pair each DRAFT with a NOTES box of the same ID.")

    # Every revision box needs its speaker-note revision prompt.
    draft_slides = {m[3] for m in s.markers if m[0] == "DRAFT"}
    no_prompt = [n for n in draft_slides if not deck.slides[n - 1].notes.strip()]
    if no_prompt and any(sl.notes.strip() for sl in deck.slides):
        add(Severity.FIX, f"response {_slides(no_prompt)} lost the speaker-note revision prompt.",
            "Restore the notes from the original deck before students copy it.")
    return out
