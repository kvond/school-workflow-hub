"""Variation Theory structure: is each critical aspect still taught as designed?

Per 08_AI_Guide_Transform_a_Lesson_Deck and 05_Deck_Adaptation_and_Technical_Guide,
each critical aspect runs: Critical Aspect question -> Contrast Set -> Build a Rule
-> Pattern Break -> 3-Tier "Getting Started" -> response slide, and the lesson has
one Concept Bank and a visible teacher note. This check finds those pieces by their
exact labels, then looks for the ways operational edits usually break them:
a step removed, steps reordered, the rule printed before students build it,
editing notes left on student slides, and the deck still dated for another week.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from preflight.context import CourseContext, WeekContext
from preflight.models import Deck, Finding, Severity, Slide
from preflight.sources.agenda import parse_month_day

EXACT_ASPECT = re.compile(r"Critical aspect:\s*([^\n/]+)")
LOOSE_ASPECT = re.compile(r"critical aspect\s*[:—–-]\s*([^\n/]+)", re.I)
RULE = "Finish this sentence as a rule"
PATTERN = re.compile(r"\bPattern break\b")
PATTERN_LOOSE = re.compile(r"\bpattern break\b", re.I)
TIER = "Getting Started"
WHATIF = "What if?"
BANK = "Define these in your own words"
RESPONSE = re.compile(r"your first answer", re.I)
CONTRAST = re.compile(r"\bCASE A\b|\bCASE B\b|\bcontrast\b|\bvs\.?\b|\bversus\b|compare", re.I)
TEACHER_SLIDE = re.compile(r"TEACHER (REFERENCE|NOTE|PREP|NAVIGATION)|do not project|not projected", re.I)
EDITING_NOTE = re.compile(r"(?<!\[)\[(added|corrected|verify|draft|todo|fix|note to self)\b(?!:)[^\]]*\]", re.I)
NOTES_TODO = re.compile(r"\bthis should\b|\bTODO\b|\bFIXME\b|\bneeds? to (be )?(changed|fixed|replaced)\b", re.I)
# Words that are VT roles, not aspect names ("CRITICAL ASPECT — CONTRAST").
ROLE_WORDS = {"contrast", "the core idea", "core idea", "question", "contrast set"}


@dataclass
class AspectBlock:
    name: str
    slides: list[Slide] = field(default_factory=list)

    def first(self, test) -> int | None:
        for s in self.slides:
            if test(s):
                return s.number
        return None


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().strip("·:—- ").lower())


def aspect_blocks(deck: Deck) -> tuple[list[AspectBlock], bool]:
    """Group slides by their 'Critical aspect: <name>' label. Returns (blocks, labels_exact)."""
    blocks: dict[str, AspectBlock] = {}
    exact_found = False
    loose_found = False
    for s in deck.slides:
        names = [m.group(1) for m in EXACT_ASPECT.finditer(s.text)]
        if names:
            exact_found = True
        else:
            names = [m.group(1) for m in LOOSE_ASPECT.finditer(s.text)]
            if names:
                loose_found = True
        for raw in names:
            key = _norm(raw)
            if key in ROLE_WORDS or not key:
                continue
            blocks.setdefault(key, AspectBlock(name=raw.strip())).slides.append(s)
    # Response slides are titled "Your answer — Critical Aspect N · name"; attach them to their block.
    for s in deck.slides:
        m = re.search(r"Critical Aspect \d+\s*·\s*([^\n]+)", s.text)
        if m and _norm(m.group(1)) in blocks and s not in blocks[_norm(m.group(1))].slides:
            blocks[_norm(m.group(1))].slides.append(s)
            blocks[_norm(m.group(1))].slides.sort(key=lambda x: x.number)
    return list(blocks.values()), exact_found or not loose_found


def _rule_given_away(slides: list[Slide]) -> list[int]:
    """Build-a-Rule slides where the blank is already filled in and there is no response box."""
    out = []
    for s in slides:
        if RULE not in s.text:
            continue
        has_blank = bool(re.search(r"_{3,}| {3,}| {6,}", s.text))
        has_box = "[[NOTES:" in s.text
        if not has_blank and not has_box:
            out.append(s.number)
    return out


def check(ctx: WeekContext) -> list[Finding]:
    out: list[Finding] = []
    for cc in ctx.courses:
        for deck in cc.lesson_decks().values():
            out.extend(check_deck(ctx, cc, deck))
    return out


def check_deck(ctx: WeekContext, cc: CourseContext, deck: Deck) -> list[Finding]:
    out: list[Finding] = []
    course = cc.name
    where = f"https://docs.google.com/presentation/d/{deck.file_id}/edit"
    text = deck.text

    def add(sev, msg, fix=""):
        out.append(Finding(sev, course, "vt", f"'{deck.title}': {msg}", fix=fix, where=where))

    is_vt = bool(LOOSE_ASPECT.search(text) or TIER in text or PATTERN_LOOSE.search(text))
    if is_vt:
        blocks, exact = aspect_blocks(deck)
        if not exact:
            add(Severity.FIX, "critical-aspect labels are not in the exact 'Critical aspect: <name>' form, "
                "so the feedback prompts cannot find the aspects.",
                "Retype the labels exactly, or run Prompt 0 on the deck.")
        if not blocks:
            add(Severity.FIX, "no named critical aspect. The lesson has VT pieces but no aspect they serve.",
                "Name the critical aspect on each slide in its block.")
            blocks = [AspectBlock(name="(whole deck)", slides=list(deck.slides))]

        for b in blocks:
            out.extend(_block_findings(course, deck, b, where))

        rule_slides = [s.number for s in deck.slides if RULE in s.text]
        if len(rule_slides) > len(blocks) and len(blocks) > 0:
            add(Severity.CHECK, f"'Finish this sentence as a rule' appears {len(rule_slides)} times for "
                f"{len(blocks)} aspect(s) (slides {', '.join(map(str, rule_slides))}).",
                "A duplicated slide counts as an extra Build-a-Rule for the feedback prompts.")

        bank = [s.number for s in deck.slides if BANK in s.text]
        if not bank:
            add(Severity.FIX, "no Concept Bank headed 'Define these in your own words'.",
                "Add the heading (the feedback prompts find the bank by it).")
        else:
            bank_text = " ".join(deck.slides[n - 1].text for n in bank)
            if not re.search(r"connect|relate|how .* (link|fit)|explain how", bank_text, re.I):
                add(Severity.CHECK, f"the Concept Bank (slide {bank[0]}) asks for definitions only; "
                    "the design asks students to connect at least two terms.",
                    "Add a line asking how two of the terms connect.")
        if not any(TEACHER_SLIDE.search(s.text) and re.search(r"critical aspect", s.text, re.I) for s in deck.slides):
            add(Severity.CHECK, "no visible teacher note naming the critical aspect and what varies.",
                "Add the teacher-note slide so later edits know what to protect.")

    # Editing notes and leftover instructions that students would see.
    for s in deck.slides:
        if TEACHER_SLIDE.search(s.text):
            continue
        m = EDITING_NOTE.search(s.text)
        if m:
            add(Severity.FIX, f"editing note visible to students on slide {s.number}: \"{m.group(0)[:70]}\".",
                "Move it to speaker notes or delete it.")
        if NOTES_TODO.search(s.notes):
            add(Severity.FIX, f"unfinished edit request in the speaker notes of slide {s.number}: "
                f"\"{s.notes.strip()[:80]}\".", "Make the change or delete the note.")

    out.extend(_stale_dates(ctx, course, deck, where))
    return out


def _block_findings(course: str, deck: Deck, b: AspectBlock, where: str) -> list[Finding]:
    out: list[Finding] = []
    label = f"aspect '{b.name}'"

    def add(sev, msg, fix=""):
        out.append(Finding(sev, course, "vt", f"'{deck.title}', {label}: {msg}", fix=fix, where=where))

    question = b.first(lambda s: "[[NOTES:" in s.text and RULE not in s.text and TIER not in s.text
                       and not PATTERN_LOOSE.search(s.text))
    contrast = b.first(lambda s: bool(CONTRAST.search(s.text)))
    rule = b.first(lambda s: RULE in s.text)
    pattern = b.first(lambda s: bool(PATTERN_LOOSE.search(s.text)))
    tier = b.first(lambda s: TIER in s.text)
    response = b.first(lambda s: "[[DRAFT:" in s.text)

    missing = [n for n, v in (("Build a Rule", rule), ("Pattern Break", pattern),
                              ("3-Tier 'Getting Started'", tier), ("response slide", response)) if v is None]
    if missing:
        add(Severity.FIX, "missing " + ", ".join(missing) + ".",
            "Restore the step from the original cycle deck; do not substitute inside the contrast.")
    if contrast is None:
        add(Severity.CHECK, "no contrast set found (two cases that differ in one way).")

    order = [("contrast set", contrast), ("Build a Rule", rule), ("Pattern Break", pattern),
             ("Getting Started", tier), ("response slide", response)]
    present = [(n, v) for n, v in order if v is not None]
    for (n1, v1), (n2, v2) in zip(present, present[1:]):
        if v2 < v1:
            add(Severity.FIX, f"{n2} (slide {v2}) comes before {n1} (slide {v1}).",
                "Put the steps back in order: contrast, rule, pattern break, 3-tier, response.")
            break
    if question is not None and contrast is not None and rule is not None and question > rule:
        add(Severity.CHECK, "the first response box comes after the rule.")

    given = _rule_given_away(b.slides)
    if given:
        add(Severity.FIX, f"the rule is already filled in on slide {', '.join(map(str, given))}, "
            "so students see the answer before they build it.",
            "Delete the filled-in copy or move it to speaker notes.")
    if tier is not None and response is not None and response - tier > 2:
        add(Severity.CHECK, f"the response slide ({response}) is {response - tier} slides after Getting Started ({tier}).",
            "Keep the question and its response slide together.")
    return out


def _stale_dates(ctx: WeekContext, course: str, deck: Deck, where: str) -> list[Finding]:
    """Student-facing dates that fall outside this week usually mean last week's deck."""
    week_dates = set(ctx.week.dates)
    outside = []
    inside = 0
    for s in deck.slides:
        for d in parse_month_day(s.text, ctx.config.school_year_start):
            if d in week_dates:
                inside += 1
            elif abs((d - ctx.week.monday).days) <= 21:
                outside.append((d, s.number))
    if outside and len(outside) > inside:
        shown = sorted({f"{d.month}/{d.day}" for d, _ in outside})
        slides = sorted({n for _, n in outside})
        return [Finding(Severity.FIX, course, "vt",
                        f"'{deck.title}' is dated {', '.join(shown[:5])} (slides {', '.join(map(str, slides[:6]))}), "
                        f"not {ctx.label}.",
                        fix="Update the dates and day plan on the slides before students copy the deck.",
                        where=where)]
    return []
