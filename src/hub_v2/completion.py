"""Completion scores for one student's deck: Notes and Revision, out of 10.

This is the teacher's completion rule (Prompt 1) as counting, for her to
review — it never judges how good an answer is:

    Notes    = 10 x (completed NOTES + completed BANK) / (required NOTES + BANK)
    Revision = 10 x completed DRAFT / required DRAFT

Student grading counts the actual parseable codes with answerable boxes in the
submitted copy. A missing or stale student inventory does not add requirements
or hold a grade. The registered Biology Photosynthesis lesson still requires
complete coding. Exact linked teacher sources must remain readable and
unambiguous; their stale inventory is advisory outside that exception.
A code with no answerable box isn't counted, so a deck with only 3 answerable
boxes is out of those 3 (the teacher's rule). Each code is a small text
box such as [[NOTES:C03:CA1-RULE]] placed on its answer box. A box counts as
completed when the student wrote something in it that wasn't already in the
copy they were given; a box whose new text is the printed slide text copied
back is flagged, not credited. OPTIONAL work is listed, not scored. A zero
denominator is N/A. Writing in a box with no code is ignored for the scores
and reported by slide, so the deck can be coded next time.
"""
import difflib
from collections import Counter
import hashlib
import io
import json
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET

from . import graphical_choice, response_binding, workspace_binding, writing_fields

P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
CATEGORIES = ("NOTES", "BANK", "DRAFT", "OPTIONAL")
# BANK ids are printed term labels, including "sensor / control center / effector".
# Only BANK may use these spaced separators. Keep the three capture groups,
# response/cycle id grammar and length bound used by matching and assessment.
MARKER = re.compile(
    r"\[\[(?=BANK:|(?:NOTES|DRAFT|OPTIONAL):[A-Za-z0-9_-]{1,40}:[A-Za-z0-9 _-]{1,80}\]\])"
    r"(NOTES|BANK|DRAFT|OPTIONAL):([A-Za-z0-9_-]{1,40}):"
    r"(?=[A-Za-z0-9 _/-]{1,80}\]\])"
    r"([A-Za-z0-9 _-]{1,80}|[A-Za-z0-9][A-Za-z0-9 _-]*(?: / [A-Za-z0-9][A-Za-z0-9 _-]*)+)\]\]")
INVENTORY = re.compile(r"\[\[MARKER-INVENTORY:([A-Za-z0-9_-]{1,40})\]\]")
ID_LIST = re.compile(r"\b(NOTES|DRAFT|OPTIONAL|BANK)_IDS=(.*?)(?=\s+[A-Z][A-Z_]*=|\s*$)", re.S)
COUNT = re.compile(r"\b(NOTES|DRAFT|OPTIONAL|BANK_TERMS)=(\d+)\b")
MAX_SLIDE_BYTES = 5_000_000
MAX_SLIDES = 200
MAX_PICTURE_BYTES = 20_000_000
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


def _highlight_shape(node, orientation):
    """Exact original paint/geometry, allowing only a shape's translation."""
    properties = node.find(P + "spPr")
    if properties is None:
        return None
    properties = ET.fromstring(ET.tostring(properties))
    off = properties.find(A + "xfrm/" + A + "off")
    if off is not None:
        off.attrib.clear()
    visibility = node.find(P + "nvSpPr/" + P + "cNvPr")
    style = node.find(P + "style")
    def canonical(element):
        return (element.tag, tuple(sorted(element.attrib.items())), element.text or "",
                tuple(canonical(child) for child in element)) if element is not None else None
    raw = repr((canonical(properties), canonical(style),
                tuple(tuple(sorted(value.items())) for value in orientation),
                visibility.get("hidden", "") if visibility is not None else "")).encode()
    return {"sha256": hashlib.sha256(raw).hexdigest()}


def _unsupported_panel_geometry(xfrm, properties, body=None, *, group=False):
    """The panel contract uses axis-aligned visible text only, not guessed bounds.

    Retain a refusal signal for attributes the ordinary rectangle reader does
    not compose. This adds no rotation/flip/visibility support to other readers.
    """
    if xfrm is None or any(xfrm.find(A + name) is None for name in ('off', 'ext')):
        return True
    if group and any(xfrm.find(A + name) is None for name in ('chOff', 'chExt')):
        return True
    try:
        if any(int(xfrm.find(A + name).get(axis, '0')) <= 0
               for name in (('ext', 'chExt') if group else ('ext',)) for axis in ('cx', 'cy')):
            return True
    except ValueError:
        return True
    def nonzero(value):
        try:
            return int(value or '0') != 0
        except ValueError:
            return True
    def active(value):
        return value is not None and value.strip().lower() not in ('0', 'false')
    if (nonzero(xfrm.get('rot')) or any(active(xfrm.get(k)) for k in ('flipH', 'flipV'))
            or (properties is not None and active(properties.get('hidden')))):
        return True
    return body is not None and (nonzero(body.get('rot')) or body.get('vert', 'horz') != 'horz')


def _walk(node, units, transform, prefix="", pictures=None, picture_orientation=(), highlighters=False,
          panel_geometry_unverified=False):
    # The root slide tree is also a group and may carry its own hidden flag.
    group_props = node.find(P + 'nvGrpSpPr/' + P + 'cNvPr')
    if group_props is not None and group_props.get('hidden', '0').strip().lower() not in ('0', 'false'):
        panel_geometry_unverified = True
    for child in node:
        if child.tag == P + "sp":
            ph = child.find(P + "nvSpPr/" + P + "nvPr/" + P + "ph")
            unit = {"key": prefix + _name(child, P + "nvSpPr"), "text": _text(child.find(P + "txBody")),
                          "box": _box(child.find(P + "spPr/" + A + "xfrm"), transform),
                          "title": ph is not None and ph.get("type") in ("title", "ctrTitle")}
            properties = child.find(P + "nvSpPr/" + P + "cNvPr")
            if panel_geometry_unverified or _unsupported_panel_geometry(child.find(P + 'spPr/' + A + 'xfrm'),
                    properties, child.find(P + 'txBody/' + A + 'bodyPr')):
                unit['panel_geometry_unverified'] = True
            binding = response_binding.description(properties.get("descr") if properties is not None else None)
            if binding is not None:
                unit["binding"] = binding
            if highlighters:
                unit["highlight_shape"] = _highlight_shape(child, picture_orientation)
            units.append(unit)
        elif child.tag == P + "pic":
            blip = child.find(P + "blipFill/" + A + "blip")
            crop = child.find(P + "blipFill/" + A + "srcRect")
            xfrm = child.find(P + "spPr/" + A + "xfrm")
            appearance = {"crop": dict(crop.attrib) if crop is not None else {},
                          "transform": {k: xfrm.get(k) for k in ("rot", "flipH", "flipV") if xfrm is not None and xfrm.get(k)},
                          "groups": list(picture_orientation),
                          "effects": [ET.tostring(e, encoding="unicode") for e in blip if e.tag != A + "extLst"] if blip is not None else []}
            units.append({"key": prefix + _name(child, P + "nvPicPr"), "text": "", "title": False,
                          "box": _box(xfrm, transform), "picture": {
                              "sha256": (pictures or {}).get(blip.get(R + "embed")) if blip is not None else None,
                              "appearance": appearance}})
        elif child.tag == P + "grpSp":
            xfrm = child.find(P + "grpSpPr/" + A + "xfrm")
            group_properties = child.find(P + 'nvGrpSpPr/' + P + 'cNvPr')
            unsupported = panel_geometry_unverified or _unsupported_panel_geometry(xfrm, group_properties, group=True)
            orientation = {k: xfrm.get(k) for k in ("rot", "flipH", "flipV") if xfrm is not None and xfrm.get(k)}
            if highlighters:
                properties = child.find(P + "nvGrpSpPr/" + P + "cNvPr")
                if properties is not None and properties.get("hidden", "").lower() in ("1", "true"):
                    orientation["hidden"] = "1"
                paint = child.find(P + "grpSpPr")
                effects = tuple(ET.tostring(e, encoding="unicode") for e in paint if e.tag != A + "xfrm") if paint is not None else ()
                if effects:
                    orientation["group_effects"] = effects
            _walk(child, units, _affine(xfrm, transform, group=True),
                  prefix + _name(child, P + "nvGrpSpPr") + "/", pictures,
                  picture_orientation + ((orientation,) if orientation else ()), highlighters, unsupported)
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
                                  "box": (x, y, width, height),
                                  # Cell/graphic-frame rendering is not an existing
                                  # fixed text-shape proof for the panel contract.
                                  "panel_geometry_unverified": True})
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
        workspace_trees = {}
        media_hashes = {}
        for number, name in enumerate(_slide_names(archive)[:MAX_SLIDES], 1):
            if archive.getinfo(name).file_size > MAX_SLIDE_BYTES:
                raise Unreadable("One of this deck's slides is too large to read.")
            try:
                root = ET.fromstring(archive.read(name))
            except ET.ParseError:
                raise Unreadable("This deck couldn't be read.") from None
            units = []
            pictures = {}
            relname = posixpath.dirname(name) + "/_rels/" + posixpath.basename(name) + ".rels"
            if relname in archive.namelist():
                try:
                    relationships = ET.fromstring(archive.read(relname))
                except ET.ParseError:
                    relationships = []
                for relationship in relationships:
                    if relationship.get("TargetMode") == "External" or not relationship.get("Type", "").endswith("/image"):
                        continue
                    target = posixpath.normpath(posixpath.join(posixpath.dirname(name), relationship.get("Target", "")))
                    if not target.startswith("ppt/media/") or target not in archive.namelist() or archive.getinfo(target).file_size > MAX_PICTURE_BYTES:
                        continue
                    if target not in media_hashes:
                        with archive.open(target) as media:
                            digest = hashlib.sha256()
                            for block in iter(lambda: media.read(262144), b""):
                                digest.update(block)
                        media_hashes[target] = digest.hexdigest()
                    pictures[relationship.get("Id")] = media_hashes[target]
            tree = root.find(P + "cSld/" + P + "spTree")
            if tree is not None:
                workspace_trees[number] = (tree, root, pictures)
                explicit_highlights = False
                for properties in tree.iter(P + "cNvPr"):
                    binding = response_binding.description(properties.get("descr", "") or "")
                    if binding and binding.get("kind") == "raster-highlight-activity":
                        explicit_highlights = True
                        break
                _walk(tree, units, (1, 1, 0, 0), pictures=pictures, highlighters=explicit_highlights,
                      panel_geometry_unverified=root.get('show', '1').strip().lower() not in ('1', 'true'))
            seen = {}
            for unit in units:  # keep keys unique on a slide
                seen[unit["key"]] = seen.get(unit["key"], 0) + 1
                if seen[unit["key"]] > 1:
                    unit["key"] += "|%d" % seen[unit["key"]]
            slide = {"number": number, "units": units}
            if tree is not None and any(u.get("binding", {}).get("kind") == writing_fields.KIND for u in units):
                writing_fields.attach(slide, tree, root, pictures, archive, _box, _name, part=name)
            if tree is not None and any(u.get("binding", {}).get("kind") == graphical_choice.KIND for u in units):
                slide["choice_shapes"] = graphical_choice.read_shapes(tree, box=_box, affine=_affine, text=_text, name=_name)
            # Raster regions are evidence only for a reviewed explicit activity,
            # never additional text or pictures in ordinary submission captures.
            if any(u.get("binding", {}).get("kind") in ("raster-card-activity", "raster-highlight-activity") for u in units):
                fill = root.find(P + "cSld/" + P + "bg/" + P + "bgPr/" + A + "blipFill")
                if fill is not None:
                    blip, crop = fill.find(A + "blip"), fill.find(A + "srcRect")
                    stretch, tile = fill.find(A + "stretch/" + A + "fillRect"), fill.find(A + "tile")
                    slide["background"] = {
                        "sha256": pictures.get(blip.get(R + "embed")) if blip is not None else None,
                        "appearance": {"crop": dict(crop.attrib) if crop is not None else {},
                            "stretch": dict(stretch.attrib) if stretch is not None else None,
                            "tile": dict(tile.attrib) if tile is not None else None,
                            "effects": [ET.tostring(e, encoding="unicode") for e in blip
                                        if e.tag != A + "extLst"] if blip is not None else []}}
            slides.append(slide)
        workspace_binding.attach(slides, workspace_trees, archive, box=_box, affine=_affine, name=_name)
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
                marker = {"category": match.group(1), "code": match.group(2), "id": match.group(3).strip(),
                          "slide": slide["number"], "key": unit["key"], "box": unit["box"],
                          "standalone": unit["text"].strip() == match.group(0)}
                if "binding" in unit:
                    marker["binding"] = unit["binding"]
                found.append(marker)
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


def _align(student, template, *, panel_reference=None):
    """{student slide number: template slide} by the codes each slide carries,
    then by position for slides without codes when the decks are the same
    length, then by the slide's printed text: a copy of an older version of the
    deck may lack codes and have slides added or removed."""
    by_codes, pairs, source_bound = [], {}, set()
    for t in template["slides"]:
        by_codes.append((_signature(t), t))
    for s in student["slides"]:
        mine = _signature(s)
        if mine:
            best = max(by_codes, key=lambda item: len(item[0] & mine), default=(set(), None))
            if best[1] is not None and best[0] & mine:
                pairs[s["number"]] = best[1]
        bound = [m for m in markers({"slides": [s]}) if "binding" in m]
        if any(m.get("binding", {}).get("kind") == response_binding.PANEL_KIND for m in bound):
            source_bound.add(s["number"])
            originals = [t for t in template["slides"] if response_binding._panel_authorized(panel_reference, t, student['size'])
                         and panel_reference.matches_deck(template)
                         and response_binding.panel_source(markers({"slides": [s]}), t,
                                                           student["size"], _plain, MARKER) is not None]
            if len(originals) == 1:
                pairs[s["number"]] = originals[0]
            else:
                pairs.pop(s["number"], None)
            continue
        if any(m.get("binding", {}).get("kind") in ("drag-sort", "picture-activity", "raster-card-activity", "numeric-record-group",
                                                     "conditional-response-row", "raster-highlight-activity", graphical_choice.KIND,
                                                     workspace_binding.KIND) for m in bound):
            source_bound.add(s["number"])
            originals = [t for t in template["slides"]
                         if (graphical_choice.source(bound[0]["binding"], t, student["size"], _plain) is not None
                             if len(bound) == 1 and bound[0].get("binding", {}).get("kind") == graphical_choice.KIND
                             else response_binding.original_slide_matches(bound, t, student["size"], _plain))]
            if len(originals) == 1:
                pairs[s["number"]] = originals[0]
            else:
                pairs.pop(s["number"], None)
    if len(student["slides"]) == len(template["slides"]):
        for s, t in zip(student["slides"], template["slides"]):
            if s["number"] not in source_bound:
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
        if s["number"] in pairs or not mine or s["number"] in source_bound:
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
    base = {u["key"]: u for u in template_slide["units"] if not u.get("picture")} if template_slide else {}
    out, exact = [], {}
    for unit in student_slide["units"]:
        if unit.get("picture"):
            continue  # Images are completion evidence only under explicit source bindings.
        before = base.get(unit["key"])
        if template_slide and unit["box"]:
            # Google export renumbers shape names after a copy gains a box.
            # The same name can then refer to an unrelated heading or prompt.
            # Prefer an unambiguous unchanged rectangle, including empty boxes.
            same_place = [u for u in template_slide["units"] if not u.get("picture") and u["box"] and
                          max(abs(a - b) for a, b in zip(u["box"], unit["box"])) <= 0.001 * max(size)]
            if len(same_place) == 1:
                before = same_place[0]
            elif before and before["box"] and before["box"] != unit["box"] and \
                    _plain(before["text"]) != _plain(unit["text"]):
                before = None
        if before is None and template_slide:
            # A box the export renamed: compare with the most similar box on the slide.
            scored = [(difflib.SequenceMatcher(None, _plain(u["text"]), _plain(unit["text"])).ratio(), u)
                      for u in template_slide["units"] if not u.get("picture") and _plain(u["text"])]
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


def _assign(slide_markers, written, template_slide, size, *, student_slide=None, panel_reference=None):
    """{(category, id): [(unit, words)]} plus uncoded [(unit, words)] and how it was matched."""
    result = {(m["category"], m["id"]): [] for m in slide_markers}
    if not slide_markers:
        return result, list(written), "no code"
    if any(m.get("binding", {}).get("kind") == response_binding.PANEL_KIND for m in slide_markers):
        return response_binding.panel_answers(slide_markers, written, template_slide, student_slide,
                                              size, _plain, MARKER, authority=panel_reference)
    if any("binding" in m for m in slide_markers):
        if all(m.get("binding", {}).get("kind") == writing_fields.KIND for m in slide_markers):
            return writing_fields.answers(slide_markers, written, template_slide, student_slide, size, _plain)
        if all(m.get("binding", {}).get("kind") == workspace_binding.KIND for m in slide_markers):
            return workspace_binding.answers(slide_markers, written, template_slide, student_slide, size, _plain)
        if all(m.get("binding", {}).get("kind") == "conditional-response-row" for m in slide_markers):
            return response_binding.row_answers(slide_markers, written, template_slide, student_slide, size, _plain)
        if all(m.get("binding", {}).get("kind") == "raster-highlight-activity" for m in slide_markers):
            return response_binding.highlight_answers(slide_markers, written, template_slide, student_slide, size, _plain)
        if any(m.get("binding", {}).get("kind") == graphical_choice.KIND for m in slide_markers):
            return graphical_choice.answers(slide_markers, written, template_slide, student_slide, size, _plain)
        numeric = [m for m in slide_markers if m.get("binding", {}).get("kind") == "numeric-record-group"]
        if numeric:
            ordinary = [m for m in slide_markers if m not in numeric]
            if len(numeric) != 1 or any("binding" in m for m in ordinary):
                return result, list(written), response_binding.REVIEW
            records, loose, basis = response_binding.numeric_answers(numeric, written, template_slide, student_slide, size, _plain)
            if basis == response_binding.REVIEW:
                return result, list(written), basis
            if ordinary:
                other, loose, other_basis = _assign(ordinary, loose, template_slide, size, student_slide=student_slide)
                records.update(other)
                if other_basis == response_binding.REVIEW:
                    return result, list(written), other_basis
            return records, loose, basis
        if all(m.get("binding", {}).get("kind") == "raster-card-activity" for m in slide_markers):
            return response_binding.raster_answers(slide_markers, written, template_slide, student_slide, size, _plain)
        if all(m.get("binding", {}).get("kind") in ("picture-slot", "picture-activity") for m in slide_markers):
            return response_binding.picture_answers(slide_markers, written, template_slide, student_slide, size)
        return response_binding.drag_answers(slide_markers, written, template_slide, student_slide, size, _plain)
    original = markers({"slides": [template_slide]}) if template_slide else []
    orphan_drafts = [m for m in slide_markers if m["category"] == "DRAFT"
                     and any(n["category"] == "NOTES" and n["id"] == m["id"] for n in original)
                     and not any(n["category"] == "NOTES" and n["id"] == m["id"] for n in slide_markers)]
    if orphan_drafts:
        if not all(sum(m["category"] == "DRAFT" and m["code"] == draft["code"] and m["id"] == draft["id"]
                       and response_binding.same_box(m["box"], draft["box"], size) for m in original) == 1
                   for draft in orphan_drafts):
            return result, list(written), response_binding.REVIEW
        if len(slide_markers) == len(orphan_drafts) == 1 and any(
                n.get("binding", {}).get("kind") == graphical_choice.KIND for n in original):
            return graphical_choice.revision_answers(orphan_drafts[0], written, template_slide,
                                                      student_slide, size, _plain)
        labels = (template_slide or {}).get("units", [])
        fields = [u for u in labels if u.get("box") and not u["text"].strip()]
        if (response_binding.tiny_points(slide_markers, size) and all(any(
                _inside(_center(draft["box"]), field["box"]) for field in fields) for draft in orphan_drafts)):
            responses, context = [], []
            for item in written:
                unit, _ = item
                target = context if any(u.get("box") and u["text"].strip()
                                        and response_binding.same_box(unit.get("box"), u["box"], size) for u in labels) else responses
                target.append(item)
            assigned, loose, basis = response_binding.point_answers(slide_markers, responses, template_slide, size, marker_pattern=MARKER)
            return assigned, context + loose, basis
        first = [u for u in labels if u.get("box") and re.match(r"\s*(?:your\s+)?first\s+answer\b", u["text"], re.I)]
        revised = [u for u in labels if u.get("box") and re.match(r"\s*(?:your\s+)?revised\s+answer\b", u["text"], re.I)]
        if len(slide_markers) != 1 or len(first) != 1 or len(revised) != 1:
            return result, list(written), response_binding.REVIEW
        if not any(_distance(_center(field["box"]), revised[0]["box"]) <
                   _distance(_center(field["box"]), first[0]["box"]) for field in fields):
            return result, list(written), response_binding.REVIEW
        loose = []
        for item in written:
            unit, words = item
            box, margin = unit.get("box"), .001 * max(size)
            anchored = [field for field in fields if box and (
                response_binding.same_box(box, field["box"], size)
                or (box[0] >= field["box"][0] - margin and box[1] >= field["box"][1] - margin
                    and box[0] + box[2] <= field["box"][0] + field["box"][2] + margin
                    and box[1] + box[3] <= field["box"][1] + field["box"][3] + margin))]
            if not anchored:
                loose.append(item)
                continue  # printed labels, prompts and other context are not answers
            if len(anchored) != 1:
                return result, list(written), response_binding.REVIEW
            a, b = _distance(_center(anchored[0]["box"]), first[0]["box"]), _distance(_center(anchored[0]["box"]), revised[0]["box"])
            if a == b:
                return result, list(written), response_binding.REVIEW
            (result[("DRAFT", orphan_drafts[0]["id"])] if b < a else loose).append(item)
        return result, loose, "original revision box labels"
    if response_binding.tiny_points(slide_markers, size):
        return response_binding.point_answers(slide_markers, written, template_slide, size, marker_pattern=MARKER)
    template_units = [u for u in (template_slide or {}).get("units", [])
                      if not u.get("picture") and u["box"] and not MARKER.search(u["text"])
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

    def printed_bank_label(marker):
        # A genuine whole-term label may carry its own BANK code. General
        # response geometry still excludes code boxes; only this label lookup
        # admits one exactly matching code, without overlooking duplicate labels.
        term = _squash(marker["id"])
        matches = [u for u in (template_slide or {}).get("units", [])
                   if not u.get("picture") and u["box"]
                   and u["box"][2] * u["box"][3] < 0.6 * size[0] * size[1]
                   and term and _squash(u["text"]) == term]
        if len(matches) != 1:
            return None
        embedded = list(MARKER.finditer(matches[0]["text"]))
        if embedded and (len(embedded) != 1 or embedded[0].groups() !=
                         ("BANK", marker["code"], marker["id"])):
            return None
        return matches[0]

    for m in [m for m in slide_markers if m["category"] == "BANK"]:
        label = printed_bank_label(m)
        for item in list(remaining):
            if label and label["key"] == item[0]["key"]:
                result[("BANK", m["id"])].append(item)
                remaining.remove(item)
    bank = [m for m in slide_markers if m["category"] == "BANK"]
    if bank and remaining:
        # An answer box without the term in it belongs to the nearest term label.
        # A term label is the whole printed term, not a prefix of a section
        # heading such as "Symbiosis & Relationships". Repeated labels do
        # not establish a unique response binding.
        labels = {m["id"]: printed_bank_label(m) for m in bank}
        placed_labels = [(m, labels[m["id"]]) for m in bank if labels[m["id"]]]
        only_bank = len(bank) == len(slide_markers)
        if len(placed_labels) != len(bank):
            return result, uncoded + remaining, "BANK term labels need review"
        bank_unbound = False
        for item in list(remaining):
            if not item[0]["box"] or not placed_labels:
                if only_bank:
                    uncoded.append(item)
                    remaining.remove(item)
                    bank_unbound = True
                continue
            c = _center(item[0]["box"])
            nearest = sorted(placed_labels, key=lambda ml: _distance(c, ml[1]["box"]))
            m, label = nearest[0]
            if (only_bank or _distance(c, label["box"]) <= 0.08 * max(size)) and len(nearest) > 1 \
                    and _distance(c, label["box"]) == _distance(c, nearest[1][1]["box"]):
                uncoded.append(item)
                remaining.remove(item)
                bank_unbound = True
                continue
            if only_bank or _distance(c, label["box"]) <= 0.08 * max(size):
                result[("BANK", m["id"])].append(item)
                remaining.remove(item)
        if bank_unbound:
            return result, uncoded, "BANK term labels need review"
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
    repeated = [key for key, count in Counter((m["category"], m["code"], m["id"]) for m in codes).items() if count > 1]
    if repeated:
        locations = ["%s %s on slides %s" % (category, ident, ", ".join(str(n) for n in sorted({
            m["slide"] for m in codes if (m["category"], m["code"], m["id"]) == (category, code, ident)})))
                     for category, code, ident in repeated]
        issue("duplicate_feedback_marker", "A response code appears more than once (%s); check which question each code belongs to." % "; ".join(locations))
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


def student_coding_required(agenda, class_key, week_label, form_id):
    """The exception is the exact registered Biology Photosynthesis lesson.

    Student file names never establish this policy. An unresolved Agenda scope
    stays strict; the existing private exact-assignment holds remain independent.
    """
    try:
        lessons = [lesson for lesson in agenda.lessons(class_key, week_label)
                   if any(form.get("form_id") == form_id for form in lesson["forms"])]
    except (TypeError, KeyError, AttributeError, ValueError):
        return True
    if not isinstance(form_id, str) or not form_id.strip() or len(lessons) != 1:
        return True
    forms = [form for form in lessons[0]['forms'] if form.get('form_id') == form_id]
    if len(forms) != 1:
        return True
    if class_key != 'biology':
        return False
    # A mixed Agenda row may continue Photosynthesis while handing out a
    # different named lesson. Only this exact Form's teacher-controlled subject
    # label can override the row topic; URLs and generic controls cannot.
    label = str(forms[0].get('label') or '').strip()
    bare = re.sub(r'\b(?:wk|week|cycle|c)\s*\d+\b', '', label, flags=re.I)
    generic = {'turn', 'it', 'in', 'submit', 'submission', 'upload', 'your', 'the', 'a', 'an',
               'first', 'second', 'form', 'google', 'student', 'copy', 'work', 'worksheet',
               'assignment', 'lesson', 'notes', 'revision', 'optional', 'bonus', 'required',
               'week', 'cycle', 'wk', 'term', 'part', 'response', 'responses', 'answer', 'answers',
               'of', 'for', 'to', 'this', 'these', 'here', 'please', 'complete', 'completed',
               'biology', 'bio', 'science', 'sciences', 'class', 'course', 'anatomy', 'physiology',
               'forensics', 'ap', 'lab', 'laboratory', 'activity', 'exercise', 'quiz', 'assessment', 'module'}
    meaningful = (not re.search(r'https?://|forms\.gle/', label, re.I)
                  and bool(set(re.findall(r'[a-z]+', bare.lower())) - generic))
    subject = label if meaningful else str(lessons[0].get('topic') or '')
    return bool(re.search(r'\bphotosynthesis\b', subject, re.I))


def student_readiness(deck, *, revision="", require_complete_codes=True):
    """Missing student coding is advisory; source/response ambiguity still holds."""
    check = readiness(deck, revision=revision)
    if require_complete_codes or check["status"] == "READY":
        return check
    coverage_issues = {"marker_inventory_missing_or_duplicated", "marker_inventory_not_on_first_slide",
                       "marker_inventory_counts_unparsed", "marker_inventory_count_mismatch", "marker_inventory_id_mismatch",
                       "draft_without_matching_notes"}
    headers = [match for slide in deck["slides"] for unit in slide["units"]
               for match in INVENTORY.finditer(unit["text"])]
    codes = markers(deck)
    if (not set(check["issues"]) <= coverage_issues or len({header.group(1) for header in headers}) > 1
            or len({marker["code"] for marker in codes}) > 1):
        # Show the actual retained ambiguity, not a coding-only warning this
        # student's policy has already waived. Keep the full census intact.
        blocking = [i for i in check["issues"] if i not in coverage_issues]
        if blocking:
            diagnostic = check["diagnostics"][check["issues"].index(blocking[0])]
            return {**check, "problem": check["label"] + ". " + diagnostic,
                    "action": "Check the response codes against the original questions, preserving the student's answers; then rerun grading."}
        return check
    countable = sum(marker["category"] != "OPTIONAL" for marker in codes)
    warning = ("Student coding is incomplete. Completion uses only the coded boxes found in this copy; "
               "missing codes do not block its grade.") if countable else (
               "No countable codes were found in this copy. Completion is N/A; no zero is prepared.")
    return {**check, "status": "READY", "label": "Ready to count found codes" if countable else "N/A",
            "coding_status": check["status"], "student_coding_policy": "found_codes",
            "warnings": [warning], "problem": None, "action": None}


def teacher_readiness(deck, *, revision="", require_complete_codes=True):
    """A stale teacher inventory cannot add requirements to found-code copies."""
    checked = student_readiness(deck, revision=revision, require_complete_codes=require_complete_codes)
    if require_complete_codes or checked["status"] != "READY":
        return checked
    if not any(marker["category"] != "OPTIONAL" for marker in markers(deck)):
        return {**checked, "status": "NOT_READY", "label": "Needs check",
                "problem": "No countable response codes are verified in this teacher source; no score or missing-work zero is prepared.",
                "action": "Confirm the exact coded handed-out source before preparing scores."}
    if checked.get("coding_status") != "NOT_READY":
        return checked
    return {**checked, "label": "Ready to count found codes", "teacher_coding_policy": "found_codes",
            "warnings": ["Teacher inventory is stale or incomplete. Completion uses only the actual codes in each submitted copy."],
            "problem": None, "action": None}


def assess(student, template, *, found_codes_only=False, panel_reference=None):
    """The completion counts for one student deck against the distributed copy."""
    code, required, basis, note = inventory(template or student)
    if template:
        # A copy of another version of the same lesson carries its own code list:
        # the student answers the boxes in the copy they were handed, so that list counts.
        own = inventory(student)
        if own[2] == "header" and own[0] == code and own[1] != required:
            code, required, basis, note = own
    codes = markers(student)
    if found_codes_only:
        # A stale/missing inventory cannot add requirements or erase actual
        # markers. Never lend absent student markers from a newer teacher deck.
        required = {category: list(dict.fromkeys(marker["id"] for marker in codes
                                                if marker["category"] == category)) for category in CATEGORIES}
        basis = "found_codes"
    pairs = _align(student, template, panel_reference=panel_reference) if template else {}
    template_text = {s["number"]: " ".join(t for u in s["units"] for t in _tokens(u["text"]))
                     for s in (template or {"slides": []})["slides"]}
    found = {}
    uncoded, how = [], set()
    bank_unbound = False
    response_unbound = False
    panel_unbound = False
    numeric_unbound = False
    choice_unbound = False
    draft_unbound = False
    bank_review_slides, response_review_slides = set(), set()
    handed_out = markers(template) if template else []
    current = {(c, qid) for c in CATEGORIES for qid in required[c]}
    for slide in student["slides"]:
        mine = [m for m in codes if m["slide"] == slide["number"]]
        match = pairs.get(slide["number"])
        if template and match is None:
            if any("binding" in m for m in mine):
                response_unbound = True
                panel_unbound |= any(m.get('binding', {}).get('kind') == response_binding.PANEL_KIND for m in mine)
                response_review_slides.add(slide["number"])
                numeric_unbound |= any(m.get("binding", {}).get("kind") == "numeric-record-group" for m in mine)
                choice_unbound |= any(m.get("binding", {}).get("kind") == graphical_choice.KIND for m in mine)
            continue  # nothing to compare this slide with
        if template and not found_codes_only and not any((m["category"], m["id"]) in current for m in mine):
            # A copy of an older version of the deck: this slide lacks today's
            # codes, so use the handed-out deck's codes for its matching slide.
            mine = [m for m in handed_out if m["slide"] == match["number"]]
        if template:
            written, exact = _written(slide, match, student["size"])
        else:
            written = [(u, _plain(u["text"])) for u in slide["units"] if MARKER.search(u["text"]) and _plain(u["text"])]
            exact = {u["key"]: " ".join(words.split()) for u, words in written}
        assigned, loose, basis_here = _assign(mine, written, match, student["size"], student_slide=slide,
                                             panel_reference=panel_reference)
        if basis_here == "BANK term labels need review":
            bank_unbound = True
            bank_review_slides.add(slide["number"])
        if basis_here == response_binding.REVIEW:
            response_unbound = True
            panel_unbound |= any(m.get('binding', {}).get('kind') == response_binding.PANEL_KIND for m in mine)
            response_review_slides.add(slide["number"])
            draft_unbound |= any(m["category"] == "DRAFT" and not any(
                n["category"] == "NOTES" and n["id"] == m["id"] for n in mine) for m in mine)
            numeric_unbound |= any(m.get("binding", {}).get("kind") == "numeric-record-group" for m in mine)
            choice_unbound |= any(m.get("binding", {}).get("kind") == graphical_choice.KIND for m in mine)
        if mine and written:
            how.add(basis_here)
        for unit, words in loose:
            if not words.startswith("moved: "):  # a dragged card isn't writing
                uncoded.append({"slide": slide["number"], "excerpt": words[:80]})
        printed = template_text.get(match["number"], "") if match else ""
        received_print = response_binding.printed_prompts(slide, match, mine, student["size"])
        if received_print:
            printed += " " + " ".join(t for text in received_print for t in _tokens(text))
        for key, items in assigned.items():
            words = " ".join(w for _, w in items)
            if not items:
                status = "blank"
            elif any(u.get("workspace_supplied_copy") for u, _ in items) or (len(words.split()) >= COPY_WORDS and " " + words.lower() + " " in " " + printed + " "):
                status = "copied"
            else:
                status = "done"
            prior = found.get(key)
            if not prior or (prior["status"] != "done" and status == "done"):
                answer = " ".join(u.get("verified_workspace_answer", exact.get(u["key"], w)) for u, w in items if not u.get("picture") and not u.get("completion_only"))
                where = match or slide  # named and ordered by the handed-out deck's slide
                found[key] = {"category": key[0], "id": key[1], "slide": slide["number"], "status": status,
                              "excerpt": words[:80], "answer": answer[:MAX_ANSWER],
                              "at": where["number"], "question": slide_title(where)}
                if any(u.get("completion_only") == "new original profile image" for u, _ in items):
                    found[key]["completion_evidence"] = "New profile picture placed in the original response region"
                elif any(u.get("picture") for u, _ in items):
                    found[key]["completion_evidence"] = "Original picture placed in response region"
                elif any(u.get("completion_only") == "numeric record" for u, _ in items):
                    found[key]["completion_evidence"] = "Numeric measurement recorded in original response fields"
                    found[key]["recorded_fields"] = [{"field": u["recorded_field"], "value": u["recorded_value"]}
                                                      for u, _ in items if u.get("completion_only") == "numeric record"]
                elif any(u.get("completion_only") == "highlighter placement" for u, _ in items):
                    found[key]["completion_evidence"] = "Original highlighter placed on the review maze"
                elif any(u.get("completion_only") == "circled choice" for u, _ in items):
                    found[key]["completion_evidence"] = "One original YES/NO option circled"
                    found[key]["recorded_choice"] = next(u["recorded_choice"] for u, _ in items
                                                        if u.get("completion_only") == "circled choice")
                elif any(u.get("completion_only") == "original raster option" for u, _ in items):
                    found[key]["completion_evidence"] = "One original printed option circled"
                    found[key]["recorded_choice"] = next(u["recorded_choice"] for u, _ in items
                                                        if u.get("completion_only") == "original raster option")
                elif any(u.get("completion_only") == "highlighted choice" for u, _ in items):
                    found[key]["completion_evidence"] = "One original YES/NO option highlighted"
                    found[key]["recorded_choice"] = next(u["recorded_choice"] for u, _ in items
                                                        if u.get("completion_only") == "highlighted choice")
                elif any(u.get("completion_only") for u, _ in items):
                    found[key]["completion_evidence"] = "Original card placed in response region"
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
    missing_code_warning = None
    if scored_items and missing_codes * 10 >= len(scored_items) * 3:
        # Missing boxes are already excluded from the denominator. Their warning
        # must not turn an otherwise usable completion score into a review hold.
        missing_code_warning = ("This copy is missing %d of its %d codes. Its completion score uses only the coded "
                                "boxes found in this copy; missing codes are not counted as unfinished work."
                                % (missing_codes, len(scored_items)))
    if bank_unbound:
        student_note = ("These BANK responses do not have a unique printed term label. "
                        "Check the term-to-response match by hand before scoring or entering grades.")
    if response_unbound:
        student_note = ("This response panel is waiting for its frozen original-source reference and verified panel match. "
                        "The source integration must be completed before scoring or entering grades.") if panel_unbound else (
                       "This revision response needs its original labeled answer box checked before scoring or entering grades.") if draft_unbound else (
                       "This YES/NO activity needs its original question, visible choice mark and separate revision box checked "
                        "before scoring or entering grades.") if choice_unbound else (
                       "This numeric activity needs its original printed instructions and response blanks checked "
                        "before scoring or entering grades.") if numeric_unbound else (
                       "This activity needs its original cards or pictures and response regions checked "
                       "before scoring or entering grades.")
    if student_note:
        locations = sorted(response_review_slides if response_unbound else bank_review_slides)
        if locations:
            student_note = ("Slide " if len(locations) == 1 else "Slides ") + ", ".join(map(str, locations)) + ": " + student_note
    return {"code": code, "inventory": basis, "note": student_note, "inventory_note": note,
            "missing_code_warning": missing_code_warning,
            "scoring_basis": "found_codes" if found_codes_only else "inventory",
            "coding": coding_issues(template or student),
            "notes_score": None if bank_unbound or response_unbound else score(counts["NOTES"]["done"] + counts["BANK"]["done"],
                                 counts["NOTES"]["required"] + counts["BANK"]["required"]),
            "revision_score": None if bank_unbound or response_unbound else score(counts["DRAFT"]["done"], counts["DRAFT"]["required"]),
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
_templates, _results, _native_teacher_templates = {}, {}, {}
MAX_CACHED_TEMPLATES, MAX_CACHED_RESULTS = 24, 600


def _remember(cache, key, value, limit):
    if key not in cache and len(cache) >= limit:
        cache.pop(next(iter(cache)))
    cache[key] = value
    return value


def _read(token, file_id, meta, *, source_role="student"):
    from . import google, native_teacher_source
    if meta.get("trashed"):
        raise Unreadable("This exact deck is in trash; no contents were scored or restored.")
    try:
        return read_deck(google.deck_bytes(token, file_id, meta.get("mimeType", "")))
    except google.GoogleError as error:
        # A provider export-size limit is distinct from missing access, quota,
        # or a generic 403. Native reading changes no source or grade policy.
        if (source_role != "student" or meta.get("mimeType") != SLIDES
                or error.status != 403 or error.reason != "exportSizeLimitExceeded"):
            raise
    before = google.drive_file(token, file_id, google.FILE_FIELDS + ",version")
    fields = ("id", "mimeType", "trashed", "version", "modifiedTime")
    if (before.get("id") != file_id or before.get("mimeType") != SLIDES
            or before.get("trashed") is not False or not str(before.get("version", "")).isdigit()
            or not before.get("modifiedTime")
            or any(before.get(field) != meta.get(field) for field in fields)):
        raise Unreadable("The exact submitted source changed or lacks a verified revision.")
    try:
        deck = native_teacher_source.read(google.native_presentation(token, file_id),
            presentation_id=file_id, revision=before["version"], max_slides=MAX_SLIDES,
            max_slide_bytes=MAX_SLIDE_BYTES, source_role="student")
    except native_teacher_source.Unreadable as error:
        raise Unreadable(str(error)) from None
    if google.drive_file(token, file_id, google.FILE_FIELDS + ",version") != before:
        raise Unreadable("The exact submitted source changed during its native read; recheck it.")
    return deck


def _template(token, deck_id):
    """(metadata, parsed deck) for a handed-out deck, re-read only when it changes."""
    from . import google
    meta = google.drive_file(token, deck_id, google.FILE_FIELDS + ",version")
    if meta.get("trashed"):
        raise Unreadable("This linked deck is in trash. It is not a current lesson source; it was not restored.")
    key = (deck_id, meta.get("modifiedTime", ""), str(meta.get("version") or ""))
    if key not in _templates:
        _remember(_templates, key, _read(token, deck_id, meta, source_role="teacher"), MAX_CACHED_TEMPLATES)
    return meta, _templates[key]


def _teacher_template(token, deck_id):
    """Only exact teacher references may use native GET after an export 403."""
    from . import google, native_teacher_source
    try:
        return _template(token, deck_id)
    except google.GoogleError as error:
        if error.status != 403:
            raise
    before = google.drive_file(token, deck_id, google.FILE_FIELDS + ",version")
    if before.get("id") != deck_id or before.get("mimeType") != SLIDES or before.get("trashed"):
        raise Unreadable("The exact teacher source is unavailable for a native read.")
    key = (deck_id, before.get("modifiedTime", ""), str(before.get("version") or ""))
    if key not in _native_teacher_templates:
        try:
            deck = native_teacher_source.read(google.native_presentation(token, deck_id),
                presentation_id=deck_id, revision=before.get("version"), max_slides=MAX_SLIDES,
                max_slide_bytes=MAX_SLIDE_BYTES)
        except native_teacher_source.Unreadable as error:
            raise Unreadable(str(error)) from None
        after = google.drive_file(token, deck_id, google.FILE_FIELDS + ",version")
        fields = ("id", "mimeType", "trashed", "version", "modifiedTime")
        if any(before.get(field) != after.get(field) for field in fields):
            raise Unreadable("The exact teacher source changed during its native read; recheck it.")
        _remember(_native_teacher_templates, key, deck, MAX_CACHED_TEMPLATES)
    return before, _native_teacher_templates[key]


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


def form_source_scope(agenda, class_key, week_label, form_id, *, binding=None, ownership=None):
    """Select source ownership before any source read; a week is not an assignment."""
    lessons = [l for l in agenda.lessons(class_key, week_label)
               if any(f.get('form_id') == form_id for f in l['forms'])]
    if len(lessons) != 1:
        raise Unreadable('The exact Form needs one unambiguous current Agenda lesson.')
    lesson = lessons[0]
    forms = {f.get('form_id') or f.get('url') for f in lesson['forms']}
    if binding:
        teachers = binding.get('teacher_ids') if isinstance(binding, dict) else None
        if (not isinstance(teachers, list) or not 1 <= len(teachers) <= 4
                or any(not isinstance(fid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', fid) for fid in teachers)
                or len(teachers) != len(set(teachers))):
            raise Unreadable('The exact Form teacher-source ownership is invalid; verify assignment setup.')
        expected = {'class_key': class_key, 'week': week_label, 'form_id': form_id,
                    'agenda_id': getattr(agenda, 'spreadsheet_id', None)}
        if any(k in binding and binding[k] != value for k, value in expected.items()):
            raise Unreadable('The teacher-source declaration belongs to another assignment Form or Agenda scope.')
        return lesson
    if ownership:
        if not isinstance(ownership, dict):
            raise Unreadable('The exact Form source ownership is invalid. Refresh this assignment before scoring.')
        basis = {'class': class_key, 'week': week_label, 'form': form_id,
                 'forms': sorted(str(f) for f in forms), 'materials': lesson['materials']}
        digest = hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()
        ids = ownership.get('source_ids')
        if (ownership.get('basis_sha256') != digest or not isinstance(ids, list) or not ids
                or any(not isinstance(fid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,200}', fid) for fid in ids)
                or len(ids) != len(set(ids))):
            raise Unreadable('The exact Form source ownership changed. Refresh this assignment before scoring.')
        selected = [m for m in lesson['materials'] if any(
            re.search(r'/presentation/d/' + re.escape(fid) + r'(?:/|$)', m['url']) for fid in ids)]
        found = {match[1] for m in selected
                 if (match := re.search(r'/presentation/d/([A-Za-z0-9_-]{20,})', m['url']))}
        if found != set(ids):
            raise Unreadable('The exact Form source ownership no longer matches the current Agenda.')
        return {**lesson, 'materials': selected}
    if len(forms) != 1:
        raise Unreadable('%s · %s · Form %s: separate assignments need exact teacher-source ownership. '
                         'Connect this Form to its own teacher sources in assignment setup; no sibling lesson was read.'
                         % (class_key, week_label, form_id))
    return lesson


def form_source_ownership(config, agenda, class_key, week_label, form_id, *, binding=None):
    """Reuse the existing validated deck -> assignment -> Form contract."""
    from school_workflow_hub.adapters.dated_agenda import _day_assignment_forms
    from school_workflow_hub.adapters.shared_form_intake import private_mapping
    from school_workflow_hub.adapters.hosted_agenda import ConnectionError
    lessons = [l for l in agenda.lessons(class_key, week_label)
               if any(f.get('form_id') == form_id for f in l['forms'])]
    if len(lessons) != 1:
        raise Unreadable('The exact Form needs one unambiguous current Agenda lesson.')
    lesson = lessons[0]
    if not config.get('HUB_INTAKE_DECK_ASSIGNMENTS') and not config.get('HUB_ASSIGNMENT_FORM_IDS'):
        return None
    try:
        decks = private_mapping(config, 'HUB_INTAKE_DECK_ASSIGNMENTS')
        forms = private_mapping(config, 'HUB_ASSIGNMENT_FORM_IDS')
    except ConnectionError:
        raise Unreadable('The exact assignment source mappings are invalid; verify assignment setup.') from None
    linked = {match[1] for m in lesson['materials']
              if (match := re.search(r'/presentation/d/([A-Za-z0-9_-]{20,})', m['url']))}
    current_forms = {f.get('form_id') or f.get('url') for f in lesson['forms']}
    relevant = set(binding['teacher_ids']) if binding else linked if len(current_forms) == 1 else set()
    for fid in relevant & set(decks):
        key = decks[fid]
        if (not isinstance(key, str) or not re.fullmatch(re.escape(class_key) + r':[A-Za-z0-9:_-]{1,128}', key)
                or (key in forms and (not isinstance(forms[key], str)
                                      or not re.fullmatch(r'[A-Za-z0-9_-]{20,128}', forms[key])))):
            raise Unreadable('A current teacher has an invalid explicit assignment-Form mapping; verify assignment setup.')
    if len(current_forms) == 1:
        for fid in linked & set(decks):
            key = decks[fid]
            if (not isinstance(key, str) or (key in forms and
                    (not isinstance(forms[key], str) or forms[key] != form_id))):
                raise Unreadable('A current linked teacher has explicit ownership for another Form; verify assignment setup.')
    exact = {fid for fid in linked if isinstance(decks.get(fid), str) and forms.get(decks[fid]) == form_id}
    if binding:
        teachers = set(binding['teacher_ids'])
        contradictory = any(fid in decks and forms.get(decks[fid]) not in (None, form_id) for fid in teachers)
        if contradictory or (exact and exact != teachers):
            raise Unreadable('Explicit teacher-source and assignment-Form ownership disagree; verify this Form setup.')
        return None
    row = lesson.get('row')
    cells = agenda.classes[class_key].get('cells')
    if row is None or cells is None:
        return None
    checked = _day_assignment_forms(cells, row, '', class_key, config)
    if not checked or checked.get('error'):
        return None
    matches = [f for f in checked['forms'] if f['form_id'] == form_id]
    if len(matches) != 1 or len(exact) != 1:
        return None
    basis = {'class': class_key, 'week': week_label, 'form': form_id,
             'forms': sorted(str(f.get('form_id') or f.get('url')) for f in lesson['forms']),
             'materials': lesson['materials']}
    return {'source_ids': sorted(exact), 'assignment_key': matches[0]['assignment_key'],
            'basis_sha256': hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()}


def lesson_decks(agenda, class_key, week_label, form_id, *, binding=None, ownership=None):
    """The Slides linked in the Agenda row of the lesson that holds this Form."""
    try:
        scoped = form_source_scope(agenda, class_key, week_label, form_id, binding=binding, ownership=ownership)
    except Unreadable:
        return []
    if binding:
        return list(binding['teacher_ids'])
    for lesson in [scoped]:
        if any(f["form_id"] == form_id for f in lesson["forms"]):
            ids = []
            for material in lesson["materials"]:
                match = re.search(r"/presentation/d/([A-Za-z0-9_-]{20,})", material["url"])
                if match and match.group(1) not in ids:
                    ids.append(match.group(1))
            return ids
    return []


def _registered_teacher_membership(token, parent, meta, fields):
    """A missing parent field needs one exact-name, MIME, parent-filtered read.

    Drive does not offer an ID equality query. Select the declared ID from a
    complete bounded result, rejecting any response outside the exact filter.
    Never enumerate a Shared Drive root or accept a filename as source identity.
    """
    from . import google
    if "parents" in meta:
        if not isinstance(meta["parents"], list) or parent not in meta["parents"]:
            raise Unreadable("The registered teacher is outside its declared source folder.")
        return None
    name = meta.get("name")
    if not isinstance(name, str) or not name.strip():
        raise Unreadable("The registered teacher's parent membership cannot be verified.")
    escaped = name.replace("\\", "\\\\").replace("'", "\\'")
    page = google.get_json("https://www.googleapis.com/drive/v3/files", token, {
        "q": "name = '%s' and mimeType = '%s' and '%s' in parents and trashed = false"
             % (escaped, meta["mimeType"], parent),
        "fields": "nextPageToken,incompleteSearch,files(%s)" % fields, "pageSize": 20,
        "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
    items = page.get("files")
    if (page.get("nextPageToken") or page.get("incompleteSearch")
            or not isinstance(items, list) or len(items) > 20):
        raise Unreadable("The registered teacher's bounded parent lookup is incomplete.")
    ids = [item.get("id") for item in items]
    if (len(set(ids)) != len(ids) or ids.count(meta["id"]) != 1
            or any(item.get("name") != name or item.get("mimeType") != meta["mimeType"]
                   or item.get("trashed") is not False or parent not in (item.get("parents") or [])
                   for item in items)):
        raise Unreadable("The exact registered teacher is not verified in its declared source folder.")
    item = next(item for item in items if item["id"] == meta["id"])
    if any(item.get(k) != meta.get(k) for k in ("id", "name", "mimeType", "trashed", "version", "modifiedTime")):
        raise Unreadable("The registered teacher metadata and parent lookup do not agree.")
    return sorted(items, key=lambda item: item["id"])


def _registered_teacher_read_failure(error, binding):
    """A typed GET timeout for this registered collection, never guessed prose."""
    from . import google
    if (binding and isinstance(error, google.GoogleError)
            and error.status is None and error.reason == "read_timeout"):
        return {"source": "registered_teacher", "reason": "read_timeout",
                "scope": {"class": binding["class_key"], "week": binding["week"],
                          "form": binding["form_id"]}}
    return None


def _registered_teacher_sources(token, binding):
    """Exact existing parent/teacher metadata bookends; no root search or move."""
    from . import google
    result = {"decks": [], "stale_links": [], "folders": [], "problem": None}
    fields = google.FILE_FIELDS + ",version,md5Checksum,size"
    parent = binding["source_folder_id"]
    try:
        folder = google.drive_file(token, parent, fields)
        if (folder.get("id") != parent or folder.get("mimeType") != google.FOLDER
                or folder.get("trashed") is not False):
            raise Unreadable("The registered teacher source folder is not a verified current folder.")
        memberships = {}
        for fid in binding["teacher_ids"]:
            meta = google.drive_file(token, fid, fields)
            if (meta.get("id") != fid or meta.get("mimeType") not in (SLIDES, google.PPTX)
                    or meta.get("trashed") is not False
                    or not meta.get("version") or not meta.get("modifiedTime")):
                raise Unreadable("An exact registered teacher is retired or unreadable.")
            memberships[fid] = _registered_teacher_membership(token, parent, meta, fields)
            result["decks"].append({"id": fid, "label": "Registered teacher source",
                                    "title": meta.get("name", ""), "metadata": meta})
        for deck in result["decks"]:
            after = google.drive_file(token, deck["id"], fields)
            if (after != deck["metadata"] or
                    _registered_teacher_membership(token, parent, after, fields) != memberships[deck["id"]]):
                raise Unreadable("The exact registered teacher source collection changed during this read.")
        if google.drive_file(token, parent, fields) != folder:
            raise Unreadable("The exact registered teacher source folder changed during this read.")
        result["folders"] = [{"id": parent, "name": folder.get("name", ""), "deck_ids": list(binding["teacher_ids"])}]
        result["binding"] = binding
        result["source_folder_metadata"] = folder
        result["membership_evidence"] = memberships
    except (google.GoogleError, Unreadable) as error:
        result["problem"] = "Cannot verify the exact Form's registered teacher sources: %s" % error
        failure = _registered_teacher_read_failure(error, binding)
        if failure:
            result["read_failure"] = failure
    return result


def _registered_teacher_bookend(token, binding, before, templates):
    """The templates actually consumed must match the fresh source resolution."""
    from . import google
    after = _registered_teacher_sources(token, binding)
    if after.get("read_failure"):
        raise google.GoogleError(after["problem"], reason="read_timeout")
    if after.get("problem") or after != before:
        raise Unreadable(after.get("problem") or "The registered teacher sources changed while their contents were read.")
    expected = {deck["id"]: deck["metadata"] for deck in before["decks"]}
    if {fid for fid, _, _ in templates} != set(binding["teacher_ids"]):
        raise Unreadable("Every exact registered teacher must be readable before scoring.")
    for fid, meta, _ in templates:
        if any(meta.get(k) != expected[fid].get(k)
               for k in ("id", "name", "mimeType", "trashed", "version", "modifiedTime", "parents")):
            raise Unreadable("A consumed teacher template differs from its verified source metadata.")


def lesson_sources(token, lesson, *, binding=None, metadata_fields=None):
    """Fresh actual-folder/current-link evidence, never a lesson-name search.

    Stale/trash links are reported separately. An inaccessible or empty named
    folder is a hold; a listing alone cannot prove its folder was readable.
    """
    from . import google
    if binding:
        return _registered_teacher_sources(token, binding)
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
            meta = (google.drive_file(token, fid, metadata_fields) if metadata_fields else
                    google.drive_file(token, fid))
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
            meta = (google.drive_file(token, fid, metadata_fields) if metadata_fields else
                    google.drive_file(token, fid))
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


def scoped_lesson_sources(token, lesson, *, binding=None, ownership=None):
    if binding:
        return lesson_sources(token, lesson, binding=binding)
    if ownership:
        from . import google
        return lesson_sources(token, lesson, metadata_fields=google.FILE_FIELDS + ',version')
    return lesson_sources(token, lesson)


def _mapped_teacher_bookend(token, lesson, ownership, before, templates):
    """Exact mapped sources bracket consumed teacher bytes using metadata only."""
    after = scoped_lesson_sources(token, lesson, ownership=ownership)
    if after.get('problem') or after != before:
        raise Unreadable('The exact mapped Form teacher source changed while its contents were read.')
    expected = {d['id']: d['metadata'] for d in before['decks']}
    if {fid for fid, _, _ in templates} != set(ownership['source_ids']):
        raise Unreadable('Every exact mapped Form teacher must be readable before scoring.')
    for fid, meta, _ in templates:
        prior = expected.get(fid, {})
        if (prior.get('id') != fid or prior.get('trashed') is not False
                or not prior.get('version') or not prior.get('modifiedTime')
                or any(meta.get(k) != prior.get(k)
                       for k in ('id', 'mimeType', 'version', 'modifiedTime', 'name', 'parents'))):
            raise Unreadable('Consumed teacher bytes differ from the exact mapped Form source metadata.')


def assignment_readiness(token, config, agenda, class_key, week_label, form_id):
    """Fresh linked-source coding and persisted teacher decision, no scoring.

    All assignment entry paths must call this before creating absence zeros.
    Submitted copies still need their own gate in score_file.
    """
    from . import google, completion_holds, work
    policy = work.assignment_policy(config, agenda, class_key, week_label, form_id)
    if policy.get("problem") or policy.get("excluded"):
        return {"status": "NOT_READY" if policy.get("problem") else "EXCLUDED",
                "code": "coverage_incomplete" if policy.get("problem") else "excluded_week",
                "problem": policy.get("problem") or policy["reason"], "scope": "assignment",
                "action": "Preserve this activity's original files and grade history; no assessment is requested."}
    try:
        binding = work.form_teacher_sources(config, agenda, class_key, week_label, form_id)
        ownership = form_source_ownership(config, agenda, class_key, week_label, form_id, binding=binding)
        scoped_lesson = form_source_scope(agenda, class_key, week_label, form_id, binding=binding, ownership=ownership)
    except (ValueError, TypeError, KeyError, Unreadable) as error:
        return {"status": "NOT_READY", "code": "deck_not_ready", "label": "Needs assignment setup", "problem": str(error),
                'scope': 'assignment', 'action': '%s · %s · Form %s: connect this Form to its own exact teacher sources in assignment setup, then refresh.'
                % (class_key, week_label, form_id)}
    lesson = scoped_lesson
    selected = [form for form in lesson["forms"] if form.get("form_id") == form_id]
    label = " ".join(str(selected[0].get("label") or "").split()) if len(selected) == 1 else ""
    title = work.assignment_title(label)
    named = bool(label and not re.match(r"https?://", label, re.I)
                 and work.norm(title) not in {"turnin", "turnitin", "upload", "submission"})
    # A shared weekly topic is not the selected assignment's name. This affects
    # diagnostic context only; original source ownership and policy stay intact.
    assignment = title if named else (lesson["topic"] if len(lesson["forms"]) == 1 else "Form " + form_id)
    context = "%s · %s · %s" % (agenda.classes[class_key]["name"], week_label, assignment)
    context_scope = {"agenda_topic": lesson["topic"], "form_label": label, "form_id": form_id}
    hold = completion_holds.check(config, agenda, class_key, week_label, form_id)
    if hold["held"]:
        return {**hold, **context_scope, "context": context, "problem": context + ": " + hold["problem"],
                "student_copies_checked": False, "scope": "assignment"}
    sources = scoped_lesson_sources(token, lesson, binding=binding, ownership=ownership)
    if sources["problem"]:
        return {**context_scope, "status": "NOT_READY", "code": "deck_not_ready", "label": "Cannot read lesson", "context": context,
                "source_resolution": sources, "problem": context + ": " + sources["problem"],
                **({"read_failure": sources["read_failure"]} if sources.get("read_failure") else {}),
                "action": "Check this exact Agenda lesson folder and current deck links, then recheck; no title-based replacement was selected."}
    ids = [item["id"] for item in sources["decks"]]
    results = []
    bound_templates = []
    for deck_id in ids:
        try:
            meta, deck = _teacher_template(token, deck_id)
            if binding or ownership:
                bound_templates.append((deck_id, meta, deck))
            checked = teacher_readiness(deck, revision=meta.get("version") or meta.get("modifiedTime", ""),
                require_complete_codes=student_coding_required(agenda, class_key, week_label, form_id))
            checked.update(deck_id=deck_id, title=meta.get("name", ""))
        except (google.GoogleError, Unreadable) as error:
            checked = {"status": "NOT_READY", "deck_id": deck_id, "code": "deck_not_ready", "label": "Cannot read lesson", "problem": str(error)}
            failure = _registered_teacher_read_failure(error, binding)
            if failure:
                checked["read_failure"] = failure
        results.append(checked)
    if binding:
        try:
            _registered_teacher_bookend(token, binding, sources, bound_templates)
        except (google.GoogleError, Unreadable) as error:
            results.append({"status": "NOT_READY", "code": "deck_not_ready", "label": "Cannot read lesson",
                            "problem": str(error),
                            **({"read_failure": failure} if (failure := _registered_teacher_read_failure(error, binding)) else {})})
    elif ownership:
        try:
            _mapped_teacher_bookend(token, lesson, ownership, sources, bound_templates)
        except (google.GoogleError, Unreadable) as error:
            results.append({'status': 'NOT_READY', 'code': 'deck_not_ready', 'label': 'Cannot read lesson',
                            'problem': str(error)})
    failed = next((r for r in results if r["status"] != "READY"), None)
    return {**context_scope, "status": "NOT_READY" if failed else "READY", "code": "deck_not_ready" if failed else None,
            "label": failed.get("label", "Needs check") if failed else "Ready",
            "scope": "assignment", "context": context, "student_copies_checked": False, "linked_decks": results,
            "source_resolution": sources,
            "source_ownership": ownership,
            **({"read_failure": failed["read_failure"]} if failed and failed.get("read_failure") else {}),
            "problem": context + ": " + failed["problem"] if failed else None,
            "action": failed.get("action") if failed and failed.get("action") else
                      ("Check this exact linked lesson's access or readability, then recheck before rerunning grading.")
                      if failed and failed.get("label") == "Cannot read lesson" else
                      ("Repair or embed this lesson's completion codes, preserving content and student answers; "
                       "then recheck and rerun grading. No scores or missing-work zeros may be entered while it is held.") if failed else
                      "Linked completion codes are ready; every submitted student copy must also pass before entry."}


def submitted_readiness(token, form, *, require_complete_codes=True, include_late=False):
    """Fresh current copy checks; a final-entry recheck uses scored candidates.

    Original submission history remains intact. A historical file cannot replace
    the selected current file, and uncertain identity/history prevents recording.
    No completion arithmetic, source repairs or grading writes happen here.
    """
    from . import google
    checked, seen = [], set()
    if (form.get('combined_assessment') or {}).get('problem'):
        return {'status': 'NOT_READY', 'code': 'coverage_incomplete', 'copies': [],
                'problem': form['combined_assessment']['problem'],
                'action': 'Reload this combined assignment and confirm its original source components.'}
    candidates = (scoring_candidates(form, include_late=include_late) if form.get('combined_assessment') else
                  recheck_candidates(form) if include_late else form.get("students", []) + form.get("late", []))
    affected = next((row for row in candidates if row.get("selection_problem")), None)
    if affected:
        who = "%s, %s (%s)" % (affected.get("last", ""), affected.get("first", ""), affected.get("section", ""))
        return {"status": "NOT_READY", "code": "coverage_incomplete", "copies": [], "label": "Needs check",
                "problem": "%s · %s: %s" % (form.get("label") or "This assignment", who, affected["selection_problem"]),
                "action": "Confirm this student's current original Form submission before recording IC entry."}
    for row in candidates:
        if not row.get("matched"):
            continue
        if form.get('combined_assessment'):
            from . import combined_assessment
            try:
                _, captures = combined_assessment.verify_sources(token, form, row)
                checked.extend({'status': 'READY', 'file_id': capture['metadata']['id'],
                                'revision': capture['metadata']['version']} for capture in captures.values())
            except (combined_assessment.Held, google.GoogleError, Unreadable) as error:
                return {'status': 'NOT_READY', 'code': 'coverage_incomplete', 'copies': checked,
                        'problem': str(error), 'action': 'Reload this combined assignment and confirm its current components.'}
            continue
        for upload in row.get("files", []):
            if upload["id"] in seen:
                continue
            seen.add(upload["id"])
            try:
                meta, deck = _template(token, upload["id"])
                result = student_readiness(deck, revision=meta.get("version") or meta.get("modifiedTime", ""),
                                           require_complete_codes=require_complete_codes)
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


def scoring_candidates(form, *, include_late=False):
    if form.get('combined_assessment'):
        from . import combined_assessment
        return combined_assessment.candidates(form, include_late=include_late)
    return recheck_candidates(form) if include_late else form.get('students', [])


def recheck_candidates(form, *, now=None):
    """Derive one submitted candidate per matched student without changing history.

    Grouping prefers on-time work for the original deadline policy. A later
    replacement remains in ``earlier``; only its own verified Form event can
    establish ordering here. Drive creation/modification is never turn-in proof.
    """
    from . import google, work
    from datetime import datetime, timezone
    if form.get('combined_assessment'):
        from . import combined_assessment
        return combined_assessment.candidates(form, include_late=True)
    now = now or datetime.now(timezone.utc)

    def file_ids(snapshot):
        return frozenset(f.get("id") for f in snapshot.get("files", []) if f.get("id"))

    def event(snapshot):
        files = snapshot.get("files", [])
        at = work.when(snapshot.get("submitted_at"))
        method = snapshot.get("matched_by")
        if (snapshot.get("source") != "form" or not at
                or method not in ("email", "file name", "name typed in the Form")
                or (method == "email" and not snapshot.get("email"))):
            return None
        if any(not f.get("source_form_id") or not f.get("source_response_id")
               or work.when(f.get("source_submitted_at")) != at for f in files):
            return None
        if len({(f["source_form_id"], f["source_response_id"]) for f in files}) > 1:
            return None
        return at

    def older_routed_folder(current, snapshot):
        """Keep an older routed folder copy as history beside a verified Form.

        Its filename time is archive-routing evidence, not an authenticated Form
        event. It never selects or scores that copy. A current canonical upload
        can supersede it only with the same exact student username and a strictly
        earlier explicit routing stamp. Unstamped/newer/conflicting work holds.
        """
        at, prior = event(current), work.when(snapshot.get("submitted_at"))
        email = current.get("email", "")
        uploads, old_files = current.get("files", []), snapshot.get("files", [])
        if (current.get("matched") is not True or current.get("matched_by") != "email"
                or not isinstance(email, str) or email.count("@") != 1
                or not at or not prior or not prior < at <= now
                or len(uploads) != 1 or len(old_files) != 1
                or not form.get("form_id")
                or uploads[0].get("source_form_id") != form["form_id"]
                or not uploads[0].get("id")
                or uploads[0].get("mime") not in (SLIDES, google.PPTX)
                or uploads[0].get("from_folder") is not False or uploads[0].get("from_shared") is not False
                or uploads[0].get("trashed") is not False or uploads[0].get("other_week")
                or uploads[0].get("problem")
                or snapshot.get("source") != "folder"
                or snapshot.get("matched_by") != "file name" or snapshot.get("email")):
            return False
        old = old_files[0]
        if (not old.get("id") or old["id"] == uploads[0]["id"]
                or old.get("mime") not in (SLIDES, google.PPTX)
                or old.get("from_folder") is not True or old.get("from_shared") is not False
                or old.get("trashed") is not False or old.get("other_week") or old.get("problem")
                or any(old.get(key) for key in ("source_form_id", "source_response_id", "source_submitted_at"))):
            return False
        from . import roster
        name = old.get("name", "")
        username = roster.file_username(name)
        return bool(username and username.lower() == email.partition("@")[0].lower()
                    and re.match(r"\s*%s\s+[—-]\s*\d{2}-\d{2}\s+\d{2}\.\d{2}\s*[—-]\s*\S"
                                 % re.escape(username), name, re.I)
                    and work.when(work.name_time(name, snapshot["submitted_at"])) == prior)

    derived = []
    for row in form.get("students", []) + form.get("late", []):
        if row.get("matched") is not True:
            continue
        if not event(row):
            # Original grouping prefers on-time work, including a routed folder
            # copy. Explicit recheck may have the later canonical Form event in
            # history. Apply the same strictly older archive guard in this
            # direction; never turn a routing stamp into a Form submission.
            snapshots = [row] + row.get("earlier", [])
            verified = [c for c in snapshots if event(c)]
            if verified:
                newest = max(event(c) for c in verified)
                latest = [c for c in verified if event(c) == newest]
                selected = latest[0]
                current = {**row, **{key: selected[key] for key in
                    ("files", "source", "submitted_at", "late", "email", "matched_by") if key in selected}}
                canonical = all(c.get("matched_by") == "email" and c.get("email", "").lower() == current.get("email", "").lower()
                    and len(c.get("files", [])) == 1 and c["files"][0].get("source_form_id") == form.get("form_id")
                    for c in verified)
                if (canonical and len({file_ids(c) for c in latest}) == 1
                        and all(older_routed_folder(current, c) for c in snapshots if not event(c))):
                    row = {**current, "earlier": [{key: c[key] for key in c if key != "earlier"}
                        for c in snapshots if c is not selected]}
        candidates = [row]
        snapshots = [row] + row.get("earlier", [])
        problem = None
        current_ids = file_ids(row)
        for earlier in row.get("earlier", []):
            at = event(earlier)
            if not at:
                if file_ids(earlier) and file_ids(earlier) != current_ids:
                    if not older_routed_folder(row, earlier):
                        problem = "Submission history has files without a verified original Form event; confirm the current revision."
                continue
            if (earlier.get("matched_by") == "email" and row.get("matched_by") == "email"
                    and earlier.get("email", "").lower() != row.get("email", "").lower()):
                problem = "Submission history has conflicting student identities; confirm the current revision."
                continue
            candidates.append(earlier)
        selected = row
        if len(candidates) > 1:
            if not event(row) and current_ids:
                problem = "The current file's turn-in event cannot be ordered against its replacement; confirm the current revision."
            else:
                newest = max(event(c) for c in candidates if event(c))
                latest = [c for c in candidates if event(c) == newest]
                if len({file_ids(c) for c in latest}) != 1:
                    problem = "Different submissions have the same latest turn-in time; confirm which revision to score."
                else:
                    selected = latest[0]
        if any(event(c) and event(c) > now for c in candidates):
            problem = "A submission's original Form event is in the future; confirm its timestamp before scoring."
        files = selected.get("files", [])
        if not files and any(file_ids(c) for c in snapshots):
            problem = "The latest Form submission has no uploaded file; confirm the intended replacement before scoring."
        if any(f.get("trashed") or f.get("other_week") for f in files):
            problem = "The selected submission is unavailable or belongs to another lesson; confirm its revision."
        decks = {f["id"] for f in files if f.get("mime") in (SLIDES, google.PPTX)}
        if len(decks) > 1:
            problem = "The selected submission contains several decks; confirm which revision to score."
        value = {**row, **{key: selected[key] for key in
                 ("files", "source", "submitted_at", "late", "email", "matched_by") if key in selected}}
        if selected is not row:
            value["earlier"] = [{key: c[key] for key in c if key != "earlier"}
                for c in snapshots if c is not selected]
        if problem:
            value["selection_problem"] = problem
        derived.append(value)
    owners = {}
    for row in derived:
        identity = (row.get("section"), work.norm(row.get("last", "")), work.norm(row.get("first", "")))
        for fid in file_ids(row):
            owners.setdefault(fid, []).append((identity, row))
    for entries in owners.values():
        if len({identity for identity, _ in entries}) > 1:
            for _, row in entries:
                row["selection_problem"] = "The same submitted file is attributed to different students; confirm its identity."
    return derived


class _StudentContentHold(dict):
    """In-process source proof only; serialized API/cache data is not authority."""


def _student_content_identity(row, upload, form_id):
    """Use the existing verified email/Form match, never a filename identity."""
    from . import work
    if (row.get('matched') is not True or row.get('matched_by') != 'email'
            or row.get('source') != 'form' or row.get('selection_problem')
            or not all(isinstance(row.get(k), str) and row[k].strip() for k in ('first', 'last', 'section', 'email'))
            or '@' not in row['email'] or len(row.get('files', [])) != 1
            or not upload.get('source_form_id') or not upload.get('source_response_id')
            or not work.when(upload.get('source_submitted_at'))
            or upload.get('source_submitted_at') != row.get('submitted_at')
            or (upload['source_form_id'] != form_id and upload.get('assignment_form_id') != form_id)
            or any(upload.get(k) for k in ('trashed', 'other_week'))):
        return None
    return {k: row[k] for k in ('first', 'last', 'section', 'email', 'matched_by', 'source', 'submitted_at')}


def _student_content_candidate(row, upload, form_id, meta, deck, check, complete_codes):
    # Multiple cycles/wrong lesson and missing coding under the Photosynthesis
    # repair-first policy remain assignment holds. Ordinary missing codes are
    # already advisory in student_readiness; only real ambiguity is local.
    allowed = {'duplicate_feedback_marker', 'marker_inventory_missing_or_duplicated',
               'marker_inventory_not_on_first_slide', 'marker_inventory_counts_unparsed',
               'marker_inventory_count_mismatch', 'marker_inventory_id_mismatch', 'draft_without_matching_notes'}
    issues = set(check.get('issues') or [])
    return (not complete_codes and 'duplicate_feedback_marker' in issues and issues <= allowed
            and _student_content_identity(row, upload, form_id) is not None
            and meta.get('id') == upload['id'] and meta.get('trashed') is False
            and meta.get('version') and meta.get('modifiedTime')
            and meta.get('mimeType') == upload.get('mime')
            and 0 < len(deck.get('slides', [])) < MAX_SLIDES)


def is_student_content_hold(row, result, form_id):
    """Only a fresh producer's exact unchanged row/source proof is local."""
    proof = result.get('_student_content_hold')
    if type(proof) is not _StudentContentHold or result.get('code') != 'deck_not_ready':
        return False
    uploads = [u for u in row.get('files', []) if u.get('id') == result.get('file_id')]
    if len(uploads) != 1:
        return False
    upload = uploads[0]
    return (proof.get('cause') == 'ambiguous_student_response_codes' and proof.get('form_id') == form_id
            and proof.get('identity') == _student_content_identity(row, upload, form_id)
            and proof.get('identity') is not None
            and proof.get('source_event') == {k: upload.get(k) for k in
                ('source_form_id', 'source_response_id', 'source_submitted_at', 'assignment_form_id')}
            and proof.get('file_id') == upload['id'] and proof.get('version') == result.get('file_version')
            and proof.get('modified_at') == result.get('file_updated')
            and bool(proof.get('version')) and bool(proof.get('modified_at'))
            and all(result.get(k) is None for k in ('notes_score', 'revision_score')))


def score_file(token, agenda, class_key, week_label, form_id, file_id, work_result, *, include_late=False, component_only=False):
    """Score a current file; late candidates require an explicit entry recheck."""
    from . import google
    form = next((f for lesson in work_result.get("lessons", []) for f in lesson["forms"]
                 if f["form_id"] == form_id), None)
    policy = (form or {}).get("grade_policy") or work_result.get("week_policy") or {}
    if policy.get("problem") or policy.get("excluded"):
        return {"file_id": file_id, "code": "coverage_incomplete" if policy.get("problem") else "excluded_week",
                "problem": policy.get("problem") or policy.get("reason")}
    candidates = scoring_candidates(form or {}, include_late=include_late)
    # A held candidate also protects its recorded historical originals. This
    # reports the exact uncertainty without authorizing an unselected old copy
    # or suppressing an unrelated student's verified current source.
    affected = next((r for r in candidates if r.get("selection_problem")
                     and any(f["id"] == file_id for snapshot in [r] + r.get("earlier", [])
                             for f in snapshot.get("files", []))), None)
    if affected:
        label = (form or {}).get("label") or next((lesson.get("topic") for lesson in work_result.get("lessons", [])
                                                  if form in lesson["forms"]), "this assignment")
        context = "%s · %s · %s · %s, %s (%s)" % (
            agenda.classes.get(class_key, {}).get("name", class_key), week_label, label,
            affected["last"], affected.get("first", ""), affected["section"])
        return {"file_id": file_id, "code": "coverage_incomplete", "problem": context + ": " + affected["selection_problem"]}
    row = next((r for r in candidates if any(f["id"] == file_id for f in r["files"])), None)
    if not form or not row:
        raise ValueError("file")
    upload = next(f for f in row["files"] if f["id"] == file_id)
    out = {"file_id": file_id, "problem": None}

    def not_ready(check, name, kind):
        lesson = next((l for l in work_result.get("lessons", []) if form in l["forms"]), {})
        class_name = agenda.classes.get(class_key, {}).get("name", class_key)
        context = "%s · %s · %s" % (class_name, week_label, form.get("label") or lesson.get("topic") or "this assignment")
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
    if form.get('combined_assessment'):
        from . import combined_assessment
        return {**out, **combined_assessment.score_row(token, form, row,
                            component_file_id=file_id if component_only else None)}
    if upload.get("mime") not in (SLIDES, google.PPTX):
        return {**out, "problem": "Only Google Slides or PowerPoint decks can be scored. Open it yourself."}
    templates = []
    eligible = (assignment_check or {}).get("source_resolution")
    binding = form.get('teacher_source_binding')
    ownership = (assignment_check or {}).get('source_ownership')
    try:
        lesson = form_source_scope(agenda, class_key, week_label, form_id, binding=binding, ownership=ownership)
    except Unreadable as error:
        return {**out, 'code': 'deck_not_ready', 'label': 'Needs assignment setup', 'problem': str(error)}
    if binding or ownership or eligible is not None:
        fresh = scoped_lesson_sources(token, lesson, binding=binding, ownership=ownership)
        changed = False
        if eligible is not None:
            changed = fresh != eligible
            if binding:
                # Old readers may omit metadata in readiness. The exact
                # declared collection is freshly read/bookended below; stale
                # or sibling source IDs still cannot authorize a source.
                changed = ({d['id'] for d in fresh.get('decks', [])} !=
                           {d['id'] for d in eligible.get('decks', [])})
        if changed:
            return {**out, 'code': 'deck_not_ready', 'label': 'Needs check',
                    'problem': 'This exact Form teacher-source resolution changed. Refresh this assignment before scoring.'}
        eligible = fresh
        if eligible.get("problem"):
            return {**out, "code": "deck_not_ready", "label": "Needs check", "problem": eligible["problem"]}
    deck_ids = [d["id"] for d in eligible["decks"]] if eligible else lesson_decks(
        agenda, class_key, week_label, form_id, binding=binding, ownership=ownership)
    for deck_id in deck_ids:
        try:
            templates.append((deck_id,) + _teacher_template(token, deck_id))
        except (google.GoogleError, Unreadable):
            continue
    try:
        meta = google.drive_file(token, file_id, google.FILE_FIELDS + ",version")
        out["file_updated"] = meta.get("modifiedTime", "")
        # Receipt evidence from this same read, not another assessment or a new
        # eligibility requirement. Unknown metadata stays unknown.
        out["source_observation"] = {
            "file_id": file_id, "version": str(meta["version"]) if meta.get("version") else None,
            "modified_at": meta.get("modifiedTime") or None,
            **{key: upload.get(key) or None for key in
               ("source_form_id", "source_response_id", "source_submitted_at")},
        }
        deck = _read(token, file_id, meta)
    except (google.GoogleError, Unreadable) as error:
        return {**out, "code": "deck_not_ready", "label": "Cannot read lesson",
                "problem": "Couldn't read this exact submitted deck (%s)." % error,
                "action": "Check this file's access or readability, then recheck before rerunning grading."}
    complete_codes = student_coding_required(agenda, class_key, week_label, form_id)
    check = student_readiness(deck, revision=meta.get("version") or meta.get("modifiedTime", ""),
                              require_complete_codes=complete_codes)
    student_check = check
    local_candidate = _student_content_candidate(row, upload, form_id, meta, deck, check, complete_codes)
    if check["status"] != "READY" and not local_candidate:
        return not_ready(check, upload.get("name") or meta.get("name", ""), "student copy")
    best = pick_template(deck, [t[2] for t in templates])
    # A correctly selected copy with no codes has no grade denominator. Keep
    # the exact linked teacher/source checks; do not borrow its requirements.
    has_codes = bool(markers(deck))
    if (not complete_codes and not has_codes and len(templates) == 1
            and (not inventory(deck)[0] or inventory(deck)[0] == inventory(templates[0][2])[0])):
        best = templates[0][2]
    if has_codes and coverage(deck, best) < 0.8 and not binding and not ownership and not local_candidate:
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
                    templates.append((item["id"],) + _teacher_template(token, item["id"]))
                except (google.GoogleError, Unreadable):
                    continue
        best = pick_template(deck, [t[2] for t in templates])
    if (binding or ownership) and has_codes and coverage(deck, best) < 0.8:
        return {**out, "code": "deck_not_ready", "label": "Needs check",
                "problem": "This submitted copy's own coded questions do not match the exact registered teacher sources."}
    if (binding or ownership) and best is not None:
        share = coverage(deck, best)
        same = [t for t in templates if inventory(t[2])[0] == inventory(best)[0] and coverage(deck, t[2]) == share]
        if len(same) != 1:
            return {**out, "code": "deck_not_ready", "label": "Needs check",
                    "problem": "This submitted copy does not uniquely match one exact registered teacher source."}
    chosen = next((t for t in templates if t[2] is best), None)
    if chosen is None:
        return {**out, **({'code': 'deck_not_ready'} if local_candidate else {}),
                "problem": "No handed-out copy of this deck is linked in the lesson's Agenda row "
                                  "(Student copy or Teacher deck), so its boxes can't be checked."}
    check = teacher_readiness(chosen[2], revision=chosen[1].get("version") or chosen[1].get("modifiedTime", ""),
                             require_complete_codes=complete_codes)
    if check["status"] != "READY":
        return not_ready(check, chosen[1].get("name", ""), "handed-out deck")
    if form.get("teacher_source_binding"):
        try:
            _registered_teacher_bookend(token, form["teacher_source_binding"], eligible, templates)
        except (google.GoogleError, Unreadable) as error:
            return {**out, "code": "deck_not_ready", "label": "Cannot read lesson", "problem": str(error),
                    "action": "Recheck the exact registered teacher sources; no scores were calculated."}
    elif ownership:
        try:
            _mapped_teacher_bookend(token, lesson, ownership, eligible, templates)
        except (google.GoogleError, Unreadable) as error:
            return {**out, 'code': 'deck_not_ready', 'label': 'Cannot read lesson', 'problem': str(error),
                    'action': 'Refresh this exact Form teacher source; no scores were calculated.'}
    if local_candidate:
        # Readiness failed on this original's ambiguous codes. Only after all
        # teacher/source checks and final metadata bookends can other students
        # proceed. Failed reads never create a local proof or a zero.
        result = not_ready(student_check, upload.get('name') or meta.get('name', ''), 'student copy')
        same = [t for t in templates if inventory(t[2])[0] == inventory(deck)[0]
                and coverage(deck, t[2]) == coverage(deck, chosen[2])]
        if (len(templates) != len(deck_ids) or len(set(deck_ids)) != len(deck_ids)
                or {item[0] for item in templates} != set(deck_ids) or chosen[0] not in deck_ids
                or len(same) != 1 or coverage(deck, chosen[2]) < 0.8):
            return result
        try:
            if (any(google.drive_file(token, tid, google.FILE_FIELDS + ',version') != tmeta
                    for tid, tmeta, _ in templates)
                    or google.drive_file(token, file_id, google.FILE_FIELDS + ',version') != meta):
                return {**out, 'code': 'deck_not_ready', 'label': 'Cannot verify source',
                        'problem': 'The exact student or teacher source changed during readiness verification.'}
        except (google.GoogleError, Unreadable) as error:
            return {**out, 'code': 'deck_not_ready', 'label': 'Cannot read lesson',
                    'problem': 'The final student/teacher source read was unavailable: %s' % error}
        proof = _StudentContentHold(cause='ambiguous_student_response_codes', form_id=form_id,
            identity=_student_content_identity(row, upload, form_id), file_id=file_id,
            version=str(meta['version']), modified_at=meta['modifiedTime'],
            source_event={k: upload.get(k) for k in
                ('source_form_id', 'source_response_id', 'source_submitted_at', 'assignment_form_id')})
        return {**result, 'notes_score': None, 'revision_score': None,
                'file_version': str(meta['version']), '_student_content_hold': proof}
    source_key = (file_id, meta.get("modifiedTime", ""), str(meta.get("version") or ""))
    template_key = (chosen[0], chosen[1].get("modifiedTime", ""), str(chosen[1].get("version") or ""))
    cached = _results.get(source_key)
    if cached and cached["template"] == template_key and cached.get("student_coding_required") == complete_codes:
        return {**out, **cached["result"]}
    assessed = assess(deck, chosen[2], found_codes_only=not complete_codes)
    result = {**assessed,
              "missing_code_warning": " ".join(student_check.get("warnings") or []) or assessed.get("missing_code_warning"),
              "template": chosen[1].get("name", ""),
              "template_id": chosen[0], "template_updated": chosen[1].get("modifiedTime", "")}
    _remember(_results, source_key, {"template": template_key, "student_coding_required": complete_codes,
                                   "result": result}, MAX_CACHED_RESULTS)
    return {**out, **result}
