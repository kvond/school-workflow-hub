"""Completion scores for one student's deck: Notes and Revision, out of 10.

This is the teacher's completion rule (Prompt 1) as counting, for her to
review — it never judges how good an answer is:

    Notes    = 10 x (completed NOTES + completed BANK) / (required NOTES + BANK)
    Revision = 10 x completed DRAFT / required DRAFT

The required items are the codes in the deck's own [[MARKER-INVENTORY:...]]
header (or, without one, every code in the deck; a student's copy of another
version of the lesson is held to its own header) that have a box to answer in:
a code on the list that no box in the student's copy carries isn't counted,
so a deck with only 3 answerable boxes is out of those 3 (the teacher's rule). Each code is a small text
box such as [[NOTES:C03:CA1-RULE]] placed on its answer box. A box counts as
completed when the student wrote something in it that wasn't already in the
copy they were given; a box whose new text is the printed slide text copied
back is flagged, not credited. OPTIONAL work is listed, not scored. A zero
denominator is N/A. Writing in a box with no code is ignored for the scores
and reported by slide, so the deck can be coded next time.
"""
import difflib
from collections import Counter
import io
import re
import zipfile
import xml.etree.ElementTree as ET

P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
CATEGORIES = ("NOTES", "BANK", "DRAFT", "OPTIONAL")
MARKER = re.compile(r"\[\[(NOTES|BANK|DRAFT|OPTIONAL):([A-Za-z0-9_-]{1,40}):([A-Za-z0-9 _-]{1,80})\]\]")
INVENTORY = re.compile(r"\[\[MARKER-INVENTORY:([A-Za-z0-9_-]{1,40})\]\]")
ID_LIST = re.compile(r"\b(NOTES|DRAFT|OPTIONAL|BANK)_IDS=(.*?)(?=\s+[A-Z][A-Z_]*=|\s*$)", re.S)
COUNT = re.compile(r"\b(NOTES|DRAFT|OPTIONAL|BANK_TERMS)=(\d+)\b")
MAX_SLIDE_BYTES = 5_000_000
MAX_SLIDES = 200
COPY_WORDS = 6
MAX_ANSWER = 4000
NEEDS_CODES = "Needs embedded codes"


class Unreadable(Exception):
    """The file isn't a deck the dashboard can read."""


# ---------- reading a .pptx ----------

def _name(node, nv):
    props = node.find(nv + "/" + P + "cNvPr")
    if props is None:
        return ""
    return props.get("name") or ("id" + props.get("id", ""))


def _affine(xfrm, outer, group=False):
    """Compose a group's child-to-parent mapping with the outer one."""
    sx, sy, tx, ty = outer
    if xfrm is None or not group:
        return outer
    off, ext = xfrm.find(A + "off"), xfrm.find(A + "ext")
    choff, chext = xfrm.find(A + "chOff"), xfrm.find(A + "chExt")
    if off is None or ext is None or choff is None or chext is None:
        return outer
    ccx, ccy = int(chext.get("cx", 0)) or 1, int(chext.get("cy", 0)) or 1
    gx, gy = int(ext.get("cx", 0)) / ccx, int(ext.get("cy", 0)) / ccy
    gtx = int(off.get("x", 0)) - int(choff.get("x", 0)) * gx
    gty = int(off.get("y", 0)) - int(choff.get("y", 0)) * gy
    return (sx * gx, sy * gy, sx * gtx + tx, sy * gty + ty)


def _box(xfrm, transform):
    if xfrm is None:
        return None
    off, ext = xfrm.find(A + "off"), xfrm.find(A + "ext")
    if off is None or ext is None:
        return None
    sx, sy, tx, ty = transform
    return (sx * int(off.get("x", 0)) + tx, sy * int(off.get("y", 0)) + ty,
            sx * int(ext.get("cx", 0)), sy * int(ext.get("cy", 0)))


def _text(body):
    if body is None:
        return ""
    lines = ["".join(t.text or "" for t in p.iter(A + "t")) for p in body.findall(A + "p")]
    return "\n".join(lines).strip()


def _walk(node, units, transform, prefix=""):
    for child in node:
        if child.tag == P + "sp":
            ph = child.find(P + "nvSpPr/" + P + "nvPr/" + P + "ph")
            units.append({"key": prefix + _name(child, P + "nvSpPr"), "text": _text(child.find(P + "txBody")),
                          "box": _box(child.find(P + "spPr/" + A + "xfrm"), transform),
                          "title": ph is not None and ph.get("type") in ("title", "ctrTitle")})
        elif child.tag == P + "grpSp":
            xfrm = child.find(P + "grpSpPr/" + A + "xfrm")
            _walk(child, units, _affine(xfrm, transform, group=True),
                  prefix + _name(child, P + "nvGrpSpPr") + "/")
        elif child.tag == P + "graphicFrame":
            table = child.find(".//" + A + "tbl")
            frame = _box(child.find(P + "xfrm"), transform)
            if table is None or frame is None:
                continue
            key = prefix + _name(child, P + "nvGraphicFramePr")
            widths = [int(g.get("w", 0)) * transform[0] for g in table.findall(A + "tblGrid/" + A + "gridCol")]
            y = frame[1]
            for r, row in enumerate(table.findall(A + "tr")):
                height = int(row.get("h", 0)) * transform[1]
                x = frame[0]
                for c, cell in enumerate(row.findall(A + "tc")):
                    width = widths[c] if c < len(widths) else 0
                    units.append({"key": "%s/r%dc%d" % (key, r, c), "text": _text(cell.find(A + "txBody")),
                                  "box": (x, y, width, height)})
                    x += width
                y += height


def _slide_names(archive):
    """Slide part names in presentation order."""
    names = [n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
    try:
        rels = ET.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))
        targets = {r.get("Id"): "ppt/" + r.get("Target", "").lstrip("/").replace("ppt/", "", 1)
                   for r in rels}
        pres = ET.fromstring(archive.read("ppt/presentation.xml"))
        ordered = [targets.get(s.get(R + "id")) for s in pres.iter(P + "sldId")]
        ordered = [n for n in ordered if n in names]
        if ordered:
            return ordered
    except (KeyError, ET.ParseError):
        pass
    return sorted(names, key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))


def read_deck(raw):
    """{"slides": [{"number", "units": [{key, text, box}]}], "size": (cx, cy)} from .pptx bytes."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile, ValueError, TypeError):
        raise Unreadable("This file isn't a slide deck the dashboard can read.") from None
    with archive:
        size = (12192000, 6858000)
        try:
            pres = ET.fromstring(archive.read("ppt/presentation.xml"))
            node = pres.find(P + "sldSz")
            if node is not None:
                size = (int(node.get("cx", size[0])), int(node.get("cy", size[1])))
        except (KeyError, ET.ParseError, ValueError):
            pass
        slides = []
        for number, name in enumerate(_slide_names(archive)[:MAX_SLIDES], 1):
            if archive.getinfo(name).file_size > MAX_SLIDE_BYTES:
                raise Unreadable("One of this deck's slides is too large to read.")
            try:
                root = ET.fromstring(archive.read(name))
            except ET.ParseError:
                raise Unreadable("This deck couldn't be read.") from None
            units = []
            tree = root.find(P + "cSld/" + P + "spTree")
            if tree is not None:
                _walk(tree, units, (1, 1, 0, 0))
            seen = {}
            for unit in units:  # keep keys unique on a slide
                seen[unit["key"]] = seen.get(unit["key"], 0) + 1
                if seen[unit["key"]] > 1:
                    unit["key"] += "|%d" % seen[unit["key"]]
            slides.append({"number": number, "units": units})
    if not slides:
        raise Unreadable("This file has no slides.")
    return {"slides": slides, "size": size}


# ---------- codes and the inventory ----------

def markers(deck):
    """Every code in the deck: {category, code, id, slide, key, box}."""
    found = []
    for slide in deck["slides"]:
        for unit in slide["units"]:
            for match in MARKER.finditer(unit["text"]):
                found.append({"category": match.group(1), "code": match.group(2), "id": match.group(3).strip(),
                              "slide": slide["number"], "key": unit["key"], "box": unit["box"]})
    return found


def _parse_header(header, census):
    """{category: [ids]} from a code list, or None when its lists don't match its own counts.
    A category the list gives a count for but no ids (BANK_TERMS=6 with no BANK_IDS=)
    uses the deck's own codes for it."""
    lists = {c: list(census[c]) for c in CATEGORIES}
    counts = {("BANK" if k == "BANK_TERMS" else k): int(v) for k, v in COUNT.findall(header)}
    for category, values in ID_LIST.findall(header):
        parsed = [" ".join(v.split()) for v in re.split(r"[,\n]", values) if v.strip()]
        lists[category] = [] if parsed == ["-"] and counts.get(category) == 0 else parsed
    if any(lists.values()) and all(counts.get(c, len(lists[c])) == len(lists[c]) for c in CATEGORIES):
        return lists
    return None


def _inventory_text(units):
    """Inventory-bearing boxes only: adjacent lesson prose is not an ID list."""
    pieces = []
    for unit in units:
        value = unit["text"]
        marker = INVENTORY.search(value)
        if marker:
            value = value[marker.start():]
        if marker or COUNT.search(value) or re.search(r"\b(?:NOTES|DRAFT|OPTIONAL|BANK)_IDS=", value):
            pieces.append(value)
    return "\n".join(pieces)


def inventory(deck):
    """(lesson code, {category: [ids]}, basis, note): the deck's own code list
    (its [[MARKER-INVENTORY:...]] slide), else every code in the deck."""
    codes = markers(deck)
    census = {c: [] for c in CATEGORIES}
    for m in codes:
        if m["id"] not in census[m["category"]]:
            census[m["category"]].append(m["id"])
    code = codes[0]["code"] if codes else ""
    slide = next((s for s in deck["slides"] if any(INVENTORY.search(u["text"]) for u in s["units"])), None)
    if not slide:
        return code, census, "census", None if codes else "This deck has no codes."
    code = next(INVENTORY.search(u["text"]).group(1) for u in slide["units"] if INVENTORY.search(u["text"]))
    # The list may be split across several boxes on its slide: try reading
    # order first, then the order the boxes were made in.
    by_position = sorted(slide["units"], key=lambda u: (u["box"][1], u["box"][0]) if u["box"] else (0, 0))
    for units in (by_position, slide["units"]):
        lists = _parse_header(_inventory_text(units), census)
        if lists:
            return code, lists, "header", None
    return code, census, "census", "The deck's code list doesn't match its own counts, so every code in it was used."


# ---------- comparing a student's deck with the copy they were given ----------

def _plain(text):
    return MARKER.sub(" ", INVENTORY.sub(" ", text or "")).strip()


TOKEN = re.compile(r"\w+|[^\w\s]")


def _tokens(text):
    return TOKEN.findall(_plain(text).lower())


def added(before, after, exact=False):
    """What the student typed into a box: the stretch of the box from the
    first to the last word that wasn't in the copy they were given ('' when
    nothing is new). Lowercased word by word for counting, or with exact=True
    cut from the box as the student wrote it, capitals and punctuation kept."""
    old, new = _tokens(before), _tokens(after)
    if old == new:
        return ""
    spans = [(j1, j2) for tag, _, _, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False)
             .get_opcodes() if tag in ("insert", "replace")]
    words = [w for j1, j2 in spans for w in new[j1:j2]]
    plain = _plain(after)
    if not words and new and len(old) <= 6:
        return "chose " + (" ".join(plain.split()) if exact else " ".join(new))  # e.g. kept one of "↑ ↓ ?"
    if not any(re.search(r"\w", w) for w in words):
        return ""
    found = list(TOKEN.finditer(plain))
    if exact and len(found) == len(new):
        return " ".join(plain[found[spans[0][0]].start():found[spans[-1][1] - 1].end()].split())
    return " ".join(new[spans[0][0]:spans[-1][1]])


def _squash(text):
    """'Inductive reasoning', 'inductive-reasoning' and 'InductiveReasoning' all read 'inductivereasoning'."""
    return re.sub(r"[^a-z0-9]+", "", _plain(text).lower())


def _center(box):
    return (box[0] + box[2] / 2, box[1] + box[3] / 2)


def _inside(point, box, margin=0):
    return (box[0] - margin <= point[0] <= box[0] + box[2] + margin
            and box[1] - margin <= point[1] <= box[1] + box[3] + margin)


def _distance(point, box):
    dx = max(box[0] - point[0], 0, point[0] - box[0] - box[2])
    dy = max(box[1] - point[1], 0, point[1] - box[1] - box[3])
    return (dx * dx + dy * dy) ** 0.5


def _signature(slide):
    return {(m.group(1), m.group(3).strip()) for u in slide["units"] for m in MARKER.finditer(u["text"])}


def _printed(slide):
    return " ".join(_plain(u["text"]) for u in slide["units"] if not MARKER.search(u["text"]))[:600]


def _align(student, template):
    """{student slide number: template slide} by the codes each slide carries,
    then by position for slides without codes when the decks are the same
    length, then by the slide's printed text: a copy of an older version of the
    deck may lack codes and have slides added or removed."""
    by_codes, pairs = [], {}
    for t in template["slides"]:
        by_codes.append((_signature(t), t))
    for s in student["slides"]:
        mine = _signature(s)
        if mine:
            best = max(by_codes, key=lambda item: len(item[0] & mine), default=(set(), None))
            if best[1] is not None and best[0] & mine:
                pairs[s["number"]] = best[1]
    if len(student["slides"]) == len(template["slides"]):
        for s, t in zip(student["slides"], template["slides"]):
            pairs.setdefault(s["number"], t)
    # How much of a handed-out slide's printed text is on the student's slide
    # (their answers add text, so it's measured against the handed-out slide).
    def kept(printed, mine):
        blocks = difflib.SequenceMatcher(None, printed, mine, autojunk=False).get_matching_blocks()
        return sum(b.size for b in blocks) / len(printed)
    taken = {t["number"] for t in pairs.values()}
    printed = {t["number"]: _printed(t) for t in template["slides"]}
    for s in student["slides"]:
        mine = _printed(s)
        if s["number"] in pairs or not mine:
            continue
        best = max(((kept(printed[t["number"]], mine), t) for t in template["slides"]
                    if t["number"] not in taken and len(printed[t["number"]]) >= 15),
                   key=lambda item: item[0], default=(0, None))
        if best[1] is not None and best[0] >= 0.8:
            pairs[s["number"]] = best[1]
            taken.add(best[1]["number"])
    return pairs


def _written(student_slide, template_slide, size=(12192000, 6858000)):
    """([(unit, added words)] for boxes the student wrote in on this slide,
    {box key: those words as the student wrote them}). A card dragged to a new
    place counts as 'moved: …' (credited only where it lands on a code)."""
    base = {u["key"]: u for u in template_slide["units"]} if template_slide else {}
    out, exact = [], {}
    for unit in student_slide["units"]:
        before = base.get(unit["key"])
        if template_slide and unit["box"]:
            # Google export renumbers shape names after a copy gains a box.
            # The same name can then refer to an unrelated heading or prompt.
            # Prefer an unambiguous unchanged rectangle, including empty boxes.
            same_place = [u for u in template_slide["units"] if u["box"] and
                          max(abs(a - b) for a, b in zip(u["box"], unit["box"])) <= 0.001 * max(size)]
            if len(same_place) == 1:
                before = same_place[0]
            elif before and before["box"] and before["box"] != unit["box"] and \
                    _plain(before["text"]) != _plain(unit["text"]):
                before = None
        if before is None and template_slide:
            # A box the export renamed: compare with the most similar box on the slide.
            scored = [(difflib.SequenceMatcher(None, _plain(u["text"]), _plain(unit["text"])).ratio(), u)
                      for u in template_slide["units"] if _plain(u["text"])]
            best = max(scored, key=lambda x: x[0], default=(0, None))
            before = best[1] if best[0] >= 0.6 else None
        words = added(before["text"] if before else "", unit["text"])
        if not words and before and before["box"] and unit["box"] and _plain(unit["text"]):
            (x0, y0), (x1, y1) = _center(before["box"]), _center(unit["box"])
            if ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 > 0.03 * max(size):
                words = "moved: " + " ".join(_plain(unit["text"]).split())[:40]
        if words:
            out.append((unit, words))
            if not words.startswith("moved: "):
                exact[unit["key"]] = added(before["text"] if before else "", unit["text"], exact=True)
    return out, exact


def _assign(slide_markers, written, template_slide, size):
    """{(category, id): [(unit, words)]} plus uncoded [(unit, words)] and how it was matched."""
    result = {(m["category"], m["id"]): [] for m in slide_markers}
    if not slide_markers:
        return result, list(written), "no code"
    template_units = [u for u in (template_slide or {}).get("units", [])
                      if u["box"] and not MARKER.search(u["text"])
                      and u["box"][2] * u["box"][3] < 0.6 * size[0] * size[1]]
    boxed = [m for m in slide_markers if m["box"]]
    placed = [m for m in boxed if any(
        _inside(_center(m["box"]), u["box"])
        and sum(1 for other in boxed if _inside(_center(other["box"]), u["box"])) == 1
        for u in template_units)]
    uncoded = []
    if boxed and len(placed) * 2 >= len(slide_markers):
        margin = 0.02 * max(size)
        reach = 0.12 * max(size)
        for unit, words in written:
            if MARKER.search(unit["text"]):  # the code sits in this very box
                for m in MARKER.finditer(unit["text"]):
                    result.setdefault((m.group(1), m.group(3).strip()), []).append((unit, words))
                continue
            if not unit["box"]:
                uncoded.append((unit, words))
                continue
            inside = [m for m in boxed if _inside(_center(m["box"]), unit["box"], margin)]
            if inside:
                for m in inside:
                    result[(m["category"], m["id"])].append((unit, words))
                continue
            if words.startswith("moved: "):
                continue  # a dragged card counts only where it lands on a code
            near = min(boxed, key=lambda m: _distance(_center(m["box"]), unit["box"]))
            if _distance(_center(near["box"]), unit["box"]) <= reach or len(slide_markers) == 1:
                # A slide with one question: whatever the student wrote on it answers that
                # question, wherever the answer box sits (often well below the code).
                result[(near["category"], near["id"])].append((unit, words))
            else:
                uncoded.append((unit, words))
        return result, uncoded, "code on the box"

    # Codes listed together rather than placed on their boxes: match by the
    # box labels ("first"/"revised" answer, Concept Bank terms), then by order.
    remaining = [item for item in written if not item[1].startswith("moved: ")]
    for m in [m for m in slide_markers if m["category"] == "BANK"]:
        term = _squash(m["id"])
        for item in list(remaining):
            before = next((u for u in template_units if u["key"] == item[0]["key"]), None)
            if term and _squash((before or item[0])["text"]).startswith(term):
                result[("BANK", m["id"])].append(item)
                remaining.remove(item)
    bank = [m for m in slide_markers if m["category"] == "BANK"]
    if bank and remaining:
        # An answer box without the term in it belongs to the nearest term label.
        labels = {m["id"]: next((u for u in template_units if u["box"] and _squash(m["id"])
                                 and _squash(u["text"]).startswith(_squash(m["id"]))), None) for m in bank}
        placed_labels = [(m, labels[m["id"]]) for m in bank if labels[m["id"]]]
        only_bank = len(bank) == len(slide_markers)
        for item in list(remaining):
            if not item[0]["box"] or not placed_labels:
                continue
            c = _center(item[0]["box"])
            m, label = min(placed_labels, key=lambda ml: _distance(c, ml[1]["box"]))
            if only_bank or _distance(c, label["box"]) <= 0.08 * max(size):
                result[("BANK", m["id"])].append(item)
                remaining.remove(item)
    drafts = [m for m in slide_markers if m["category"] == "DRAFT"]
    notes = [m for m in slide_markers if m["category"] in ("NOTES", "OPTIONAL")]
    ordered = sorted(remaining, key=lambda item: (_center(item[0]["box"])[1], _center(item[0]["box"])[0])
                     if item[0]["box"] else (0, 0))
    if drafts and notes:
        # Use answer labels, including labels that contain their own marker.
        # Directions often mention both first and revised and are not labels.
        labels = [u for u in (template_slide or {}).get("units", []) if u["box"]]
        first = next((u for u in labels if re.match(r"\s*(?:your\s+)?first\s+answer\b", u["text"], re.I)), None)
        revised = next((u for u in labels if re.match(r"\s*(?:your\s+)?revised\s+answer\b", u["text"], re.I)), None)
        for item in ordered:
            if first and revised and item[0]["box"]:
                c = _center(item[0]["box"])
                target = drafts[0] if _distance(c, revised["box"]) < _distance(c, first["box"]) else notes[0]
            else:
                target = notes[0] if ordered.index(item) == 0 else drafts[0]
            result[(target["category"], target["id"])].append(item)
        return result, uncoded, "box labels"
    if len(slide_markers) == 1:
        m = slide_markers[0]
        result[(m["category"], m["id"])].extend(ordered)
        return result, uncoded, "one question on the slide"
    for m, item in zip(notes + drafts, ordered):
        result[(m["category"], m["id"])].append(item)
    return result, uncoded, "order on the slide"


def slide_title(slide):
    """A slide's title: its title box, else the first line of its topmost text."""
    units = [u for u in slide["units"] if _plain(u["text"])]
    title = next((u for u in units if u.get("title")), None)
    if title is None:
        boxed = [u for u in units if u["box"]]
        title = min(boxed, key=lambda u: (u["box"][1], u["box"][0])) if boxed else (units[0] if units else None)
    return " ".join(_plain(title["text"]).split("\n")[0].split())[:150] if title else ""


def coding_issues(deck):
    """Codes on the deck's list that no box carries, and box codes missing from the list."""
    code, required, basis, _ = inventory(deck)
    if basis != "header":
        return {"missing": [], "extra": []}
    present = {(m["category"], m["id"]) for m in markers(deck)}
    listed = {(c, qid) for c in CATEGORIES for qid in required[c]}
    return {"missing": sorted("%s %s" % key for key in listed - present),
            "extra": sorted("%s %s" % key for key in present - listed)}


def readiness(deck, *, revision=""):
    """Completion marker gate; curriculum/teacher-reference checks are separate.

    Read the complete first-slide inventory, including exported line breaks
    and separate text boxes. A marker census is diagnostic, never permission
    to score a missing or inconsistent inventory.
    """
    headers = [(slide, unit, match) for slide in deck["slides"] for unit in slide["units"]
               for match in INVENTORY.finditer(unit["text"])]
    codes = markers(deck)
    observed = Counter(m["category"] for m in codes)
    issues, messages, missing, extra = [], [], [], []

    def issue(ident, message):
        if ident not in issues:
            issues.append(ident); messages.append(message)

    cycle, line, declared = "", "", {}
    if len(headers) != 1:
        issue("marker_inventory_missing_or_duplicated",
              "The deck needs exactly one readable marker inventory on slide 1; found %d." % len(headers))
    else:
        slide, _, match = headers[0]
        cycle = match.group(1)
        if slide["number"] != 1:
            issue("marker_inventory_not_on_first_slide", "The marker inventory is on slide %d, rather than slide 1." % slide["number"])
        units = sorted(slide["units"], key=lambda u: (u["box"][1], u["box"][0]) if u["box"] else (0, 0))
        line = _inventory_text(units)
        fields = COUNT.findall(line)
        declared = {("BANK" if k == "BANK_TERMS" else k): int(v) for k, v in fields}
        if set(declared) != set(CATEGORIES) or len(fields) != len(CATEGORIES):
            issue("marker_inventory_counts_unparsed",
                  "The inventory must give one numeric total each for NOTES, DRAFT, OPTIONAL and BANK_TERMS.")
        else:
            for category in CATEGORIES:
                if declared[category] != observed[category]:
                    issue("marker_inventory_count_mismatch",
                          "%s lists %d in the inventory, but the deck carries %d markers." %
                          (category, declared[category], observed[category]))
        lists = {}
        for category, values in ID_LIST.findall(line):
            parsed = [" ".join(v.split()) for v in re.split(r"[,\n]", values) if v.strip()]
            if parsed == ["-"] and declared.get(category) == 0:
                parsed = []  # explicit empty-list sentinel used in marked Forensics decks
            if category in lists or len(parsed) != len(set(parsed)):
                issue("marker_inventory_id_mismatch", "The inventory repeats a list or question ID.")
            lists[category] = parsed
        for category, listed in lists.items():
            actual = [m["id"] for m in codes if m["category"] == category]
            missing.extend("%s %s" % (category, ident) for ident in set(listed) - set(actual))
            extra.extend("%s %s" % (category, ident) for ident in set(actual) - set(listed))
            if Counter(listed) != Counter(actual):
                issue("marker_inventory_id_mismatch", "The inventory's question IDs do not match the embedded markers.")
    if any(count > 1 for count in Counter((m["category"], m["code"], m["id"]) for m in codes).values()):
        issue("duplicate_feedback_marker", "A response marker is repeated; each category/question ID must appear once.")
    if cycle and any(m["code"] != cycle for m in codes):
        issue("feedback_marker_cycle_mismatch", "A response marker names a different cycle than the inventory.")
    if not {m["id"] for m in codes if m["category"] == "DRAFT"} <= {m["id"] for m in codes if m["category"] == "NOTES"}:
        issue("draft_without_matching_notes", "A DRAFT question has no matching NOTES question ID.")
    return {"schema_version": "hub-completion-readiness/v1", "workflow": "completion",
            "status": "NOT_READY" if issues else "READY", "revision": str(revision or ""),
            "label": NEEDS_CODES if issues else "Ready",
            "inventory_cycle": cycle, "inventory_text": line, "counts": dict(observed),
            "declared_counts": declared, "missing_ids": sorted(missing), "extra_ids": sorted(extra),
            "issues": issues, "warnings": [], "critical_aspects": [],
            "problem": NEEDS_CODES + ". " + messages[0] if messages else None, "diagnostics": messages,
            "action": ("Move the existing code-summary inventory to slide 1, preserving the question markers and lesson content; then recheck and rerun grading.")
                      if issues == ["marker_inventory_not_on_first_slide"] else None,
            "receipt": {"revision": str(revision or "")}, "external_writes": 0}


def score(done, required):
    return None if not required else round(10 * done / required, 1)


def assess(student, template):
    """The completion counts for one student deck against the distributed copy."""
    code, required, basis, note = inventory(template or student)
    if template:
        # A copy of another version of the same lesson carries its own code list:
        # the student answers the boxes in the copy they were handed, so that list counts.
        own = inventory(student)
        if own[2] == "header" and own[0] == code and own[1] != required:
            code, required, basis, note = own
    pairs = _align(student, template) if template else {}
    template_text = {s["number"]: " ".join(t for u in s["units"] for t in _tokens(u["text"]))
                     for s in (template or {"slides": []})["slides"]}
    found = {}
    uncoded, how = [], set()
    codes = markers(student)
    handed_out = markers(template) if template else []
    current = {(c, qid) for c in CATEGORIES for qid in required[c]}
    for slide in student["slides"]:
        mine = [m for m in codes if m["slide"] == slide["number"]]
        match = pairs.get(slide["number"])
        if template and match is None:
            continue  # nothing to compare this slide with
        if template and not any((m["category"], m["id"]) in current for m in mine):
            # A copy of an older version of the deck: this slide lacks today's
            # codes, so use the handed-out deck's codes for its matching slide.
            mine = [m for m in handed_out if m["slide"] == match["number"]]
        if template:
            written, exact = _written(slide, match, student["size"])
        else:
            written = [(u, _plain(u["text"])) for u in slide["units"] if MARKER.search(u["text"]) and _plain(u["text"])]
            exact = {u["key"]: " ".join(words.split()) for u, words in written}
        assigned, loose, basis_here = _assign(mine, written, match, student["size"])
        if mine and written:
            how.add(basis_here)
        for unit, words in loose:
            if not words.startswith("moved: "):  # a dragged card isn't writing
                uncoded.append({"slide": slide["number"], "excerpt": words[:80]})
        printed = template_text.get(match["number"], "") if match else ""
        for key, items in assigned.items():
            words = " ".join(w for _, w in items)
            if not items:
                status = "blank"
            elif len(words.split()) >= COPY_WORDS and " " + words.lower() + " " in " " + printed + " ":
                status = "copied"
            else:
                status = "done"
            prior = found.get(key)
            if not prior or (prior["status"] != "done" and status == "done"):
                answer = " ".join(exact.get(u["key"], w) for u, w in items)
                where = match or slide  # named and ordered by the handed-out deck's slide
                found[key] = {"category": key[0], "id": key[1], "slide": slide["number"], "status": status,
                              "excerpt": words[:80], "answer": answer[:MAX_ANSWER],
                              "at": where["number"], "question": slide_title(where)}
    items = []
    for category in CATEGORIES:
        for qid in required[category]:
            items.append(found.get((category, qid)) or {"category": category, "id": qid, "slide": None,
                                                        "status": "not found", "excerpt": "", "answer": "",
                                                        "at": None, "question": ""})
    # Only codes with a box to answer in count toward the 10 (see the module note).
    counts = {c: {"done": sum(1 for i in items if i["category"] == c and i["status"] == "done"),
                  "required": sum(1 for i in items if i["category"] == c and i["status"] != "not found")}
              for c in CATEGORIES}
    scored_items = [i for i in items if i["category"] != "OPTIONAL"]
    missing_codes = sum(1 for i in scored_items if i["status"] == "not found")
    student_note = None
    if scored_items and missing_codes * 10 >= len(scored_items) * 3:
        student_note = ("This copy is missing %d of its %d codes, so it may have been copied from an older version "
                        "of the deck. Check it by hand." % (missing_codes, len(scored_items)))
    return {"code": code, "inventory": basis, "note": student_note, "inventory_note": note,
            "coding": coding_issues(template or student),
            "notes_score": score(counts["NOTES"]["done"] + counts["BANK"]["done"],
                                 counts["NOTES"]["required"] + counts["BANK"]["required"]),
            "revision_score": score(counts["DRAFT"]["done"], counts["DRAFT"]["required"]),
            "counts": counts, "items": items, "uncoded": uncoded[:40], "matched_by": sorted(how),
            "compared": bool(template)}


def coverage(student, template):
    """The share of the student's codes that the template also carries."""
    mine = {k for s in student["slides"] for k in _signature(s)}
    if not mine or template is None:
        return 0.0
    return len(mine & {k for s in template["slides"] for k in _signature(s)}) / len(mine)


def pick_template(student, templates):
    """The distributed copy with the same lesson code and the most codes in common."""
    code = inventory(student)[0]
    best, best_share = None, 0.0
    for template in templates:
        if code and inventory(template)[0] != code:
            continue
        share = coverage(student, template)
        if share > best_share:
            best, best_share = template, share
    return best


# ---------- reading the decks from Drive ----------

SLIDES = "application/vnd.google-apps.presentation"
_templates, _results = {}, {}
MAX_CACHED_TEMPLATES, MAX_CACHED_RESULTS = 24, 600


def _remember(cache, key, value, limit):
    if key not in cache and len(cache) >= limit:
        cache.pop(next(iter(cache)))
    cache[key] = value
    return value


def _read(token, file_id, meta):
    from . import google
    if meta.get("trashed"):
        raise Unreadable("This exact deck is in trash; no contents were scored or restored.")
    return read_deck(google.deck_bytes(token, file_id, meta.get("mimeType", "")))


def _template(token, deck_id):
    """(metadata, parsed deck) for a handed-out deck, re-read only when it changes."""
    from . import google
    meta = google.drive_file(token, deck_id, google.FILE_FIELDS + ",version")
    if meta.get("trashed"):
        raise Unreadable("This linked deck is in trash. It is not a current lesson source; it was not restored.")
    key = (deck_id, meta.get("modifiedTime", ""), str(meta.get("version") or ""))
    if key not in _templates:
        _remember(_templates, key, _read(token, deck_id, meta), MAX_CACHED_TEMPLATES)
    return meta, _templates[key]


_folders = {}
FOLDER_CACHE_SECONDS = 600


def _siblings(token, folder_id):
    """Supported decks in a lesson folder, re-listed at most every ten minutes."""
    import time
    from . import google
    cached = _folders.get(folder_id)
    if cached and time.time() - cached[0] < FOLDER_CACHE_SECONDS:
        return cached[1]
    try:
        decks = [f for f in google.folder_children(token, folder_id) if f.get("mimeType") in (SLIDES, google.PPTX)][:60]
    except google.GoogleError:
        decks = []
    _remember(_folders, folder_id, (time.time(), decks), 50)
    return decks


def original_title(name):
    """The deck a student's copy was made from: 's.x — 09-16 12.41 — Copy of Deck - Ana Rivera.pptx' → 'Deck'."""
    text = re.sub(r"^\s*s\.[a-z0-9.\-]+\s+[—-]\s+\d{2}-\d{2}\s+\d{2}\.\d{2}\s+[—-]\s*", "", str(name or ""), flags=re.I)
    text = re.sub(r"\.(pptx?|key)$", "", text.strip(), flags=re.I)
    if " - " in text:
        text = text.rsplit(" - ", 1)[0]
    text = re.sub(r"^(?:\s*copy of\s+)+", "", text, flags=re.I).strip()
    return text if len(text) >= 6 else ""


def lesson_decks(agenda, class_key, week_label, form_id):
    """The Slides linked in the Agenda row of the lesson that holds this Form."""
    for lesson in agenda.lessons(class_key, week_label):
        if any(f["form_id"] == form_id for f in lesson["forms"]):
            ids = []
            for material in lesson["materials"]:
                match = re.search(r"/presentation/d/([A-Za-z0-9_-]{20,})", material["url"])
                if match and match.group(1) not in ids:
                    ids.append(match.group(1))
            return ids
    return []


def lesson_sources(token, lesson):
    """Fresh actual-folder/current-link evidence, never a lesson-name search.

    Stale/trash links are reported separately. An inaccessible or empty named
    folder is a hold; a listing alone cannot prove its folder was readable.
    """
    from . import google
    materials = lesson.get("materials", [])
    folders = list(dict.fromkeys(m.group(1) for item in materials
                 if item.get("label") == "Lesson folder"
                 if (m := re.search(r"/folders/([A-Za-z0-9_-]+)", item.get("url", "")))))
    linked = {}
    for item in materials:
        match = re.search(r"/presentation/d/([A-Za-z0-9_-]{20,})", item.get("url", ""))
        if match:
            linked.setdefault(match.group(1), item.get("label", "Deck"))
    result = {"decks": [], "stale_links": [], "folders": [], "problem": None}
    members = set()
    for fid in folders:
        try:
            meta = google.drive_file(token, fid)
            if meta.get("mimeType") != google.FOLDER or meta.get("trashed"):
                raise Unreadable("The Agenda lesson folder is unavailable or in trash.")
            children = google.folder_children(token, fid)
            present = {f["id"] for f in children if f.get("mimeType") in (SLIDES, google.PPTX) and not f.get("trashed")}
            if not present:
                raise Unreadable("The readable Agenda lesson folder contains no current slide deck.")
            members.update(present)
            result["folders"].append({"id": fid, "name": meta.get("name", ""), "deck_ids": sorted(present)})
        except (google.GoogleError, Unreadable) as error:
            result["problem"] = "Cannot verify the actual Agenda lesson folder: %s" % error
            return result
    for fid, label in linked.items():
        try:
            meta = google.drive_file(token, fid)
            if meta.get("trashed") or (folders and fid not in members):
                result["stale_links"].append({"id": fid, "label": label, "reason": "in trash" if meta.get("trashed") else "outside the current lesson folder"})
                continue
            if meta.get("mimeType") not in (SLIDES, google.PPTX):
                raise Unreadable("The exact linked source is not a supported slide deck.")
            result["decks"].append({"id": fid, "label": label, "title": meta.get("name", ""), "metadata": meta})
        except (google.GoogleError, Unreadable) as error:
            result["problem"] = "Cannot read the exact %s link: %s" % (label, error)
            return result
    if not result["decks"]:
        result["problem"] = "No current deck is linked inside the verified lesson folder." if folders else "No current handed-out deck is linked for this lesson."
    return result


def assignment_readiness(token, config, agenda, class_key, week_label, form_id):
    """Fresh linked-source coding and persisted teacher decision, no scoring.

    All assignment entry paths must call this before creating absence zeros.
    Submitted copies still need their own gate in score_file.
    """
    from . import google, completion_holds
    lessons = [lesson for lesson in agenda.lessons(class_key, week_label)
               if any(f.get("form_id") == form_id for f in lesson["forms"])]
    if len(lessons) != 1:
        return {"status": "NOT_READY", "code": "deck_not_ready", "label": "Needs check", "problem": "The exact assignment's Agenda lesson is not confirmed.",
                "action": "Confirm this Form's Agenda lesson before rerunning grading."}
    lesson = lessons[0]
    context = "%s · %s · %s" % (agenda.classes[class_key]["name"], week_label, lesson["topic"])
    hold = completion_holds.check(config, agenda, class_key, week_label, form_id)
    if hold["held"]:
        return {**hold, "context": context, "problem": context + ": " + hold["problem"],
                "student_copies_checked": False, "scope": "assignment"}
    sources = lesson_sources(token, lesson)
    if sources["problem"]:
        return {"status": "NOT_READY", "code": "deck_not_ready", "label": "Cannot read lesson", "context": context,
                "source_resolution": sources, "problem": context + ": " + sources["problem"],
                "action": "Check this exact Agenda lesson folder and current deck links, then recheck; no title-based replacement was selected."}
    ids = [item["id"] for item in sources["decks"]]
    results = []
    for deck_id in ids:
        try:
            meta, deck = _template(token, deck_id)
            checked = readiness(deck, revision=meta.get("version") or meta.get("modifiedTime", ""))
            checked.update(deck_id=deck_id, title=meta.get("name", ""))
        except (google.GoogleError, Unreadable) as error:
            checked = {"status": "NOT_READY", "deck_id": deck_id, "code": "deck_not_ready", "label": "Cannot read lesson", "problem": str(error)}
        results.append(checked)
    failed = next((r for r in results if r["status"] != "READY"), None)
    return {"status": "NOT_READY" if failed else "READY", "code": "deck_not_ready" if failed else None,
            "label": failed.get("label", "Needs check") if failed else "Ready",
            "scope": "assignment", "context": context, "student_copies_checked": False, "linked_decks": results,
            "source_resolution": sources,
            "problem": context + ": " + failed["problem"] if failed else None,
            "action": failed.get("action") if failed and failed.get("action") else
                      ("Check this exact linked lesson's access or readability, then recheck before rerunning grading.")
                      if failed and failed.get("label") == "Cannot read lesson" else
                      ("Repair or embed this lesson's completion codes, preserving content and student answers; "
                       "then recheck and rerun grading. No scores or missing-work zeros may be entered while it is held.") if failed else
                      "Linked completion codes are ready; every submitted student copy must also pass before entry."}


def submitted_readiness(token, form):
    """Fresh copy revisions/codes only, without completion arithmetic or writes."""
    from . import google
    checked, seen = [], set()
    for row in form.get("students", []) + form.get("late", []):
        if not row.get("matched"):
            continue
        for upload in row.get("files", []):
            if upload["id"] in seen:
                continue
            seen.add(upload["id"])
            try:
                meta, deck = _template(token, upload["id"])
                result = readiness(deck, revision=meta.get("version") or meta.get("modifiedTime", ""))
            except (google.GoogleError, Unreadable) as error:
                result = {"status": "NOT_READY", "label": "Cannot read lesson", "problem": str(error)}
            result.update(file_id=upload["id"], name=upload.get("name", ""))
            checked.append(result)
            if result["status"] != "READY":
                return {"status": "NOT_READY", "code": "deck_not_ready", "copies": checked,
                        "label": result.get("label", "Needs check"),
                        "problem": "Submitted copy “%s”: %s" % (result["name"], result["problem"]),
                        "action": "Check access to this exact submitted file, then recheck before rerunning grading." if result.get("label") == "Cannot read lesson" else
                                  "Repair or embed codes in this existing copy, preserving answers; then rerun grading. No assignment scores or missing-work zeros may be entered."}
    return {"status": "READY", "label": "Ready", "copies": checked, "student_copies_checked": len(checked)}


def score_file(token, agenda, class_key, week_label, form_id, file_id, work_result):
    """Scores for one student's current, on-time file in one Form's list."""
    from . import google
    form = next((f for lesson in work_result.get("lessons", []) for f in lesson["forms"]
                 if f["form_id"] == form_id), None)
    row = next((r for r in (form or {}).get("students", []) if any(f["id"] == file_id for f in r["files"])), None)
    if not form or not row:
        raise ValueError("file")
    upload = next(f for f in row["files"] if f["id"] == file_id)
    out = {"file_id": file_id, "problem": None}

    def not_ready(check, name, kind):
        lesson = next((l for l in work_result.get("lessons", []) if form in l["forms"]), {})
        class_name = agenda.classes.get(class_key, {}).get("name", class_key)
        context = "%s · %s · %s" % (class_name, week_label, lesson.get("topic") or form.get("label") or "this assignment")
        action = check.get("action") or ("Run the embed-coding program on this existing %s, preserving its answers and lesson content; "
                  "then rerun grading. No scores were calculated." % kind)
        return {**out, "code": "deck_not_ready", "label": check["label"], "readiness": check, "action": action,
                "problem": "%s: %s “%s”. %s %s" %
                           (context, kind, name, check["problem"], action)}
    if work_result.get("assessment_problem"):
        return {**out, "code": "coverage_incomplete", "problem": work_result["assessment_problem"]}
    policy = work_result.get("week_policy") or {}
    if policy.get("problem"):
        return {**out, "code": "coverage_incomplete", "problem": policy["problem"]}
    if policy.get("excluded"):
        return {**out, "code": "excluded_week", "problem": policy.get("reason") or
                "The teacher excluded this Agenda week from grading for this school year. Its work remains filed."}
    # Manual score requests, feedback collection and morning scoring all use
    # this entry point. Do not read or assess decks while the same lesson-work
    # scan still contains submissions without a confirmed assignment match.
    # Existing score/template caches remain intact for a later resolved scan.
    if work_result.get("other_files"):
        return {**out, "code": "coverage_incomplete", "problem":
                "Scoring is on hold until every submission in this week's turn-in folders "
                "has a confirmed lesson match. Open Student work and resolve "
                "‘Confirm these submission matches’."}
    if upload.get("other_week"):
        return {**out, "code": "source_week_mismatch", "problem":
                "This submission names Week %s, outside this assignment's Agenda and teacher-deck weeks. "
                "Confirm its lesson before scoring or entering a grade." % upload["other_week"]}
    assignment_check = form.get("grade_readiness")
    if assignment_check and assignment_check.get("status") != "READY":
        return {**out, "code": "deck_not_ready", "label": assignment_check.get("label", "Needs check"), "problem": assignment_check.get("problem") or
                "This assignment's completion readiness is held.", "readiness": assignment_check,
                "action": assignment_check.get("action")}
    if upload.get("mime") not in (SLIDES, google.PPTX):
        return {**out, "problem": "Only Google Slides or PowerPoint decks can be scored. Open it yourself."}
    templates = []
    eligible = (assignment_check or {}).get("source_resolution")
    deck_ids = [d["id"] for d in eligible["decks"]] if eligible else lesson_decks(agenda, class_key, week_label, form_id)
    for deck_id in deck_ids:
        try:
            templates.append((deck_id,) + _template(token, deck_id))
        except (google.GoogleError, Unreadable):
            continue
    try:
        meta = google.drive_file(token, file_id, google.FILE_FIELDS + ",version")
        out["file_updated"] = meta.get("modifiedTime", "")
        deck = _read(token, file_id, meta)
    except (google.GoogleError, Unreadable) as error:
        return {**out, "code": "deck_not_ready", "label": "Cannot read lesson",
                "problem": "Couldn't read this exact submitted deck (%s)." % error,
                "action": "Check this file's access or readability, then recheck before rerunning grading."}
    check = readiness(deck, revision=meta.get("version") or meta.get("modifiedTime", ""))
    if check["status"] != "READY":
        return not_ready(check, upload.get("name") or meta.get("name", ""), "student copy")
    best = pick_template(deck, [t[2] for t in templates])
    if coverage(deck, best) < 0.8:
        # Look only in the actual linked lesson/source folders. A same-title
        # file elsewhere, including an obsolete lesson, is not a replacement.
        title = original_title(upload.get("name") or meta.get("name", ""))
        found = []
        if title:
            parents = {p for t in list(templates) for p in (t[1].get("parents") or [])[:1]}
            parents.update(f["id"] for f in (eligible or {}).get("folders", []))
            for parent in parents:
                found += [f for f in _siblings(token, parent)
                          if re.sub(r"\.pptx$", "", f["name"].strip(), flags=re.I) == title]
        for item in found:
            if item["id"] != file_id and not any(t[0] == item["id"] for t in templates):
                try:
                    templates.append((item["id"],) + _template(token, item["id"]))
                except (google.GoogleError, Unreadable):
                    continue
        best = pick_template(deck, [t[2] for t in templates])
    chosen = next((t for t in templates if t[2] is best), None)
    if chosen is None:
        return {**out, "problem": "No handed-out copy of this deck is linked in the lesson's Agenda row "
                                  "(Student copy or Teacher deck), so its boxes can't be checked."}
    check = readiness(chosen[2], revision=chosen[1].get("version") or chosen[1].get("modifiedTime", ""))
    if check["status"] != "READY":
        return not_ready(check, chosen[1].get("name", ""), "handed-out deck")
    source_key = (file_id, meta.get("modifiedTime", ""), str(meta.get("version") or ""))
    template_key = (chosen[0], chosen[1].get("modifiedTime", ""), str(chosen[1].get("version") or ""))
    cached = _results.get(source_key)
    if cached and cached["template"] == template_key:
        return {**out, **cached["result"]}
    result = {**assess(deck, chosen[2]), "template": chosen[1].get("name", ""),
              "template_id": chosen[0], "template_updated": chosen[1].get("modifiedTime", "")}
    _remember(_results, source_key, {"template": template_key, "result": result}, MAX_CACHED_RESULTS)
    return {**out, **result}
