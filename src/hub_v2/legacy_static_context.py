"""Private raw-PPTX proof for one bounded coincident static-label layout.

Both original objects survive in the census. This never turns a coincident
object into a response, and a parsed/native/style-summary lookalike is no proof.
"""
from copy import deepcopy
import hashlib
import json
import posixpath
import re
import xml.etree.ElementTree as E
import zipfile
from typing import NamedTuple

from school_workflow_hub.core.markers import MARKER
from . import explicit_response_binding as explicit, response_binding as rb, writing_fields

P, A = explicit.P, explicit.A
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _snapshot(page):
    return _digest(page)


class _ContextPage(dict):
    """Reader-only proof lives outside JSON/source hashes and stored records."""


class TableCell(dict):
    """Ephemeral role from a complete unmerged table reader, never a JSON flag."""


def is_table_cell(unit):
    return type(unit) is TableCell


def _canonical(node, *, paint=False):
    attrs = dict(node.attrib)
    if node.tag == P + "cNvPr":
        attrs.pop("id", None)
        attrs.pop("name", None)  # aliases are not rendering or source authority
    text = (node.text or "") if node.tag == A + "t" else (node.text or "").strip()
    if paint and node.tag == A + "t":
        text = "response text"
    return [node.tag, sorted(attrs.items()), text, [_canonical(c, paint=paint) for c in node]]


def _groups(page):
    """Only exact two-object empty-behind-known-label groups are candidates."""
    groups = []
    units = page["units"]
    for index, label in enumerate(units):
        codes = list(MARKER.finditer(label["text"]))
        if len(codes) != 1:
            continue
        plain = MARKER.sub("", label["text"]).strip()
        match = re.fullmatch(r"(?i)(?:your )?(first|revised) answer", plain)
        if not match or codes[0].group(1) != ("NOTES" if match[1].lower() == "first" else "DRAFT"):
            continue
        same = [i for i, u in enumerate(units) if u.get("box") == label.get("box")]
        if len(same) == 2 and index > 0 and same == [index - 1, index] and not units[index - 1]["text"].strip():
            groups.append((index - 1, index))
    return groups


def _resources(archive):
    # Unresolved or modified inherited resources cannot authorize this exception.
    if writing_fields.style_resource_roles(archive) is None:
        raise ValueError("unverified style resources")
    rows, total = [], 0
    names = archive.namelist()
    if len(names) != len(set(names)):
        raise ValueError("duplicate package part")
    for name in sorted(names):
        if name.endswith("/") or name.startswith("docProps/") or re.fullmatch(r"ppt/slides/slide\d+\.xml", name):
            continue
        if not re.fullmatch(r"(?:\[Content_Types\]\.xml|(?:_rels|ppt)/[A-Za-z0-9_./-]+\.(?:xml|rels|png|jpg|jpeg|gif))", name):
            raise ValueError("unsupported package resource")
        if posixpath.normpath(name) != name:
            raise ValueError("ambiguous resource path")
        length = archive.getinfo(name).file_size
        total += length
        if length > 5_000_000 or total > 30_000_000:
            raise ValueError("oversized render context")
        raw = archive.read(name)
        if name.endswith((".xml", ".rels")):
            root = E.fromstring(raw)
            if any(node.tag in (P + "timing", P + "transition", P + "embeddedFontLst", A + "fld") for node in root.iter()):
                raise ValueError("unsupported render branch")
            if name.endswith(".rels"):
                if root.tag != REL + "Relationships":
                    raise ValueError("unsupported relationships")
                if any(node.get("TargetMode") == "External" and not node.get("Type", "").endswith("/hyperlink") for node in root):
                    raise ValueError("external render resource")
                safe = {"officeDocument", "core-properties", "extended-properties", "slide", "slideMaster", "slideLayout",
                        "notesMaster", "notesSlide", "theme", "presProps", "viewProps", "tableStyles", "image", "hyperlink", "thumbnail"}
                if any(node.get("Type", "").rsplit("/", 1)[-1] not in safe for node in root):
                    raise ValueError("unknown render relationship")
                owner = re.fullmatch(r"(.*/)?_rels/([^/]+)\.rels", name)
                base = posixpath.dirname((owner[1] or "") + owner[2]) if owner else ""
                for node in root:
                    mode, target = node.get("TargetMode"), node.get("Target", "")
                    if (mode not in (None, "Internal", "External") or not target
                            or (mode != "External" and posixpath.normpath(posixpath.join(base, target)) not in names)):
                        raise ValueError("missing or unresolved render resource")
        rows.append((name, hashlib.sha256(raw).hexdigest()))
    return _digest(rows)


def _card(node, rules):
    return (node is not None and not any(c.tag not in rules for c in node)
            and all(low <= len(node.findall(tag)) <= high for tag, (low, high) in rules.items())
            and [list(rules).index(c.tag) for c in node] == sorted(list(rules).index(c.tag) for c in node))


def _layout(tree):
    """A bounded, source-side rendering refusal, separate from other contracts."""
    if tree.attrib or not _card(tree, {P + "nvGrpSpPr": (0, 1), P + "grpSpPr": (0, 1), P + "sp": (1, 1000)}):
        return False
    metadata = tree.find(P + "nvGrpSpPr")
    if metadata is not None:
        if metadata.attrib or not _card(metadata, {P + "cNvPr": (1, 1), P + "cNvGrpSpPr": (0, 1), P + "nvPr": (0, 1)}):
            return False
        if any(node.get("hidden", "0").lower() not in ("0", "false") for node in metadata):
            return False
        if any(len(node) or set(node.attrib) - {"id", "name", "descr", "title", "hidden"} for node in metadata):
            return False
    group = tree.find(P + "grpSpPr")
    if group is not None:
        if not _card(group, {A + "xfrm": (0, 1)}): return False
        # The ordinary reader does not compose a root-tree transform.
        for node in group.iter():
            if node is not group and (any(not re.fullmatch(r"0|false", str(v)) for v in node.attrib.values())
                                      or node.tag not in {A + n for n in ("xfrm", "off", "ext", "chOff", "chExt")}):
                return False
    for shape in tree.findall(P + "sp"):
        if not _card(shape, {P + "nvSpPr": (1, 1), P + "spPr": (1, 1), P + "txBody": (1, 1)}): return False
        nv = shape.find(P + "nvSpPr")
        if nv.attrib or not _card(nv, {P + "cNvPr": (1, 1), P + "cNvSpPr": (0, 1), P + "nvPr": (0, 1)}): return False
        props = nv.find(P + "cNvPr")
        if (len(props) or set(props.attrib) - {"id", "name", "descr", "title", "hidden"}
                or props.get("hidden", "0").lower() not in ("0", "false")):
            return False
        nvpr = nv.find(P + "nvPr")
        if nvpr is not None and (len(nvpr) or nvpr.attrib): return False
        visual = shape.find(P + "spPr")
        if visual.attrib or not _card(visual, {A + "xfrm": (1, 1), A + "prstGeom": (0, 1), A + "solidFill": (1, 1), A + "ln": (0, 1)}): return False
        transform = visual.find(A + "xfrm")
        if (not _card(transform, {A + "off": (1, 1), A + "ext": (1, 1)})
                or set(transform.attrib) - {"rot", "flipH", "flipV"}
                or set(transform[0].attrib) != {"x", "y"} or set(transform[1].attrib) != {"cx", "cy"}): return False
        outline = visual.find(A + "ln")
        if outline is not None and (not _card(outline, {A + "noFill": (1, 1)}) or set(outline.attrib) - {"w"}
                                    or outline.get("w", "0") != "0"):
            return False
        geometry = visual.find(A + "prstGeom")
        if geometry is not None and (geometry.attrib != {"prst": "rect"}
                or not _card(geometry, {A + "avLst": (0, 1)}) or any(len(c) or c.attrib for c in geometry)):
            return False
        body = shape.find(P + "txBody")
        if not _card(body, {A + "bodyPr": (1, 1), A + "lstStyle": (0, 1), A + "p": (1, 1000)}): return False
        settings = body.find(A + "bodyPr")
        if (not _card(settings, {A + "noAutofit": (1, 1)})
                or set(settings.attrib) - {"lIns", "rIns", "tIns", "bIns", "wrap", "anchor", "horzOverflow", "vertOverflow"}
                or settings.get("anchor", "t") != "t" or settings.get("wrap", "square") not in ("square", "none")
                or any(settings.get(k, "overflow") != "overflow" for k in ("horzOverflow", "vertOverflow"))):
            return False
        dims = transform.find(A + "ext")
        try:
            width, height = int(dims.get("cx", 0)), int(dims.get("cy", 0))
            insets = [int(settings.get(k, default)) for k, default in
                      (("lIns", "91440"), ("rIns", "91440"), ("tIns", "45720"), ("bIns", "45720"))]
            if (width <= 0 or height <= 0 or any(v < 0 for v in insets)
                    or any(v > .25 * dim for v, dim in zip(insets, (width, width, height, height)))): return False
        except (ValueError, TypeError): return False
        listing = body.find(A + "lstStyle")
        if listing is not None and (listing.attrib or len(listing)): return False
        for paragraph in body.findall(A + "p"):
            if paragraph.attrib or not _card(paragraph, {A + "pPr": (0, 1), A + "r": (0, 1000), A + "endParaRPr": (0, 1)}): return False
            props = paragraph.find(A + "pPr")
            if props is not None:
                safe = {"marL": "0", "marR": "0", "indent": "0", "lvl": "0", "algn": "l", "fontAlgn": "base"}
                if any(safe.get(k) != v for k, v in props.attrib.items()): return False
                if not _card(props, {A + n: (0, 1) for n in ("lnSpc", "spcBef", "spcAft", "buNone", "defRPr")}): return False
                for key in ("lnSpc", "spcBef", "spcAft"):
                    node = props.find(A + key)
                    child, value = ("spcPct", "100000") if key == "lnSpc" else ("spcPts", "0")
                    if node is not None and (node.attrib or len(node) != 1 or node[0].tag != A + child
                                              or node[0].attrib != {"val": value} or len(node[0])): return False
            for run in paragraph.findall(A + "r"):
                if run.attrib or not _card(run, {A + "rPr": (1, 1), A + "t": (1, 1)}): return False
            for face in paragraph.iter():
                if face.tag in (A + "latin", A + "ea", A + "cs") and (set(face.attrib) != {"typeface"} or len(face)):
                    return False
    return True


class _Proof(NamedTuple):
    snapshot: str
    paint: str
    resources: str
    nodes: tuple


def capture(page, tree, root, archive, name):
    """Only the raw reader can issue complete current-page proof; no writes."""
    if not _groups(page) or any("binding" in u for u in page["units"]):
        return page
    inspected = deepcopy(page)
    explicit.attach_pptx(inspected, tree, root, name)
    try:
        if (not _layout(tree) or inspected.get("explicit_invalid") or len(tree.findall(P + "sp")) != len(page["units"])
                or any(node.tag == A + "fld" for node in root.iter())
                or any(u.get("panel_geometry_unverified") or not u.get("explicit_surface", {}).get("visible")
                       for u in inspected["units"])):
            return page
        resources = _resources(archive)
        result = _ContextPage(page)
        result._legacy_context_proof = _Proof(_snapshot(page), _digest(_canonical(root, paint=True)), resources,
            tuple(_digest(_canonical(node)) for node in tree.findall(P + "sp")))
        return result
    except (ValueError, TypeError, KeyError, OSError, zipfile.BadZipFile, E.ParseError):
        return page  # missing render facts retain the existing hold


def plan(original, student, size, placeholder):
    """Return proven static member indices, or None for an unverified group."""
    groups = _groups(original)
    if not groups:
        return frozenset()
    a, b = (getattr(p, "_legacy_context_proof", None) for p in (original, student))
    if (type(a) is not _Proof or type(b) is not _Proof or a.snapshot != _snapshot(original)
            or b.snapshot != _snapshot(student) or a.paint != b.paint or a.resources != b.resources
            or _groups(student) != groups or len(groups) > 2):
        return None
    members = frozenset(i for group in groups for i in group)
    if any(a.nodes[i] != b.nodes[i] for i in members):
        return None
    units = original["units"]
    labels = []
    for i, u in enumerate(units):
        codes = list(MARKER.finditer(u["text"]))
        text = MARKER.sub("", u["text"]).strip()
        if re.fullmatch(r"(?i)(?:your )?(?:first|revised) answer", text) and len(codes) == 1:
            labels.append((i, codes[0].groups()))
    all_codes = [m.groups() for u in units for m in MARKER.finditer(u["text"])]
    if (len(labels) != 2 or len(all_codes) != 2 or {c for _, (c, _, _) in labels} != {"NOTES", "DRAFT"}
            or len({(cycle, ident) for _, (_, cycle, ident) in labels}) != 1):
        return None
    fields = [i for i, u in enumerate(units) if i not in members and not MARKER.search(u["text"])
              and (not u["text"].strip() or placeholder(u["text"])) and not u.get("picture")]
    if len(fields) != 2 or not all(rb.rectangle(u.get("box"), size) for u in units):
        return None
    targets = []
    for index, (category, _, _) in labels:
        label = units[index]["box"]
        expected = "first" if category == "NOTES" else "revised"
        if not re.fullmatch(r"(?i)(?:your )?" + expected + r" answer", MARKER.sub("", units[index]["text"]).strip()):
            return None
        below = [j for j in fields if (units[j]["box"][1] >= label[1] + label[3]
                 and units[j]["box"][1] - label[1] - label[3] <= .1 * size[1]
                 and max(units[j]["box"][0], label[0]) < min(units[j]["box"][0] + units[j]["box"][2], label[0] + label[2]))]
        if len(below) != 1:
            return None
        targets.append(below[0])
    if len(set(targets)) != 2 or rb._intersects(units[targets[0]]["box"], units[targets[1]]["box"]):
        return None
    for j in targets:
        box = units[j]["box"]
        if (sum(u.get("box") == box for u in units) != 1 or sum(u.get("box") == box for u in student["units"]) != 1
                or any(rb._intersects(box, u["box"]) for i, u in enumerate(units) if i != j)):
            return None
    return members
