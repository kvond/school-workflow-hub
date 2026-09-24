"""Completion scores for one student's deck: Notes and Revision, out of 10.

This is the teacher's completion rule (Prompt 1) as counting, for her to
review — it never judges how good an answer is:

    Notes    = 10 x (completed NOTES + completed BANK) / (required NOTES + BANK)
    Revision = 10 x completed DRAFT / required DRAFT

The required items are the codes in the deck's own [[MARKER-INVENTORY:...]]
header (or, without one, every code in the deck). Each code is a small text
box such as [[NOTES:C03:CA1-RULE]] placed on its answer box. A box counts as
completed when the student wrote something in it that wasn't already in the
copy they were given; a box whose new text is the printed slide text copied
back is flagged, not credited. OPTIONAL work is listed, not scored. A zero
denominator is N/A. Writing in a box with no code is ignored for the scores
and reported by slide, so the deck can be coded next time.
"""
import difflib
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
            units.append({"key": prefix + _name(child, P + "nvSpPr"), "text": _text(child.find(P + "txBody")),
                          "box": _box(child.find(P + "spPr/" + A + "xfrm"), transform)})
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
    for category, values in ID_LIST.findall(header):
        lists[category] = [" ".join(v.split()) for v in re.split(r"[,\n]", values) if v.strip()]
    counts = {("BANK" if k == "BANK_TERMS" else k): int(v) for k, v in COUNT.findall(header)}
    if any(lists.values()) and all(counts.get(c, len(lists[c])) == len(lists[c]) for c in CATEGORIES):
        return lists
    return None


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
        lists = _parse_header("\n".join(u["text"] for u in units if u["text"]), census)
        if lists:
            return code, lists, "header", None
    return code, census, "census", "The deck's code list doesn't match its own counts, so every code in it was used."


# ---------- comparing a student's deck with the copy they were given ----------

def _plain(text):
    return MARKER.sub(" ", INVENTORY.sub(" ", text or "")).strip()


def _tokens(text):
    return re.findall(r"\w+|[^\w\s]", _plain(text).lower())


def added(before, after):
    """What the student typed into a box: the stretch of the box from the
    first to the last word that wasn't in the copy they were given ('' when
    nothing is new)."""
    old, new = _tokens(before), _tokens(after)
    if old == new:
        return ""
    spans = [(j1, j2) for tag, _, _, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False)
             .get_opcodes() if tag in ("insert", "replace")]
    words = [w for j1, j2 in spans for w in new[j1:j2]]
    if not words and new and len(old) <= 6:
        return "chose " + " ".join(new)  # e.g. kept one of "↑ ↓ ?"
    if not any(re.search(r"\w", w) for w in words):
        return ""
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


def _align(student, template):
    """{student slide number: template slide} by the codes each slide carries,
    then by position for slides without codes when the decks are the same length."""
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
    return pairs


def _written(student_slide, template_slide, size=(12192000, 6858000)):
    """[(unit, added words)] for boxes the student wrote in on this slide. A
    card dragged to a new place counts as 'moved: …' (credited only where it
    lands on a code)."""
    base = {u["key"]: u for u in template_slide["units"]} if template_slide else {}
    out = []
    for unit in student_slide["units"]:
        before = base.get(unit["key"])
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
    return out


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
            if _distance(_center(near["box"]), unit["box"]) <= reach:
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
        first = next((u for u in template_units if re.search(r"\bfirst\b", u["text"], re.I)), None)
        revised = next((u for u in template_units if re.search(r"\brevis", u["text"], re.I)), None)
        for item in ordered:
            if first and revised and item[0]["box"]:
                c = _center(item[0]["box"])
                target = drafts[0] if _distance(c, revised["box"]) < _distance(c, first["box"]) else notes[0]
            else:
                target = notes[0] if ordered.index(item) == 0 else drafts[0]
            result[(target["category"], target["id"])].append(item)
        return result, uncoded, "box labels"
    for m, item in zip(notes + drafts, ordered):
        result[(m["category"], m["id"])].append(item)
    return result, uncoded, "order on the slide"


def coding_issues(deck):
    """Codes on the deck's list that no box carries, and box codes missing from the list."""
    code, required, basis, _ = inventory(deck)
    if basis != "header":
        return {"missing": [], "extra": []}
    present = {(m["category"], m["id"]) for m in markers(deck)}
    listed = {(c, qid) for c in CATEGORIES for qid in required[c]}
    return {"missing": sorted("%s %s" % key for key in listed - present),
            "extra": sorted("%s %s" % key for key in present - listed)}


def score(done, required):
    return None if not required else round(10 * done / required, 1)


def assess(student, template):
    """The completion counts for one student deck against the distributed copy."""
    code, required, basis, note = inventory(template or student)
    pairs = _align(student, template) if template else {}
    template_text = {s["number"]: " ".join(t for u in s["units"] for t in _tokens(u["text"]))
                     for s in (template or {"slides": []})["slides"]}
    found = {}
    uncoded, how = [], set()
    codes = markers(student)
    for slide in student["slides"]:
        mine = [m for m in codes if m["slide"] == slide["number"]]
        match = pairs.get(slide["number"])
        if template and match is None:
            continue  # nothing to compare this slide with
        written = _written(slide, match, student["size"]) if template else [
            (u, _plain(u["text"])) for u in slide["units"] if MARKER.search(u["text"]) and _plain(u["text"])]
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
                found[key] = {"category": key[0], "id": key[1], "slide": slide["number"], "status": status,
                              "excerpt": words[:80]}
    items = []
    for category in CATEGORIES:
        for qid in required[category]:
            items.append(found.get((category, qid)) or {"category": category, "id": qid, "slide": None,
                                                        "status": "not found", "excerpt": ""})
    counts = {c: {"done": sum(1 for i in items if i["category"] == c and i["status"] == "done"),
                  "required": len(required[c])} for c in CATEGORIES}
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
    return read_deck(google.deck_bytes(token, file_id, meta.get("mimeType", "")))


def _template(token, deck_id):
    """(metadata, parsed deck) for a handed-out deck, re-read only when it changes."""
    from . import google
    meta = google.drive_file(token, deck_id)
    key = (deck_id, meta.get("modifiedTime", ""))
    if key not in _templates:
        _remember(_templates, key, _read(token, deck_id, meta), MAX_CACHED_TEMPLATES)
    return meta, _templates[key]


_folders = {}
FOLDER_CACHE_SECONDS = 600


def _siblings(token, folder_id):
    """The Slides decks in a lesson folder, re-listed at most every ten minutes."""
    import time
    from . import google
    cached = _folders.get(folder_id)
    if cached and time.time() - cached[0] < FOLDER_CACHE_SECONDS:
        return cached[1]
    try:
        decks = [f for f in google.folder_children(token, folder_id) if f.get("mimeType") == SLIDES][:60]
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
    if upload.get("mime") not in (SLIDES, google.PPTX):
        return {**out, "problem": "Only Google Slides or PowerPoint decks can be scored. Open it yourself."}
    templates = []
    for deck_id in lesson_decks(agenda, class_key, week_label, form_id):
        try:
            templates.append((deck_id,) + _template(token, deck_id))
        except (google.GoogleError, Unreadable):
            continue
    try:
        meta = google.drive_file(token, file_id)
        out["file_updated"] = meta.get("modifiedTime", "")
        cached = _results.get((file_id, meta.get("modifiedTime", "")))
        if cached and any((t[0], t[1].get("modifiedTime", "")) == cached["template"] for t in templates):
            return {**out, **cached["result"]}
        deck = _read(token, file_id, meta)
    except (google.GoogleError, Unreadable) as error:
        return {**out, "problem": "Couldn't read this deck (%s). Open it yourself." % error}
    best = pick_template(deck, [t[2] for t in templates])
    if coverage(deck, best) < 0.8:
        # The Agenda row links another assignment's deck: look for the
        # original this copy was made from, by its name — anywhere the
        # dashboard can search, then in the folder beside each linked deck
        # (a deck shared by link can be opened but not searched for).
        title = original_title(upload.get("name") or meta.get("name", ""))
        found = []
        if title:
            try:
                found += google.files_named(token, title, SLIDES)[:5]
            except google.GoogleError:
                pass
            for parent in {p for t in list(templates) for p in (t[1].get("parents") or [])[:1]}:
                found += [f for f in _siblings(token, parent) if f["name"].strip() == title]
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
    result = {**assess(deck, chosen[2]), "template": chosen[1].get("name", "")}
    _remember(_results, (file_id, meta.get("modifiedTime", "")),
              {"template": (chosen[0], chosen[1].get("modifiedTime", "")), "result": result}, MAX_CACHED_RESULTS)
    return {**out, **result}
