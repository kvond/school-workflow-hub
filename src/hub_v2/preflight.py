"""Next-week preflight: one read-only pass over a teaching week before it starts.

It reads the Agenda week (plans, notes, lab / prep, links), each lesson's
teacher deck, lesson folder, Turn-in Forms and the Agenda's Worksheets tab,
and returns only what needs the teacher:

  exceptions  what will break or confuse the week, each with the smallest fix
  print       what has to be printed, by day, with copies, and a merged packet
  digital     printed work that already has, or can have, a digital form
  supplies    the grocery / pull-from-storage list for the week's labs
  schoology   the student-facing links to post or confirm (the Hub can't read
              Schoology, so it hands over the exact links instead of guessing)

It checks the intellectual structure as well as the files: each critical
aspect keeps its Variation Theory sequence (question, contrast, rule, pattern
break, mastery, then a revision box), slide references survive edits, the
deck's days fit the class meetings the Agenda gives it, revision is not due
before it can happen, and each meeting has a workable 90-minute plan.

Nothing is written anywhere and no model is called. The checks are pure
functions over plain data, so they run the same on the live Google sources
(GoogleSources) and on fixtures.
"""
import math
import re
from datetime import date, timedelta

from . import agenda as agenda_module, google, roster

DECK_ID = re.compile(r"https://docs\.google\.com/presentation/d/([A-Za-z0-9_-]{20,})")
DOC_ID = re.compile(r"https://docs\.google\.com/document/d/([A-Za-z0-9_-]{20,})")
FILE_ID = re.compile(r"https://(?:drive|docs)\.google\.com/(?:file/d/|open\?id=|drive/folders/|"
                     r"document/d/|presentation/d/|spreadsheets/d/|forms/d/)([A-Za-z0-9_-]{20,})")
URL = re.compile(r"https?://[^\s\"'<>]+")
DOW = ("Mon", "Tue", "Wed", "Thu", "Fri")
DOW_DATE = re.compile(r"\b(Mon|Tue|Wed|Thu|Fri)[a-z]*\.?,?\s+(\d{1,2})/(\d{1,2})\b")
DIVIDER = re.compile(r"^\s*(?:day|meeting)\s+(\d+)\s+of\s+(\d+)\b", re.I | re.M)
REVISION_REF = re.compile(r"^\s*slide\s+(\d+)\s*[—–-]\s*(.+)$", re.I | re.M)
NAV_LINE = re.compile(r"^\s*(\d+)\s*·\s*(.+)$")
DUE = re.compile(r"\b(DRAFT|Notes)\.?\s*([^\n·⏎]*?)\s+due\s+(\d{1,2})/(\d{1,2})", re.I)
WEEK_NUMBER = re.compile(r"\b(?:wk|week)\s*0*(\d{1,2})[a-z]?\b", re.I)
ISO_DATE = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")
AUTHOR_NOTE = re.compile(r"(?<!\[)\[(?!\[)\s*(draft|verify|todo|confirm|added|corrected|image|fix|check|placeholder)\b"
                         r"[^\]\n]{0,200}\]", re.I)
MARKER = re.compile(r"\[\[(NOTES|DRAFT|BANK|OPTIONAL):([A-Za-z0-9_-]{1,40}):([^\]]{1,120})\]\]")
ASPECT_LABEL = re.compile(r"critical\s+aspect\s*:\s*([^\n\r]{1,180})", re.I)
TEACHER_ONLY = ("teacher reference", "teacher prep", "teacher navigation", "do not project",
                "not projected")
NOT_MEETING = re.compile(r"does not meet|doesn't meet|\bOFF\b|schools? closed|no class|"
                         r"have it\b|has it\b|not a .*meeting day", re.I)
NEEDS_CONTENT = re.compile(r"needs content", re.I)
PRINT_HEADS = ("to print", "print / hand out", "print/hand out", "print", "hand out", "print and hand out")
BUY_HEADS = ("buy ahead", "buy")
PULL_HEADS = ("put out", "put out for students", "pull", "materials", "set out")
PREP_HEADS = ("prepare",)
ALL_HEADS = set(PRINT_HEADS + BUY_HEADS + PULL_HEADS + PREP_HEADS + (
    "watch for", "objectives", "additional resources", "essential claim", "critical aspects",
    "critical aspect", "week question", "case study", "standard", "scored for completion",
    "not scored", "directions", "ngss performance expectations"))
STOP = set("""a an and are as at be but by can do does for from has have how if in into is it its
of on or that the their them then there these they this to was what when where which who why will
with you your lesson short long day days class plan plans today week due notes draft deck copy
http https docs google com presentation edit turn activity check moved verify flagged previously
planned real status reconcile teaching meet meets monday tuesday wednesday thursday friday""".split())
APPENDIX = re.compile(r"^\s*(activity and resource links|image credits|credits|everything linked|"
                      r"resources?|links)\b", re.I)
NOT_SUPPLY = re.compile(r"\b(slides?|worksheets?|response|deck|handouts?|copies)\b", re.I)
CLAIMS_OFF = re.compile(r"does not meet|doesn't meet|\bOFF\b|no class\b|schools? closed", re.I)
ASSESSMENT = re.compile(r"(unit exam|unit \d+ assessment|assessment|quiz|\btest\b|\bexam\b)[^\n·⏎]*", re.I)
DIGITAL_PREFIXES = ("slides:", "slide deck", "student copy")
REFERENCE_WORDS = ("chart", "scale", "reference", "labeling", "labelling", "coloring", "colouring")
WORKSHEET_WORDS = ("worksheet", "guided notes", "reinforcement", "practice", "guide")
LAB_WORDS = ("lab", "investigation")
GROUP_SIZE = 4
MEETING_MINUTES = 90
UNDER, OVER = 55, 110
FIX, DECIDE, TIDY = "fix", "decide", "tidy"


class SourceError(Exception):
    """A source the preflight needed could not be read; the message says why."""


# ---------- the live sources ----------

class GoogleSources:
    """Read-only Google access with the dashboard's service token."""

    def __init__(self, token):
        self.token = token

    def _wrap(self, call, what):
        try:
            return call()
        except google.GoogleError as error:
            if error.status in (403, 404):
                raise SourceError("%s can't be opened: it was deleted, or it isn't shared with the Hub."
                                  % what) from None
            raise SourceError("%s couldn't be read: %s" % (what, error)) from None

    def deck(self, deck_id):
        meta = self._wrap(lambda: google.drive_file(self.token, deck_id), "The deck")
        pres = self._wrap(lambda: google.native_presentation(self.token, deck_id), "The deck")
        return {"id": deck_id, "title": meta.get("name") or pres.get("title", ""),
                "trashed": bool(meta.get("trashed")), "slides": presentation_slides(pres)}

    def file(self, file_id):
        return self._wrap(lambda: google.drive_file(self.token, file_id), "A linked file")

    def children(self, folder_id):
        return self._wrap(lambda: google.folder_children(self.token, folder_id), "The lesson folder")

    def form(self, form_id):
        info = self._wrap(lambda: google.form_info(self.token, form_id), "The Turn-in Form")
        return {"title": (info.get("info") or {}).get("documentTitle") or (info.get("info") or {}).get("title", ""),
                "responder_url": info.get("responderUri", "")}

    def pdf(self, file_id, mime):
        return self._wrap(lambda: google.printable_pdf_bytes(self.token, file_id, mime), "A print file")


def _text_of(text):
    out = []
    for element in (text or {}).get("textElements", []) or []:
        run = element.get("textRun") or element.get("autoText") or {}
        out.append(run.get("content", ""))
    return "".join(out)


def _links_of(text):
    found = []
    for element in (text or {}).get("textElements", []) or []:
        run = element.get("textRun") or {}
        url = ((run.get("style") or {}).get("link") or {}).get("url")
        if url:
            found.append((" ".join(run.get("content", "").split()), url))
    return found


def presentation_slides(pres):
    """[{number, object_id, text, links, skipped}] from a Slides API presentation."""
    slides = []
    for number, slide in enumerate(pres.get("slides") or [], 1):
        parts, links = [], []

        def walk(elements):
            for element in elements or []:
                if "elementGroup" in element:
                    walk(element["elementGroup"].get("children"))
                shape = element.get("shape")
                if shape and shape.get("text"):
                    parts.append(_text_of(shape["text"]))
                    links.extend(_links_of(shape["text"]))
                for row in (element.get("table") or {}).get("tableRows", []) or []:
                    for cell in row.get("tableCells", []) or []:
                        parts.append(_text_of(cell.get("text")))
                        links.extend(_links_of(cell.get("text")))
        walk(slide.get("pageElements"))
        slides.append({"number": number, "object_id": slide.get("objectId", ""),
                       "text": "\n".join(p.strip("\n") for p in parts if p.strip()),
                       "links": links,
                       "skipped": bool((slide.get("slideProperties") or {}).get("isSkipped"))})
    return slides


# ---------- small helpers ----------

def _lines(text):
    return [" ".join(line.split()) for line in str(text or "").splitlines() if line.strip()]


def _key(value):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).split())


def _tokens(value):
    """Distinctive words: 4+ letters, or short codes with a digit (CA2, wk08)."""
    words = re.findall(r"[a-z][a-z0-9]{3,}|\b[a-z]{1,3}\d+\b", str(value or "").lower())
    return {w[:-1] if w.endswith("s") and len(w) > 4 else w for w in words} - STOP


def _deck_id(url):
    match = DECK_ID.match(url or "")
    return match.group(1) if match else ""


def _file_id(url):
    match = FILE_ID.match(url or "")
    return match.group(1) if match else ""


def _fmt(day):
    return "%s %d/%d" % (DOW[day.weekday()] if day.weekday() < 5 else day.strftime("%a"), day.month, day.day)


def _date_in_year(month, day, near):
    """MM/DD as the date nearest `near` (school years cross New Year)."""
    best = None
    for year in (near.year - 1, near.year, near.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if best is None or abs((candidate - near).days) < abs((best - near).days):
            best = candidate
    return best


def _exception(level, cls, lesson, text, action="", url="", day=None):
    return {"level": level, "class": cls, "lesson": lesson, "day": _fmt(day) if day else "",
            "date": day.isoformat() if day else "", "text": text, "action": action, "url": url}


# ---------- schedule ----------

def closed_days(monday, closures, label=""):
    """{date: reason} for the days of this week that the school is closed."""
    closed = {}
    text = "%s\n%s" % (label, closures)
    if re.search(r"closed all week|spring break", text, re.I) and not re.search(r"begins", text, re.I):
        return {monday + timedelta(days=i): "school closed" for i in range(5)}
    only = re.search(r"\b(Mon|Tue|Wed|Thu|Fri)\s*[–-]\s*(Mon|Tue|Wed|Thu|Fri)\s+only\b", text)
    if only:
        last = DOW.index(only.group(2))
        for i in range(last + 1, 5):
            closed[monday + timedelta(days=i)] = "school closed"
    for line in str(closures or "").splitlines():
        match = re.match(r"\s*⚠\s*(Mon|Tue|Wed|Thu|Fri)\s+(\d{1,2})/(\d{1,2})\s*(.*)", line)
        if match and "reopen" not in line.lower():
            day = _date_in_year(int(match.group(2)), int(match.group(3)), monday)
            if day and monday <= day < monday + timedelta(days=5):
                closed[day] = match.group(4).strip(" —-") or "school closed"
    return closed


def friday_side(closures):
    match = re.search(r"=\s*([AB])\s+Friday", str(closures or ""))
    return match.group(1) if match else ""


def class_side(plan_days):
    """'A' for a Mon/Wed class, 'B' for a Tue/Thu class (the A/B block days)."""
    if "Mon" in plan_days or "Wed" in plan_days:
        return "A"
    if "Tue" in plan_days or "Thu" in plan_days:
        return "B"
    return ""


def meetings(week, plan_days):
    """[{day: 'Mon', date, meets, why}] for one class in one Agenda week."""
    monday = date.fromisoformat(week["monday"])
    closed = closed_days(monday, week.get("closures", ""), week.get("label", ""))
    side, fri = class_side(plan_days), friday_side(week.get("closures", ""))
    out = []
    for name in plan_days:
        day = monday + timedelta(days=DOW.index(name))
        why = closed.get(day, "")
        if not why and name == "Fri" and side and fri and side != fri:
            why = "%s Friday — this class doesn't meet" % fri
        out.append({"day": name, "date": day, "meets": not why, "why": why})
    return out


# ---------- the deck ----------

def _teacher_only(text):
    low = text.lower()
    return any(cue in low for cue in TEACHER_ONLY)


def _sections(text):
    """{heading: [line, ...]} for the ALL-CAPS headings of a teacher slide."""
    found, current = {}, None
    for line in _lines(text):
        head = line.lower().rstrip(":")
        if head in ALL_HEADS and (line.isupper() or line.rstrip(":").isupper()):
            current = head
            found.setdefault(current, [])
        elif current:
            found[current].append(line)
    return found


def _aspects(reference):
    lines = _lines(reference)
    start = next((i for i, line in enumerate(lines)
                  if line.lower() in ("critical aspect", "critical aspects")), None)
    if start is None:
        return []
    out = []
    for line in lines[start + 1:]:
        if line.lower() in ALL_HEADS or line.startswith("[[MARKER-INVENTORY"):
            break
        name = re.sub(r"^\s*\d+\s*[.)\\]*\s*", "", line)
        name = re.split(r"\s+[—–]\s+|:\s+", name, maxsplit=1)[0].strip()
        if name and _key(name) not in [_key(a) for a in out]:
            out.append(name)
    return out


def read_deck(deck):
    """Everything the checks need from a deck's slides, in plain data."""
    slides = deck["slides"]
    reference = next((s["text"] for s in slides if "teacher reference" in s["text"].lower()
                      and "critical aspect" in s["text"].lower()), "")
    teacher = [s for s in slides if _teacher_only(s["text"])]
    student = [s for s in slides if not _teacher_only(s["text"]) and not APPENDIX.match(s["text"])]
    sections = {}
    for slide in teacher:
        for head, items in _sections(slide["text"]).items():
            sections.setdefault(head, []).extend((slide["number"], item) for item in items)
    # Days ("Day 2 of 3", "MEETING 4 OF 5"): each divider starts a segment;
    # slides before the first divider belong to the first one.
    dividers = []
    for slide in student:
        match = DIVIDER.search(slide["text"])
        if match and (not dividers or int(match.group(1)) != dividers[-1][1]):
            dividers.append((slide["number"], int(match.group(1)), slide["text"]))
    segments = []
    for i, (number, label, text) in enumerate(dividers):
        start = 1 if i == 0 else number
        end = dividers[i + 1][0] - 1 if i + 1 < len(dividers) else len(slides)
        revise = bool(re.search(r"\brevis|make it stronger|go back to (your|what)", text, re.I))
        segments.append({"label": label, "start": start, "end": end, "revision": revise,
                         "divider": number})
    if not segments:
        segments = [{"label": 1, "start": 1, "end": len(slides), "revision": False, "divider": 0}]
    return {"id": deck["id"], "title": deck.get("title", ""), "trashed": bool(deck.get("trashed")),
            "slides": slides, "student": student,
            "teacher": teacher, "reference": reference, "aspects": _aspects(reference),
            "sections": sections, "segments": segments}


def segment_of(info, number):
    for index, segment in enumerate(info["segments"]):
        if segment["start"] <= number <= segment["end"]:
            return index
    return None


def _phases(text):
    low = text.lower()
    markers = [m.group(3).lower() for m in MARKER.finditer(text)]
    found = set()
    if "?" in text and "[[notes:" in low:
        found.add("question")
    if any(cue in low for cue in ("compare", "same ", "different", "opposite", "case a", "case b",
                                  "contrast", "two linings", "two ")) or any(
            "contrast" in m or "both" in m for m in markers):
        found.add("contrast")
    if "finish this sentence as a rule" in low or any(m.endswith("-rule") for m in markers):
        found.add("rule")
    if "pattern break" in low or any("patternbreak" in m for m in markers):
        found.add("pattern_break")
    if "mastery" in low or "getting started" in low or any(m.endswith("-mastery") for m in markers):
        found.add("mastery")
    return found


PHASES = ("question", "contrast", "rule", "pattern_break", "mastery")
PHASE_NAMES = {"question": "critical-aspect question", "contrast": "contrast set",
               "rule": "build-a-rule", "pattern_break": "pattern break", "mastery": "3-tier / mastery"}


def vt_check(info):
    """Problems with the deck's Variation Theory structure, as (level, text, slide)."""
    out = []
    if not info["reference"]:
        return [(DECIDE, "No TEACHER REFERENCE slide with CRITICAL ASPECTS, so the lesson's "
                         "critical aspects can't be checked.", None)]
    if not info["aspects"]:
        return [(DECIDE, "The teacher reference slide lists no critical aspects.", None)]
    first_slide = []
    for aspect in info["aspects"]:
        key = _key(aspect)
        block = [s for s in info["student"] if any(
            _key(label).startswith(key) or key.startswith(_key(label))
            for label in ASPECT_LABEL.findall(s["text"]))]
        live = [s for s in block if not s.get("skipped")]
        if not live:
            out.append((FIX, "Critical aspect “%s” has no slides labelled “Critical aspect: %s”%s."
                        % (aspect, aspect, " that aren't skipped" if block else ""), None))
            first_slide.append(None)
            continue
        first_slide.append(live[0]["number"])
        where = {}
        for slide in live:
            for phase in _phases(slide["text"]):
                where.setdefault(phase, slide["number"])
        missing = [PHASE_NAMES[p] for p in PHASES if p not in where]
        if missing:
            out.append((FIX, "“%s” is missing its %s." % (aspect, ", ".join(missing)), live[0]["number"]))
        order = [(p, where[p]) for p in ("contrast", "rule", "pattern_break", "mastery") if p in where]
        for (a, sa), (b, sb) in zip(order, order[1:]):
            if sb < sa:
                out.append((FIX, "In “%s” the %s (slide %d) now comes before the %s (slide %d)."
                            % (aspect, PHASE_NAMES[b], sb, PHASE_NAMES[a], sa), sb))
    known = [n for n in first_slide if n]
    if known != sorted(known):
        out.append((DECIDE, "The critical aspects are taught in a different order from the teacher "
                            "reference (%s)." % "; ".join(info["aspects"]), None))
    text = "\n".join(s["text"] for s in info["slides"])
    drafts = {m.group(3) for m in MARKER.finditer(text) if m.group(1) == "DRAFT"}
    mastery = [d for d in drafts if re.fullmatch(r"CA\d+-MASTERY", d)]
    if len(mastery) < len(info["aspects"]):
        out.append((FIX, "%d critical aspect(s) but %d revision box(es) (DRAFT CA…-MASTERY): a revised "
                         "answer is missing." % (len(info["aspects"]), len(mastery)), None))
    notes = {m.group(3) for m in MARKER.finditer(text) if m.group(1) == "NOTES"}
    orphan = sorted(drafts - notes)
    if orphan:
        out.append((FIX, "Revision box(es) with no first-answer box: %s." % ", ".join(orphan), None))
    out.extend(reference_check(info))
    return out


def reference_check(info):
    """Slide numbers the deck quotes (revision steps, the teacher index) still point at the
    right slides after edits."""
    out, by_number = [], {s["number"]: s for s in info["slides"]}
    for slide in info["student"]:
        for match in REVISION_REF.finditer(slide["text"]):
            number, label = int(match.group(1)), match.group(2)
            target = by_number.get(number)
            aspect = re.search(r"critical aspect\s+(\d+)", label, re.I)
            ok = target and "[[DRAFT:" in target["text"] and (
                not aspect or re.search(r"\[\[DRAFT:[^:\]]+:CA%s-" % aspect.group(1), target["text"])
                or "what if" in label.lower())
            if not ok:
                actual = _find_draft(info, label)
                out.append((FIX, "The revision step on slide %d sends students to slide %d (“%s”), but "
                                 "that slide has no matching revision box%s." % (
                                     slide["number"], number, label[:60],
                                     "; it is now slide %d" % actual if actual else ""), slide["number"]))
    for slide in info["teacher"]:
        if "navigation" not in slide["text"].lower():
            continue
        lines = _lines(slide["text"])
        for i, line in enumerate(lines[:-1]):
            match = NAV_LINE.match(line)
            if not match or URL.match(lines[i + 1]):
                continue
            number, snippet = int(match.group(1)), _key(lines[i + 1])[:50]
            target = by_number.get(number)
            if snippet and target and snippet[:30] not in _key(target["text"]):
                actual = next((s["number"] for s in info["slides"]
                               if s is not slide and snippet[:30] in _key(s["text"])), None)
                if actual and actual != number:
                    out.append((TIDY, "The teacher slide index lists “%s” as slide %d; it is now slide %d."
                                % (match.group(2)[:50], number, actual), slide["number"]))
    return out


def _find_draft(info, label):
    aspect = re.search(r"critical aspect\s+(\d+)", label, re.I)
    for slide in info["student"]:
        if aspect and re.search(r"\[\[DRAFT:[^:\]]+:CA%s-" % aspect.group(1), slide["text"]):
            return slide["number"]
        if not aspect and "what if" in label.lower() and "[[DRAFT:" in slide["text"] and \
                "what if" in slide["text"].lower():
            return slide["number"]
    return None


def link_check(info):
    """Deck links that open the wrong file: a shown URL that opens a different file, or one file
    linked under two different names. Returns (level, text, slide)."""
    out, by_file = [], {}
    for slide in info["slides"]:
        for label, url in slide.get("links", []):
            target = _file_id(url)
            shown = URL.match(label or "")
            if shown:
                if target and _file_id(shown.group(0)) and _file_id(shown.group(0)) != target:
                    out.append((FIX, "Slide %d shows one file's address but the link opens a different file."
                                % slide["number"], slide["number"]))
                continue
            name = re.sub(r"^(open|teacher|for readers|for testers|students?)\s*[:—–-]\s*", "", label or "", flags=re.I)
            name = re.split(r"\s+·\s+", name)[0]
            if target and _tokens(name):
                by_file.setdefault(target, []).append((name, slide))
    for target, uses in by_file.items():
        names = []  # one entry per distinct name, preferring the slide students see
        for name, slide in uses:
            same = next((n for n in names if _tokens(n[0]) <= _tokens(name) or _tokens(name) <= _tokens(n[0])), None)
            if not same:
                names.append([name, slide])
            elif _teacher_only(same[1]["text"]) and not _teacher_only(slide["text"]):
                same[1] = slide
        if len(names) > 1:
            (a, sa), (b, sb) = names[0], names[1]
            student = not _teacher_only(sa["text"]) or not _teacher_only(sb["text"])
            out.append((FIX if student else DECIDE,
                        "“%s” (slide %d) and “%s” (slide %d) open the same file — one of these links is wrong."
                        % (a, sa["number"], b, sb["number"]), sb["number"] if not _teacher_only(sb["text"]) else sa["number"]))
    return out


def author_notes(info):
    """Bracketed authoring notes and image placeholders left on student-facing slides."""
    out = []
    for slide in info["student"]:
        for match in AUTHOR_NOTE.finditer(slide["text"]):
            note = " ".join(match.group(0).split())
            out.append((slide["number"], note if len(note) <= 80 else note[:80].rsplit(" ", 1)[0] + " …]"))
    return out


# ---------- minutes ----------

def slide_minutes(text):
    """A rough classroom-minutes estimate for one student slide."""
    explicit = re.search(r"\((\d{1,3})\s*min", text)
    if explicit:
        return int(explicit.group(1))
    low, head = text.lower(), " ".join(_lines(text)[:2]).lower()
    markers = MARKER.findall(text)
    if re.search(r"\bunit exam\b|\bunit \d+ exam\b|\btest\b", head) and "revise" not in head:
        return 50
    if re.search(r"\blab\b|investigation|activity", head):
        return 35 if "lab" in head else 25
    if "bellringer" in head:
        return 8
    if "concept bank" in head:
        return 5
    minutes = 0
    for category, _, _ in markers:
        minutes += {"NOTES": 6, "DRAFT": 8, "BANK": 1, "OPTIONAL": 0}[category]
    if not markers:
        if re.search(r"explore|interactive|\bsim\b|video|watch", low):
            return 8
        if re.search(r"\bcase\b|reading|discuss|bioethics|concept check", low):
            return 10
        return 2
    return minutes


def segment_minutes(info, index):
    """Minutes for one deck day; a revision day also counts the boxes it sends students back to."""
    segment = info["segments"][index]
    slides = [s for s in info["student"] if segment["start"] <= s["number"] <= segment["end"]
              and not s.get("skipped")]
    minutes = sum(slide_minutes(s["text"]) for s in slides)
    if segment["revision"]:
        steps = sum(len(REVISION_REF.findall(s["text"])) for s in slides)
        drafts = len({m.group(3) for s in info["slides"] for m in MARKER.finditer(s["text"])
                      if m.group(1) == "DRAFT"})
        minutes += 10 * (steps or drafts)
    return minutes


# ---------- matching Agenda meetings to deck days ----------

def _plan_tokens(text):
    text = DUE.sub(" ", text or "")
    text = URL.sub(" ", text)
    return _tokens(text)


def map_days(info, linked, topic_words=frozenset()):
    """Assign each linked meeting (in date order) a deck segment index or None.

    Meetings take the deck's days in order; a plan that names words found in
    only one later day jumps to that day (and the order continues from it)."""
    seg_tokens = []
    for segment in info["segments"]:
        words = set()
        for slide in info["student"]:
            if segment["start"] <= slide["number"] <= segment["end"]:
                words |= _tokens(slide["text"])
        seg_tokens.append(words)
    owners = {}
    for index, words in enumerate(seg_tokens):
        for word in words:
            owners.setdefault(word, set()).add(index)
    cursor, out = 0, []
    for meeting in linked:
        unique = {}
        for word in _plan_tokens(meeting.get("plan", "")) - set(topic_words):
            if len(owners.get(word, ())) == 1:
                index = next(iter(owners[word]))
                unique.setdefault(index, set()).add(word)
        strong = [i for i, words in unique.items() if len(words) >= 2]
        if len(strong) == 1:
            out.append((meeting, strong[0], sorted(unique[strong[0]])))
            cursor = strong[0] + 1
        elif cursor < len(info["segments"]):
            out.append((meeting, cursor, []))
            cursor += 1
        else:
            out.append((meeting, None, []))
    return out


# ---------- print, supplies, digital ----------

def _split_items(line):
    body, purpose = (re.split(r"\s+[—–]\s+", line, maxsplit=1) + [""])[:2]
    parts, depth, current = [], 0, ""
    for piece in re.split(r"(,\s*(?:and\s+)?|\s+and\s+|[()])", body):
        if piece == "(":
            depth += 1
        elif piece == ")":
            depth -= 1
        if depth == 0 and piece and re.fullmatch(r",\s*(?:and\s+)?|\s+and\s+", piece):
            parts.append(current)
            current = ""
        else:
            current += piece
    parts.append(current)
    items = []
    for part in (p.strip(" .;") for p in parts):
        if not part:
            continue
        if items and re.match(r"or\b", part):
            items[-1] += ", " + part
        else:
            items.append(part)
    return items, purpose.strip()


def _day_hint(text):
    match = re.search(r"\((?:day|meeting)\s+(\d+)\)", text, re.I)
    return int(match.group(1)) if match else None


def _qty(text):
    low = text.lower()
    if "per student" in low:
        return "student"
    if "per group" in low or "per lab group" in low or "per station" in low:
        return "group"
    if "class set" in low or "open-book" in low or any(w in low for w in REFERENCE_WORDS):
        return "class set"
    if "support" in low or "as needed" in low:
        return "support"
    return "student"


def print_items(info, mapped):
    """Print needs from the deck's TO PRINT / PRINT / HAND OUT / PREPARE lines, each tied to
    the meeting that uses it when the slides say so."""
    items, lines = [], []
    bullet = re.compile(r"^[•\-–]\s*")
    for head in PRINT_HEADS:
        lines += [bullet.sub("", line) for _, line in info["sections"].get(head, [])]
    for _, line in info["sections"].get("prepare", []):
        line = bullet.sub("", line)
        if re.match(r"print\b", line, re.I):
            lines.append(re.sub(r"^print( or adapt)?( the)?\s+", "", line, flags=re.I))
    seg_dates = {}
    for meeting, index, _ in mapped:
        if index is not None:
            seg_dates.setdefault(index, []).append(meeting["date"])
    for line in lines:
        if line.lower().startswith(DIGITAL_PREFIXES):
            continue
        name = re.split(r"\s+[·(]|\s+[—–]\s+(?=(?:see|if|for|use|print|optional)\b)", line, maxsplit=1)[0]
        name = name.strip(" “”\"")
        key = _key(name)
        if not key or key in ("nothing", "none"):
            continue
        twin = next((i for i in items if _tokens(i["name"]) <= _tokens(name)
                     or _tokens(name) <= _tokens(i["name"])), None)
        if twin:
            if re.search(r"\bper\b|class set|support", line, re.I):
                twin["qty"] = _qty(line)
            continue
        conditional = bool(re.search(r"\bif\b|as needed|optional|challenge|support", line, re.I))
        index = _item_segment(info, name, line)
        items.append({"name": name, "line": line, "qty": _qty(line), "segment": index,
                      "dates": seg_dates.get(index, []) if index is not None else [],
                      "conditional": conditional, "link": _item_link(info, name)})
    return items


def _item_segment(info, name, line):
    explicit = re.search(r"\bslide\s+(\d+)", line, re.I)
    if explicit:
        return segment_of(info, int(explicit.group(1)))
    hint = _day_hint(line)
    if hint is not None:
        for index, segment in enumerate(info["segments"]):
            if segment["label"] == hint or index + 1 == hint:
                return index
    aspect = re.search(r"\bCA\s?(\d+)\b", name)
    if aspect and 0 < int(aspect.group(1)) <= len(info["aspects"]):
        key = _key(info["aspects"][int(aspect.group(1)) - 1])
        for slide in info["student"]:
            if any(_key(label).startswith(key) for label in ASPECT_LABEL.findall(slide["text"])):
                return segment_of(info, slide["number"])
    words = _tokens(name) - _tokens(info["title"]) - _tokens(info["reference"].splitlines()[1] if
                                                             info["reference"] else "")
    for slide in info["student"]:
        if words and words <= _tokens(slide["text"]):
            return segment_of(info, slide["number"])
    return None


def _item_link(info, name):
    key = _key(name)
    for slide in info["slides"]:
        for label, url in slide.get("links", []):
            if key and (key in _key(label) or (_key(label) and _key(label) in key)):
                return url
        lines = _lines(slide["text"])
        for i, line in enumerate(lines[:-1]):
            if key and _key(line).startswith(key[:40]) and URL.match(lines[i + 1]):
                return lines[i + 1]
    return ""


def supply_lines(prep, info_list, target_segments):
    """Supply rows from the Agenda's lab / prep cell and the decks' BUY AHEAD / PUT OUT lines."""
    rows = []
    status_on_order, status_buy = [], []
    for part in re.split(r"\s+·\s+|\n", prep or ""):
        part = part.strip()
        match = re.match(r"(BUY|PULL)\s*:\s*(.+)", part, re.I)
        if match:
            kind = "buy" if match.group(1).upper() == "BUY" else "pull"
            for item in _split_items(match.group(2))[0]:
                rows.append({"item": item, "kind": kind, "source": "Agenda lab / prep", "purpose": ""})
            continue
        if re.match(r"✔\s*on order", part, re.I):
            # "✔ on order — yeast, balloons, water bath, straws; 🛒 sugar"
            body = re.split(r"[—–:]", part, maxsplit=1)[-1]
            ordered, _, rest = body.partition("🛒")
            status_on_order += _split_items(ordered.strip(" ;"))[0]
            status_buy += _split_items(rest.strip(" ;"))[0] if rest else []
        for buy in re.findall(r"🛒\s*([^;·⚠]+)", part):
            if "on order" not in part.lower():
                status_buy += _split_items(buy)[0]
        for add in re.findall(r"⚠\s*ADD\s+([^;·(]+)", part, re.I):
            status_buy += _split_items(add)[0]
    for row in rows:
        row["agenda"] = True
    for info in info_list:
        wanted = set(target_segments.get(info["id"]) or [])
        labels = {info["segments"][i]["label"] for i in wanted} | {i + 1 for i in wanted}
        demo_slides = {s["number"] for s in info["teacher"]
                       if re.search(r"\bdemo\b", " ".join(_lines(s["text"])[:2]), re.I)}
        for head, kind in (("buy ahead", "buy"), ("buy", "buy"), ("put out", "pull"),
                           ("put out for students", "pull"), ("pull", "pull"), ("materials", "pull")):
            for number, line in info["sections"].get(head, []):
                if re.match(r"nothing\b", line, re.I):
                    continue
                hint = _day_hint(line)
                if hint is not None and hint not in labels:
                    continue
                # A demo's own materials belong to the day the demo sits in.
                if hint is None and number in demo_slides and segment_of(info, number) not in wanted:
                    continue
                clean = re.sub(r"\s*\((?:day|meeting)\s+\d+\)", "", re.sub(r"^[•\-]\s*", "", line), flags=re.I)
                items, purpose = _split_items(clean)
                for item in items:
                    if NOT_SUPPLY.search(item):
                        continue
                    rows.append({"item": item, "kind": kind, "source": info["title"], "agenda": False,
                                 "purpose": purpose if kind == "buy" else ""})
    merged = []
    for row in rows:
        words = _tokens(re.sub(r"\(.*?\)", "", row["item"]))
        if not words:
            continue
        twin = next((m for m in merged if m["words"] <= words or words <= m["words"]), None)
        if twin:
            if len(row["item"]) > len(twin["item"]):
                twin["item"] = row["item"]
            twin["words"] |= words
            # The Agenda's lab / prep cell is the teacher's current word on buy vs pull.
            if row["agenda"] and not twin["agenda"]:
                twin["kind"] = row["kind"]
            elif row["agenda"] == twin["agenda"] and row["kind"] == "buy":
                twin["kind"] = "buy"
            twin["purpose"] = twin["purpose"] or row["purpose"]
            twin["agenda"] = twin["agenda"] or row["agenda"]
            continue
        merged.append({**row, "words": words})
    for row in merged:
        if row["kind"] == "buy":
            if any(_tokens(o) and _tokens(o) <= row["words"] for o in status_on_order):
                row["kind"] = "on order"
            if any(_tokens(b) and _tokens(b) <= row["words"] for b in status_buy):
                row["kind"] = "buy"
        del row["words"]
    for extra in status_buy:
        if extra and not any(_tokens(extra) <= _tokens(r["item"]) for r in merged if _tokens(extra)):
            merged.append({"item": extra, "kind": "buy", "source": "Agenda lab / prep", "purpose": "",
                           "agenda": True})
    return merged


def copies(qty, sizes):
    total, largest = sum(sizes), max(sizes) if sizes else 0
    if not sizes:
        return {"student": "1 per student", "group": "1 per group", "class set": "1 class set",
                "support": "a few support copies"}[qty]
    return {"student": "%d (1 per student + 2 spare)" % (total + 2),
            "group": "%d (1 per group of %d)" % (sum(math.ceil(s / GROUP_SIZE) for s in sizes), GROUP_SIZE),
            "class set": "%d (one class set, reused across sections)" % largest,
            "support": "%d support copies" % max(3, math.ceil(total * 0.15))}[qty]


def digital_option(item, info):
    """A digital form for a printed item, when one exists or can be linked without editing anything."""
    name, low = item["name"], item["name"].lower()
    if re.search(r"\b(%s)\b" % "|".join(LAB_WORDS), low) and not any(w in low for w in WORKSHEET_WORDS):
        return None
    aspect = re.search(r"\bCA\s?(\d+)\b", name)
    if aspect and 0 < int(aspect.group(1)) <= len(info["aspects"]):
        key = _key(info["aspects"][int(aspect.group(1)) - 1])
        boxes = [s["number"] for s in info["student"] if "[[NOTES:" in s["text"] and any(
            _key(label).startswith(key) for label in ASPECT_LABEL.findall(s["text"]))]
        if boxes:
            return {"name": name, "kind": "in deck",
                    "text": "The deck already has this critical aspect's answer boxes (slides %d–%d). "
                            "Students work there; print only support copies." % (boxes[0], boxes[-1]),
                    "url": "", "saves": "student"}
    if any(w in low for w in REFERENCE_WORDS):
        return None  # already printed as one class set
    doc = DOC_ID.match(item.get("link") or "")
    if doc and any(w in low for w in WORKSHEET_WORDS):
        return {"name": name, "kind": "copy link",
                "text": "Students can make their own editable copy and turn it in with the lesson's "
                        "Form; keep paper for students who need it.",
                "url": "https://docs.google.com/document/d/%s/copy" % doc.group(1), "saves": ""}
    return None


# ---------- the week ----------

def _class_rows(agenda, class_key):
    entry = agenda.classes[class_key]
    if not entry["layout"]:
        return []
    return [agenda_module.build_lesson(entry["cells"], entry["layout"], row, "")
            for row, _ in entry["layout"]["rows"]]


def _week_key(label):
    """'Wk 13 — closed all week' -> 'wk13'; 'Wk 16b — Mon/Tue only' -> 'wk16b'."""
    match = re.match(r"\s*wk\s*0*(\d+)([a-z]?)\b", str(label or ""), re.I)
    return "wk%s%s" % (match.group(1), match.group(2).lower()) if match else agenda_module.norm(label)


def _week_by_label(agenda, label):
    return next((w for w in agenda.weeks if _week_key(w["label"]) == _week_key(label)), None)


def next_week(agenda, today):
    """The first Agenda week starting after `today` (the week to prepare)."""
    later = [w for w in agenda.weeks if date.fromisoformat(w["monday"]) > today]
    return min(later, key=lambda w: w["monday"]) if later else None


def run(agenda, week_label, sources, roster_sizes=None):
    """The preflight for one Agenda week. `roster_sizes` is {class_key: [section sizes]}."""
    roster_sizes = roster_sizes or {}
    week = _week_by_label(agenda, week_label)
    if not week:
        raise ValueError("week")
    monday = date.fromisoformat(week["monday"])
    weeks_by_label = {_week_key(w["label"]): w for w in agenda.weeks}
    out = {"week": week["label"], "monday": week["monday"], "closures": week.get("closures", ""),
           "exceptions": [], "print": [], "digital": [], "supplies": [], "schoology": [],
           "meetings": [], "checked": {"lessons": 0, "decks": 0, "links": 0}}
    deck_cache, file_cache = {}, {}

    def load_deck(deck_id):
        if deck_id not in deck_cache:
            try:
                deck_cache[deck_id] = read_deck(sources.deck(deck_id))
                out["checked"]["decks"] += 1
            except SourceError as error:
                deck_cache[deck_id] = error
        return deck_cache[deck_id]

    def file_meta(file_id):
        if file_id not in file_cache:
            try:
                file_cache[file_id] = sources.file(file_id)
            except SourceError as error:
                file_cache[file_id] = error
            out["checked"]["links"] += 1
        return file_cache[file_id]

    for class_key, class_name in agenda_module.CLASSES:
        entry = agenda.classes[class_key]
        if not entry["layout"]:
            if entry.get("problem"):
                out["exceptions"].append(_exception(FIX, class_name, "", entry["problem"]))
            continue
        plan_days = list(entry["layout"]["plans"])
        rows = _class_rows(agenda, class_key)
        # Every meeting of this class, all year, with the deck its row links.
        timeline = []
        for lesson in rows:
            row_week = weeks_by_label.get(_week_key(lesson["week"]))
            if not row_week:
                continue
            for meeting in meetings(row_week, plan_days):
                plan = next((p for p in lesson["plans"] if p["day"] == meeting["day"]), {})
                timeline.append({**meeting, "plan": plan.get("plan", ""), "note": plan.get("note", ""),
                                 "deck": _deck_id(lesson["links"].get("deck", "")), "lesson": lesson,
                                 "week": row_week["label"]})
        target = [l for l in rows if _week_key(l["week"]) == _week_key(week["label"])]
        if not target:
            out["exceptions"].append(_exception(DECIDE, class_name, "", "No %s row in the %s tab."
                                                % (week["label"], entry["tab"])))
            continue
        for lesson in target:
            kind = agenda_module.lesson_kind(lesson["topic"])
            if kind != "lesson":
                continue
            out["checked"]["lessons"] += 1
            _check_lesson(out, agenda, class_key, class_name, lesson, week, monday, plan_days,
                          timeline, load_deck, file_meta, sources, roster_sizes.get(class_key, []))
    order = {FIX: 0, DECIDE: 1, TIDY: 2}
    out["exceptions"].sort(key=lambda e: (order[e["level"]], e["date"] or "9", e["class"]))
    out["print"].sort(key=lambda p: (p["date"] or "9", p["class"], p["name"]))
    return out


def _check_lesson(out, agenda, class_key, class_name, lesson, week, monday, plan_days, timeline,
                  load_deck, file_meta, sources, sizes):
    topic = lesson["topic"]
    url = lesson["edit_url"] or ""

    def add(level, text, action="", link="", day=None):
        out["exceptions"].append(_exception(level, class_name, topic, text, action, link or url, day))

    week_meetings = [m for m in timeline if m["lesson"] is lesson]
    # ---- each day of the week ----
    for meeting in week_meetings:
        plan, day = meeting["plan"], meeting["date"]
        stated = re.match(r"\s*(?:⚠\s*check the day:\s*)?(\d{1,2})/(\d{1,2})", plan, re.I)
        if stated:
            said = _date_in_year(int(stated.group(1)), int(stated.group(2)), monday)
            if said and said != day:
                add(FIX, "The %s plan is dated %d/%d, which is %s." % (meeting["day"], said.month, said.day,
                                                                      _fmt(said)), "Fix the date or move the plan.", day=day)
        body = re.sub(r"^\s*(?:⚠\s*check the day:\s*)?\d{1,2}/\d{1,2}(?:/\d{2,4})?\s*", "", plan, flags=re.I)
        if meeting["meets"]:
            flagged = bool(re.search(r"verify day", plan + meeting["note"], re.I))
            claims_off = any(CLAIMS_OFF.search(line) for line in _lines(body))
            if not body.strip():
                add(FIX, "%s meets, but there is no plan." % class_name, "Plan this day.", day=day)
            elif claims_off:
                add(FIX, "%s meets (%s), but the plan says it doesn't%s." % (
                    class_name, _side_text(week, day), " and is flagged VERIFY DAY" if flagged else ""),
                    "This is a teaching day: plan it, then clear the flag.", day=day)
            elif NEEDS_CONTENT.search(body):
                add(FIX, "The plan still says NEEDS CONTENT.", "Plan this day.", day=day)
            elif flagged:
                add(DECIDE, "The plan is flagged “VERIFY DAY”.",
                    "Confirm the plan matches the real schedule, then delete the flag.", day=day)
            for found in ASSESSMENT.finditer(body):
                name = " ".join(re.sub(r"\(.*?\)", "", found.group(0)).split()).strip(" -—·")
                if re.search(r"moved|review|reinforcement", name, re.I) and not re.match(r"unit exam", name, re.I):
                    name = re.split(r"\s*[(·]", name)[0]
                out["print"].append({"class": class_name, "lesson": topic, "name": name[:80],
                                     "date": day.isoformat(), "day": _fmt(day),
                                     "copies": copies("student", sizes), "optional": False, "url": "",
                                     "file_id": "", "note": "From the Agenda plan; link the file to add it to the packet."})
                break
        else:
            leftover = NOT_MEETING.sub(" ", body)
            leftover = re.sub(r"\b(A|B) Friday\b|bio|forensics|a&p|biology|in-service|\(.*?\)", " ",
                              leftover, flags=re.I)
            if len(_tokens(leftover)) >= 4 and not re.search(r"\bmoved\b.*\bto\b", leftover, re.I):
                add(FIX, "%s doesn't meet (%s), but there is teaching planned for it."
                    % (class_name, meeting["why"]), "Move that work to a meeting day.", day=day)
    # Due dates written in the plans must land on a meeting day.
    meets_on = {m["date"] for m in timeline if m["meets"]}
    for meeting in week_meetings:
        for match in DUE.finditer(meeting["plan"] + "\n" + meeting["note"]):
            due = _date_in_year(int(match.group(3)), int(match.group(4)), monday)
            if due and due not in meets_on:
                add(FIX, "%s.%s is due %s, when %s doesn't meet." % (match.group(1), match.group(2).strip(),
                                                                    _fmt(due), class_name),
                    "Move the due date to a meeting day.", day=meeting["date"])
    # ---- the deck(s) ----
    decks = [d for d in (_deck_id(lesson["links"].get("deck", "")), _deck_id(lesson["links"].get("deck2", "")))
             if d]
    if not decks:
        add(DECIDE, "No teacher deck is linked.", "Link the deck in the Agenda (_teacherURL).")
    student = next((m["url"] for m in lesson["materials"] if m["label"] == "Student copy"), "")
    if decks and student and _deck_id(student) and _deck_id(student) != decks[0]:
        add(FIX, "The student copy link opens a different deck from the teacher deck.",
            "Point _studentURL at the teacher deck's /copy link.", student)
    infos, target_segments = [], {}
    for deck_id in decks:
        info = load_deck(deck_id)
        deck_url = "https://docs.google.com/presentation/d/%s/edit" % deck_id
        if isinstance(info, SourceError):
            add(FIX, str(info) + " Students' /copy link will fail too.",
                "Restore or re-share the deck, or link the right one in the Agenda.", deck_url)
            continue
        infos.append(info)
        _check_deck(out, add, class_name, lesson, info, deck_url, week, monday, timeline, target_segments, sizes)
    # Plans that link a "deck for this day" other than the lesson's deck.
    for meeting in week_meetings:
        for found in DECK_ID.findall(meeting["plan"]):
            if decks and found not in decks:
                add(TIDY, "The %s plan links a different deck from the lesson's teacher deck."
                    % meeting["day"], "Use one deck link for this lesson.", day=meeting["date"])
    # ---- folder, Forms, Worksheets tab ----
    labels = {}
    for info in infos:
        found = WEEK_NUMBER.search(info["title"])
        if found:
            labels["deck"] = int(found.group(1))
    folder = next((m["url"] for m in lesson["materials"] if m["label"] == "Lesson folder"), "")
    folder_id = _file_id(folder)
    children = []
    if folder_id:
        meta = file_meta(folder_id)
        if isinstance(meta, SourceError):
            add(FIX, "The lesson folder link is broken: " + str(meta), "Link the lesson's folder in the Agenda.", folder)
        elif meta.get("trashed"):
            add(FIX, "The lesson folder is in the trash.", "Restore it or link the right folder.", folder)
        else:
            found = WEEK_NUMBER.search(meta.get("name", ""))
            if found:
                labels["folder"] = int(found.group(1))
            try:
                children = sources.children(folder_id)
            except SourceError:
                children = []
    week_number = int(WEEK_NUMBER.search(week["label"]).group(1)) if WEEK_NUMBER.search(week["label"]) else None
    for form in lesson["forms"]:
        if not form.get("form_id"):
            add(FIX, form.get("problem") or "A Turn-in link isn't a readable Form.", "", form["url"])
            continue
        try:
            info = sources.form(form["form_id"])
        except SourceError as error:
            add(FIX, str(error), "Link the lesson's own Form in the Agenda.", form["url"])
            continue
        out["checked"]["links"] += 1
        found = WEEK_NUMBER.search(info.get("title", ""))
        if found and week_number and int(found.group(1)) != week_number and int(found.group(1)) not in labels.values():
            add(TIDY, "The Turn-in Form is titled “%s” but this lesson is in %s."
                % (info["title"], week["label"]),
                "Rename the Form (or confirm it is meant to collect this week's work).", form["url"])
        if info.get("responder_url"):
            out["schoology"].append({"class": class_name, "lesson": topic, "what": "Turn-in Form",
                                     "url": info["responder_url"]})
    if decks and isinstance(load_deck(decks[0]), dict):
        out["schoology"].append({"class": class_name, "lesson": topic, "what": "Student deck (make a copy)",
                                 "url": "https://docs.google.com/presentation/d/%s/copy" % decks[0]})
    if len(set(labels.values())) > 1:
        add(TIDY, "This lesson's deck and folder are labelled for different weeks (%s)."
            % ", ".join("%s: Wk %02d" % (k, v) for k, v in sorted(labels.items())),
            "Rename so one lesson has one week label.")
    deck_weeks = {_week_key(m["week"]) for m in timeline if m["deck"] and m["deck"] in decks}
    canonical = _check_worksheets(out, add, agenda, class_name, lesson, deck_weeks | {_week_key(week["label"])},
                                  infos, children)
    for item in out["print"]:
        if item["lesson"] == topic and item["class"] == class_name:
            words = _tokens(item["name"])
            match = next((row for row in canonical if words and (words <= _tokens(row["document"])
                                                                 or _tokens(row["document"]) <= words)), None)
            if match:
                item["url"] = match["url"] or "https://docs.google.com/document/d/%s/edit" % match["file_id"]
                item["file_id"] = match["file_id"] or _file_id(match["url"])
                for option in out["digital"]:
                    if (option["class"], option["lesson"], option["name"], option["kind"]) == (
                            class_name, topic, item["name"], "copy link") and DOC_ID.match(item["url"]):
                        option["url"] = "https://docs.google.com/document/d/%s/copy" % item["file_id"]
    # ---- supplies ----
    rows = supply_lines(lesson.get("prep", ""), infos, target_segments)
    for row in rows:
        out["supplies"].append({"class": class_name, "lesson": topic, **row})
    needed_by = re.search(r"PREP by (\w+ \d{1,2}/\d{1,2})", lesson.get("prep", ""))
    for row in out["supplies"]:
        if row["lesson"] == topic and row["class"] == class_name:
            if needed_by and row.get("agenda"):
                row["by"] = needed_by.group(1)
            row.pop("agenda", None)


def _side_text(week, day):
    side = friday_side(week.get("closures", ""))
    return "%s Friday" % side if day.weekday() == 4 and side else "a meeting day"


def _check_deck(out, add, class_name, lesson, info, deck_url, week, monday, timeline, target_segments, sizes):
    topic = lesson["topic"]
    if info.get("trashed"):
        add(FIX, "The teacher deck is in the trash.", "Restore it.", deck_url)
    linked = sorted((m for m in timeline if m["meets"] and m["deck"] == info["id"]), key=lambda m: m["date"])
    topic_words = _tokens(topic) | _tokens(info["title"])
    mapped = map_days(info, linked, topic_words)
    in_week = [(m, i, why) for m, i, why in mapped if m["lesson"] is lesson]
    target_segments[info["id"]] = sorted({i for _, i, _ in in_week if i is not None})
    days_needed = len(info["segments"])
    # Fit: the deck's days against the meetings the Agenda gives it.
    if len(linked) < days_needed:
        add(FIX, "The deck has %d class days but the Agenda gives it %d meeting(s) (%s)."
            % (days_needed, len(linked), ", ".join(_fmt(m["date"]) for m in linked) or "none"),
            "Add a meeting or cut a day from the deck.", deck_url)
    for meeting, index, why in in_week:
        unplanned = [e for e in out["exceptions"] if e["date"] == meeting["date"].isoformat()
                     and e["class"] == class_name and e["lesson"] == topic
                     and re.search(r"no plan|NEEDS CONTENT|says it doesn't", e["text"])]
        if unplanned:
            if index is not None:
                seg = info["segments"][index]
                hint = "Next in the deck: day %s (slides %d–%d, about %d min)%s." % (
                    seg["label"], seg["start"], seg["end"], segment_minutes(info, index),
                    " — the revision day" if seg["revision"] else "")
            else:
                nxt = next((m for m in timeline if m["date"] > meeting["date"] and m["meets"]
                            and m["deck"] and m["deck"] != info["id"]), None)
                hint = "This deck's days are used up by then%s." % (
                    "; the next deck (%s) starts %s — start it early or plan an extension"
                    % (nxt["lesson"]["topic"], _fmt(nxt["date"])) if nxt else "")
            for exc in unplanned:
                exc["action"] = hint
        if index is not None:
            minutes = segment_minutes(info, index)
            share = sum(1 for _, i, _ in mapped if i == index)
            budget = MEETING_MINUTES * share
            seg = info["segments"][index]
            out["meetings"].append({"class": class_name, "lesson": topic, "date": meeting["date"].isoformat(),
                                    "day": _fmt(meeting["date"]), "deck_day": seg["label"],
                                    "slides": "%d–%d" % (seg["start"], seg["end"]), "minutes": minutes,
                                    "shared_with": share, "matched_by": why})
            if unplanned:
                continue
            if minutes > OVER * share:
                add(DECIDE, "%s: deck day %s (slides %d–%d) is about %d minutes of work for %d minutes of class."
                    % (_fmt(meeting["date"]), seg["label"], seg["start"], seg["end"], minutes, budget),
                    "Split it across two meetings or mark what to cut.", deck_url, meeting["date"])
            elif minutes < UNDER * share and not re.search(r"exam|test|assessment", meeting["plan"], re.I):
                add(DECIDE, "%s: deck day %s (slides %d–%d) is only about %d minutes of a %d-minute block."
                    % (_fmt(meeting["date"]), seg["label"], seg["start"], seg["end"], minutes, budget),
                    "Add the next activity or an extension.", deck_url, meeting["date"])
    # Revision must come after the aspects are first taught.
    seg_dates = {}
    for meeting, index, _ in mapped:
        if index is not None:
            seg_dates.setdefault(index, []).append(meeting["date"])
    revision = [i for i, s in enumerate(info["segments"]) if s["revision"]]
    teaching_last = max((max(d) for i, d in seg_dates.items() if i not in revision), default=None)
    for meeting in [m for m in timeline if m["meets"]]:
        for match in DUE.finditer(meeting["plan"]):
            name = _tokens(match.group(2))
            if not name or not name <= (_tokens(info["title"]) | _tokens(topic) | set(_tokens(meeting["lesson"]["topic"]))):
                continue
            if not (name <= _tokens(info["title"])):
                continue
            due = _date_in_year(int(match.group(3)), int(match.group(4)), monday)
            kind = match.group(1).upper()
            if not due or due < monday - timedelta(days=14):
                continue
            if kind == "NOTES" and teaching_last and due < teaching_last:
                add(DECIDE, "Notes.%s is due %s, but the deck's last teaching day lands %s — those "
                            "answer boxes can't be filled by then." % (match.group(2).strip(), _fmt(due),
                                                                      _fmt(teaching_last)),
                    "Move the Notes due date, or the Hub will score empty boxes.", deck_url)
            if kind == "DRAFT" and revision:
                rev_dates = seg_dates.get(revision[0], [])
                if (rev_dates and due < min(rev_dates)) or (teaching_last and due <= teaching_last):
                    when = _fmt(min(rev_dates)) if rev_dates else "after %s" % _fmt(teaching_last)
                    add(FIX, "DRAFT.%s (revision) is due %s, but the deck's revision day falls %s and its "
                             "last critical aspect is first taught %s." % (
                                 match.group(2).strip(), _fmt(due), when,
                                 _fmt(teaching_last) if teaching_last else "?"),
                        "Revision must follow first answers: move the due date or the plan.", deck_url)
    # Variation Theory structure and references.
    for level, text, slide in vt_check(info):
        add(level, text, "", _slide_url(info, deck_url, slide))
    for level, text, slide in link_check(info):
        add(level, text, "Fix the link on that slide.", _slide_url(info, deck_url, slide))
    # Dates written on student slides that this deck is no longer taught on.
    taught = {m["date"] for m in linked}
    stale = {}
    for slide in info["student"]:
        for match in DOW_DATE.finditer(slide["text"]):
            day = _date_in_year(int(match.group(2)), int(match.group(3)), monday)
            if day and day not in taught and abs((day - monday).days) < 120:
                if slide["number"] not in stale.setdefault(day, []):
                    stale[day].append(slide["number"])
    if stale and any(m["lesson"] is lesson for m in linked):
        first = sorted(stale)[:4]
        add(FIX, "Student slides still give dates this deck isn't taught on: %s. It is taught %s."
            % ("; ".join("%s (slide %s)" % (_fmt(d), ", ".join(map(str, stale[d][:3]))) for d in first),
               ", ".join(_fmt(m["date"]) for m in linked)),
            "Update the dates on those slides.", _slide_url(info, deck_url, stale[first[0]][0]))
    notes = author_notes(info)
    if notes:
        add(FIX, "Authoring notes are visible on student slides: %s." % "; ".join(
            "slide %d %s" % (n, t) for n, t in notes[:4]) + (" and %d more" % (len(notes) - 4) if len(notes) > 4 else ""),
            "Resolve them or move them to speaker notes.", _slide_url(info, deck_url, notes[0][0]))
    # Print and digital alternatives.
    items = print_items(info, mapped)
    this_week = [d for d in (m["date"] for m, i, _ in in_week if i is not None)]
    for item in items:
        dates_here = [d for d in item["dates"] if d in this_week]
        if item["segment"] is not None and not dates_here:
            continue
        if item["segment"] is None and not this_week:
            continue
        option = digital_option(item, info)
        qty = item["qty"]
        if option and option["kind"] == "in deck":
            qty = "support"
        out["print"].append({"class": class_name, "lesson": topic, "name": item["name"],
                             "date": min(dates_here).isoformat() if dates_here else "",
                             "day": _fmt(min(dates_here)) if dates_here else "this week",
                             "copies": copies(qty, sizes), "optional": item["conditional"],
                             "url": item["link"], "file_id": _file_id(item["link"]),
                             "note": item["line"] if item["line"] != item["name"] else ""})
        if option:
            out["digital"].append({"class": class_name, "lesson": topic, **option})
        if not item["link"] and not item["conditional"]:
            add(DECIDE, "“%s” is on the print list but the deck doesn't link it." % item["name"],
                "Link the file on the deck's resource slide so the packet can include it.", deck_url,
                min(dates_here) if dates_here else None)


def _check_worksheets(out, add, agenda, class_name, lesson, weeks, infos, children):
    """The Agenda's Worksheets tab, the deck's links and the lesson folder should name one copy of
    each worksheet; a row with no document can't be printed. Returns the tab's rows with a document
    (the copies the packet prints)."""
    rows = [w for w in getattr(agenda, "worksheets", []) if _key(w["course"]) in (
        _key(class_name), _key({"A&P": "anatomy"}.get(class_name, class_name)))
            and _week_key(w["week"]) in weeks]
    deck_links = [(label, url) for info in infos for s in info["slides"] for label, url in s.get("links", [])]
    for info in infos:
        for s in info["slides"]:
            lines = _lines(s["text"])
            deck_links += [(lines[i - 1], line) for i, line in enumerate(lines) if i and URL.match(line)]
    for row in rows:
        name = row["document"]
        if not row["file_id"] and not row["url"]:
            if _week_key(row["week"]) != _week_key(lesson["week"]):
                continue
            add(FIX, "“%s” (%s) is listed in the Worksheets tab with no document." % (name, row["type"]),
                "Link or make the sheet; the print packet can't include it.")
            continue
        copies_found = {}
        wanted = _tokens(name)
        if row["file_id"]:
            copies_found[row["file_id"]] = "Worksheets tab"
        for label, url in deck_links:
            fid = _file_id(url)
            if fid and wanted and wanted <= _tokens(label):
                copies_found.setdefault(fid, "deck link")
        for child in children:
            if wanted and wanted <= _tokens(child.get("name", "")) and child.get("mimeType") != google.FOLDER:
                copies_found.setdefault(child["id"], "lesson folder")
        if len(copies_found) > 1:
            add(TIDY, "%d different copies of “%s”: %s. The packet uses the Worksheets tab copy."
                % (len(copies_found), name, ", ".join(sorted(set(copies_found.values())))),
                "Keep one copy and point the other links at it.")
    return [row for row in rows if row["file_id"] or row["url"]]


def _slide_url(info, deck_url, number):
    if not number:
        return deck_url
    slide = next((s for s in info["slides"] if s["number"] == number), None)
    return deck_url + ("#slide=id.%s" % slide["object_id"] if slide and slide.get("object_id") else "")


# ---------- the print packet ----------

def packet(result, sources):
    """One PDF: a cover page listing every print job, then each printable Google Doc / PDF."""
    from io import BytesIO
    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    cover = BytesIO()
    page = canvas.Canvas(cover, pagesize=letter)
    width, height = letter
    y = height - 54

    def line(text, size=10, bold=False, indent=0):
        nonlocal y
        if y < 54:
            page.showPage()
            y = height - 54
        page.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        page.drawString(54 + indent, y, text[:110])
        y -= size + 5

    line("Print / lab packet — %s (week of %s)" % (result["week"], result["monday"]), 14, True)
    line("")
    included, manual = [], []
    for item in result["print"]:
        mime = None
        if item.get("file_id"):
            meta = None
            try:
                meta = sources.file(item["file_id"])
            except SourceError:
                pass
            mime = (meta or {}).get("mimeType")
        printable = mime in ("application/pdf", "application/vnd.google-apps.document")
        (included if printable else manual).append((item, mime))
    for item, _ in included + manual:
        line("☐ %s — %s · %s: %s%s" % (item["day"], item["class"], item["name"], item["copies"],
                                       " (optional)" if item["optional"] else ""))
    if manual:
        line("")
        line("Print these yourself (not a Google Doc or PDF):", 10, True)
        for item, mime in manual:
            line("%s — %s" % (item["name"], item["url"] or "no link"), 9, indent=12)
    if result["supplies"]:
        line("")
        line("Supplies", 12, True)
        for row in result["supplies"]:
            line("☐ [%s] %s — %s%s" % (row["kind"], row["item"], row["class"],
                                       (" · by " + row["by"]) if row.get("by") else ""), 9)
    page.save()
    writer = PdfWriter()
    writer.append(PdfReader(BytesIO(cover.getvalue())))
    problems = []
    for item, mime in included:
        try:
            raw = sources.pdf(item["file_id"], mime)
            writer.append(PdfReader(BytesIO(raw)))
        except Exception:
            problems.append(item["name"])
    output = BytesIO()
    writer.add_metadata({"/Title": "Print / lab packet — %s" % result["week"]})
    writer.write(output)
    return output.getvalue(), problems


# ---------- the Hub ----------

def for_hub(token, config, agenda, week_label=None, today=None):
    """The preflight for `week_label`, or for the next Agenda week after today (school time)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    today = today or datetime.now(ZoneInfo("America/New_York")).date()
    if not week_label:
        week = next_week(agenda, today)
        if not week:
            raise ValueError("week")
        week_label = week["label"]
    students, _ = roster.load(token, config)
    sizes = {}
    for class_key, people in students.items():
        counts = {}
        for person in people:
            counts[person.get("section", "")] = counts.get(person.get("section", ""), 0) + 1
        sizes[class_key] = sorted(counts.values())
    result = run(agenda, week_label, GoogleSources(token), sizes)
    result["weeks"] = [w["label"] for w in agenda.weeks]
    result["agenda"] = {"title": agenda.title,
                        "url": "https://docs.google.com/spreadsheets/d/%s/edit" % agenda.spreadsheet_id}
    return result
