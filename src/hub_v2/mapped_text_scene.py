"""Parser-owned immutable scene evidence for prospective text-target maps.

No URL, object name, supplied digest or serialized dictionary is render proof.
The supported profile is deliberately narrower than the slide file formats.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import io
import json
import math
import posixpath
import re
import xml.etree.ElementTree as E

from . import response_binding as rb

P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"
KIND = "original-text-targets/v1"
REVIEW = "Mapped text scene needs review"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def integrity(value):
    """Bind JSON plus parser-owned raw evidence, which list JSON omits."""
    pages = value.get("slides", [value])
    proofs = []
    for page in pages:
        units = page.get("units")
        if type(units) is SceneUnits:
            proof = getattr(units, "_mapped_proof", None)
            need(type(proof) is _Proof)
            proofs.append([page["number"], proof.snapshot, hashlib.sha256(proof.xml).hexdigest(),
                           proof.resources, proof.refs_json, proof.media_json, proof.raw_sha256])
    return digest([value, proofs])


def need(condition):
    if not condition:
        raise ValueError(REVIEW)


def children(node, required=(), optional=(), repeated=()):
    """Admit a bounded XML grammar, never a .find()-selected duplicate branch."""
    tags = [c.tag for c in node]
    need(all(t in required + optional + repeated for t in tags))
    need(all(tags.count(t) == 1 for t in required))
    need(all(tags.count(t) <= 1 for t in optional))


def _validate_transform(node, *, group=False):
    need(node is not None and not node.attrib)
    tags = ("off", "ext", "chOff", "chExt") if group else ("off", "ext")
    need([c.tag for c in node] == [A + t for t in tags])
    for item, tag in zip(node, tags):
        fields = {"x", "y"} if tag.endswith("Off") or tag == "off" else {"cx", "cy"}
        need(set(item.attrib) == fields and not len(item))
        need(all(re.fullmatch(r"-?\d+", v) for v in item.attrib.values()))
        if fields == {"cx", "cy"}: need(all(int(v) > 0 for v in item.attrib.values()))


def nonvisual(node, *, picture=False, group=False):
    middle = "cNvGrpSpPr" if group else "cNvPicPr" if picture else "cNvSpPr"
    need(node is not None and not node.attrib)
    need([c.tag for c in node] == [P + "cNvPr", P + middle, P + "nvPr"])
    nv, detail, properties = node
    need(set(nv.attrib) <= {"id", "name", "descr", "title", "hidden"})
    need(nv.get("hidden", "0") in ("0", "false") and not len(nv))
    need(set(detail.attrib) <= ({"txBox"} if middle == "cNvSpPr" else set()) and not len(detail))
    need(all(v in ("0", "1", "false", "true") for v in detail.attrib.values()))
    need(not properties.attrib and not len(properties))
    return nv


def xml_bounds(root):
    """Reject deep/oversized structure before any recursive canonicalizer."""
    stack, count = [(root, 0)], 0
    while stack:
        node, depth = stack.pop(); count += 1
        need(depth <= 32 and count <= 20000)
        stack.extend((child, depth + 1) for child in node)


def box(value):
    need(isinstance(value, (list, tuple)) and len(value) == 4)
    need(all(type(v) in (int, float) and math.isfinite(v) and abs(v - round(v)) <= .01 for v in value))
    return [round(v) for v in value]


def _binding(value):
    if not isinstance(value, str) or not value.startswith(rb.HEADER):
        return False
    try:
        return json.loads(value[len(rb.HEADER):]).get("kind") == KIND
    except (ValueError, AttributeError, RecursionError):
        return False


def _xml(node, refs=None, *, intrinsic=False):
    attrs = dict(node.attrib)
    if node.tag == P + "cNvPr":
        attrs.pop("id", None); attrs.pop("name", None)
        if "descr" in attrs and (not attrs["descr"] or _binding(attrs["descr"])): attrs.pop("descr")
    if node.tag == P + "sldId": attrs.pop("id", None)
    for key, value in list(attrs.items()):
        if key.startswith(R):
            need(intrinsic or refs is not None and value in refs)
            attrs[key] = "relationship" if intrinsic else refs[value]
    text = node.text or ""
    if node.tag != A + "t" and not text.strip(): text = ""
    return [node.tag, sorted(attrs.items()), text, [_xml(c, refs, intrinsic=intrinsic) for c in node]]


def _rels(archive, part):
    name = posixpath.dirname(part) + "/_rels/" + posixpath.basename(part) + ".rels" if part else "_rels/.rels"
    if name not in archive.namelist(): return {}
    root = E.fromstring(archive.read(name)); need(root.tag == REL + "Relationships")
    need(not root.attrib)
    result = {}
    for row in root:
        need(row.tag == REL + "Relationship" and not len(row) and set(row.attrib) <= {"Id", "Type", "Target", "TargetMode"})
        ident, target = row.get("Id"), row.get("Target", "")
        office = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
        allowed = {office + k for k in ("slide", "slideLayout", "slideMaster", "theme", "image", "notesSlide", "notesMaster",
                   "presProps", "viewProps", "tableStyles", "officeDocument", "extended-properties")}
        allowed.add("http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties")
        need(row.get("Type") in allowed)
        need(ident and ident not in result and row.get("TargetMode") in (None, "Internal") and target)
        target = posixpath.normpath(posixpath.join(posixpath.dirname(part), target))
        need(target in archive.namelist() and not target.startswith("../"))
        result[ident] = (row.get("Type", ""), target)
    return result


def _package(archive, part):
    from . import legacy_static_context as legacy
    legacy._graph(archive)  # actual main part, complete slide graph and order
    names = archive.namelist(); need(len(names) == len(set(names)) and len(names) <= 2000)
    types = E.fromstring(archive.read("[Content_Types].xml")); xml_bounds(types)
    need(types.tag == CT + "Types" and not types.attrib)
    for row in types:
        need(not len(row) and ((row.tag == CT + "Default" and set(row.attrib) == {"Extension", "ContentType"}) or
                              (row.tag == CT + "Override" and set(row.attrib) == {"PartName", "ContentType"})))
    defaults = {r.get("Extension"): r.get("ContentType") for r in types if r.tag == CT + "Default"}
    overrides = {r.get("PartName"): r.get("ContentType") for r in types if r.tag == CT + "Override"}
    content_types = {name: overrides.get("/" + name, defaults.get(name.rsplit(".", 1)[-1])) for name in names}
    payloads, trees, relations = {}, {}, {}
    total = 0
    for name in names:
        need(posixpath.normpath(name) == name and not name.startswith("/"))
        size = archive.getinfo(name).file_size; total += size
        need(size <= 5_000_000 and total <= 60_000_000)
        if name.endswith("/") or name.startswith("docProps/") or name.endswith(".rels") or name == "[Content_Types].xml": continue
        need(re.fullmatch(r"ppt/[A-Za-z0-9_./-]+\.(?:xml|png|jpg|jpeg)", name))
        raw = archive.read(name)
        if name.endswith(".xml"):
            root = E.fromstring(raw)
            xml_bounds(root)
            need(not any(n.tag in (P + "timing", P + "transition", P + "embeddedFontLst", A + "fld",
                                  A + "hlinkClick", A + "hlinkMouseOver") for n in root.iter()))
            # Fingerprinting inherited defaults is not proof of their initial
            # rendering. This version does not resolve presentation, master,
            # layout or theme object defaults; retain a hold instead.
            defaults = {P + "defaultTextStyle", P + "txStyles", A + "objectDefaults"}
            need(not any(n.tag in defaults and (n.attrib or len(n)) for n in root.iter()))
            if root.tag in (P + "sldMaster", P + "sldLayout"):
                need(not any(n.tag in (P + "sp", P + "pic", P + "grpSp", P + "graphicFrame") for n in root.iter()))
                need(not any(n.tag in (A + "bodyPr", A + "pPr", A + "defRPr", A + "lstStyle") and
                             (n.attrib or len(n)) for n in root.iter()))
            trees[name] = root
            payloads[name] = digest(_xml(root, intrinsic=True))
        else:
            from PIL import Image
            try:
                with Image.open(io.BytesIO(raw)) as image:
                    need(image.format in ("PNG", "JPEG") and image.width * image.height <= 20_000_000)
                    need(not getattr(image, "is_animated", False) and getattr(image, "n_frames", 1) == 1)
                    need(content_types[name] == ("image/png" if image.format == "PNG" else "image/jpeg"))
                    image.verify()
            except Image.DecompressionBombError:
                raise ValueError(REVIEW) from None
            payloads[name] = digest([content_types[name], hashlib.sha256(raw).hexdigest()])
        relations[name] = _rels(archive, name)
    # Resolve every relationship, including unused ones. No external resources.
    for name in names:
        if name.endswith(".rels"):
            owner = re.fullmatch(r"(.*/)?_rels/([^/]+)\.rels", name)
            _rels(archive, (owner[1] or "") + owner[2] if owner else "")
    need(part in trees)
    # Canonical resource graph: diagnostic ZIP names, XML prefixes and rIds are
    # not identity. Five refinements cover the bounded slide/layout/master/theme
    # graph; unknown relationship kinds cannot introduce a deeper render path.
    allowed = {"slide", "slideLayout", "slideMaster", "theme", "image", "notesSlide", "notesMaster",
               "presProps", "viewProps", "tableStyles", "officeDocument", "core-properties", "extended-properties"}
    for rows in relations.values():
        need(all(kind.rsplit("/", 1)[-1] in allowed and target in payloads for kind, target in rows.values()))
        need(all(not kind.endswith("/image") or target.lower().endswith((".png", ".jpg", ".jpeg"))
                 for kind, target in rows.values()))
    def reference(kind, target, values):
        # Source/copy response text is intentionally mutable on other slides;
        # slide order/part coverage is independently proven by the OPC graph.
        if kind.endswith(("/slide", "/notesSlide", "/notesMaster")):
            return kind.rsplit("/", 1)[-1]
        return values[target]
    values = payloads
    for _ in range(5):
        values = {name: digest([content_types[name], _xml(trees[name], {rid: [kind, reference(kind, target, values)] for rid, (kind, target) in relations[name].items()}),
                               sorted([kind, reference(kind, target, values)] for kind, target in relations[name].values())])
                  if name in trees else payloads[name] for name in payloads}
    resources = sorted([name.split("/")[1], values[name]] for name in values
                       if not re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
                       and not name.startswith(("ppt/notesSlides/", "ppt/notesMasters/")))
    refs = {rid: [kind, values[target]] for rid, (kind, target) in relations[part].items()}
    media = {rid: payloads[target] for rid, (kind, target) in relations[part].items() if kind.endswith("/image")}
    return digest(resources), refs, media


class SceneUnits(list):
    """JSON serialization intentionally loses parser authority."""


@dataclass(frozen=True)
class _Proof:
    snapshot: str
    xml: bytes
    resources: str
    refs_json: str
    media_json: str
    raw_sha256: str


def attach(page, root, archive, part):
    units = SceneUnits(page["units"]); page["units"] = units
    try:
        resources, refs, media = _package(archive, part)
        units._mapped_proof = _Proof(digest(page), E.tostring(root), resources, json.dumps(refs), json.dumps(media),
                                    hashlib.sha256(archive.fp.getvalue()).hexdigest())
    except (ValueError, TypeError, KeyError, E.ParseError, OSError, OverflowError, AttributeError):
        units._mapped_proof = None


def _text(body):
    return "\n".join("".join(t.text or "" for t in p.iter(A + "t")) for p in body.findall(A + "p")).strip() if body is not None else ""


def _text_body(body):
    need(body is not None and not body.attrib and all(c.tag in (A + "bodyPr", A + "lstStyle", A + "p") for c in body))
    children(body, (A + "bodyPr", A + "lstStyle"), repeated=(A + "p",))
    need([c.tag for c in list(body)[:2]] == [A + "bodyPr", A + "lstStyle"])
    props = body.find(A + "bodyPr"); need(props is not None)
    need(set(props.attrib) <= {"wrap", "anchor", "lIns", "rIns", "tIns", "bIns", "rtlCol", "vertOverflow", "horzOverflow"})
    # Fixed geometry alone does not bound painted text. The paired export must
    # explicitly prove clipping; native APIs do not expose an equivalent flag.
    need(props.get("vertOverflow") == props.get("horzOverflow") == "clip")
    need(props.get("wrap", "square") == "square" and props.get("anchor", "t") in ("t", "ctr", "b"))
    need(all(props.get(k, "0") == "0" for k in ("lIns", "rIns", "tIns", "bIns", "rtlCol")))
    need(all(c.tag == A + "noAutofit" and not c.attrib and not len(c) for c in props))
    need(len(props.findall(A + "noAutofit")) == 1)
    need(body.find(A + "lstStyle") is not None and not len(body.find(A + "lstStyle")))
    paragraphs = []
    for paragraph in body.findall(A + "p"):
        need(not paragraph.attrib and all(c.tag in (A + "r", A + "pPr", A + "endParaRPr", A + "br") for c in paragraph))
        need(not any(c.tag == A + "br" and (len(c) or c.attrib) for c in paragraph))
        children(paragraph, optional=(A + "pPr", A + "endParaRPr"), repeated=(A + "r", A + "br"))
        for run in paragraph.findall(A + "r"):
            need(not run.attrib and [c.tag for c in run] == [A + "rPr", A + "t"])
            need(not run[1].attrib and not len(run[1]))
            for face in run[0]:
                if face.tag in (A + "latin", A + "ea", A + "cs"):
                    need(set(face.attrib) == {"typeface"} and not len(face))
        for p in paragraph.findall(A + "pPr"):
            need(not len(p) and not p.attrib)  # inherited/bulleted/custom layout remains held
        row = [_xml(p) for p in paragraph.findall(A + "pPr") if len(p) or p.attrib]
        if row and (not paragraphs or row != paragraphs[-1]): paragraphs.append(row)
    return [_xml(props), _xml(body.find(A + "lstStyle")), paragraphs]


def _target_text_fits(row):
    """Conservative eligibility, never an estimate of answer quality.

    The supported normal-font ASCII envelope budgets 1.5 em per glyph, 2 em
    per line and 1 em of edge overhang. Budgeting each whitespace-delimited
    word on a separate line also covers early wrapping. All raw spaces count;
    extra paragraphs, controls and unresolved fallback glyphs stay held.
    """
    body = row["node"].find(P + "txBody")
    paragraphs = body.findall(A + "p")
    need(len(paragraphs) == 1)
    paragraph = paragraphs[0]
    need(all(r.tag == A + "r" for r in paragraph))
    text = "".join(r.find(A + "t").text or "" for r in paragraph)
    if not text: return
    need(all(32 <= ord(ch) <= 126 for ch in text))
    size = max(int(r.find(A + "rPr").get("sz")) for r in paragraph) * 127
    width, height = row["box"][2:]
    width -= 2 * row["margin"] + size
    height -= 2 * row["margin"]
    need(width >= 1.5 * size and height >= 2 * size)
    # Split only at a literal space, preserving every leading/trailing/extra
    # space in the conservative width/line budget.
    lines = sum(max(1, math.ceil(len(word) * 1.5 * size / width)) for word in text.split(" "))
    need(lines * 2 * size <= height)


def _pptx_leaf(node, refs, media, transform, depth, order):
    from . import completion as c, explicit_response_binding as v1
    picture = node.tag == P + "pic"
    need(not node.attrib and node.find(P + "style") is None)
    expected = ("nvPicPr", "blipFill", "spPr") if picture else ("nvSpPr", "spPr", "txBody")
    need([c.tag for c in node] == [P + t for t in expected])
    props = node.find(P + "spPr"); need(props is not None and not props.attrib)
    children(props, (A + "xfrm",), (A + "solidFill", A + "noFill", A + "ln", A + "prstGeom"))
    need(not (props.find(A + "solidFill") is not None and props.find(A + "noFill") is not None))
    xf = props.find(A + "xfrm"); need(xf is not None and not xf.attrib)
    _validate_transform(xf)
    bounds = box(c._box(xf, transform))
    nv = nonvisual(node.find(P + ("nvPicPr" if picture else "nvSpPr")), picture=picture)
    stroke = props.find(A + "ln"); margin = 0
    if stroke is not None:
        need(set(stroke.attrib) <= {"w", "cap", "cmpd", "algn"})
        need(all(c.tag in (A + "solidFill", A + "noFill", A + "prstDash", A + "round", A + "headEnd", A + "tailEnd") for c in stroke))
        children(stroke, optional=(A + "solidFill", A + "noFill", A + "prstDash", A + "round", A + "headEnd", A + "tailEnd"))
        need(not (stroke.find(A + "solidFill") is not None and stroke.find(A + "noFill") is not None))
        margin = int(stroke.get("w", "9525")) / 2 if stroke.find(A + "noFill") is None else 0
        need(0 <= margin <= 63500)
        margin *= max(1, abs(transform[0]), abs(transform[1]))
    if picture:
        need(all(ch.tag in (P + "nvPicPr", P + "spPr", P + "blipFill") for ch in node))
        fill = node.find(P + "blipFill"); need(fill is not None and not fill.attrib)
        children(fill, (A + "blip", A + "stretch"), (A + "srcRect",))
        blip = fill.find(A + "blip"); need(blip is not None and set(blip.attrib) == {R + "embed"} and not len(blip))
        need(blip.get(R + "embed") in media and fill.find(A + "stretch/" + A + "fillRect") is not None)
        stretch = fill.find(A + "stretch")
        need(not stretch.attrib and len(stretch) == 1 and not stretch[0].attrib and not len(stretch[0]))
        crop = fill.find(A + "srcRect")
        if crop is not None:
            need(not len(crop) and set(crop.attrib) <= {"l", "r", "t", "b"})
            values = {k: int(crop.get(k, "0")) for k in ("l", "r", "t", "b")}
            need(all(0 <= v < 100000 for v in values.values()) and values["l"] + values["r"] < 100000 and values["t"] + values["b"] < 100000)
        text, surface, layout = "", None, None
    else:
        need(all(ch.tag in (P + "nvSpPr", P + "spPr", P + "txBody") for ch in node))
        geometry = props.find(A + "prstGeom")
        need(geometry is None or (set(geometry.attrib) == {"prst"} and geometry.get("prst") in ("rect", "roundRect")
             and len(geometry) == 1 and geometry[0].tag == A + "avLst" and not len(geometry[0]) and not geometry[0].attrib))
        body = node.find(P + "txBody"); layout = _text_body(body); text = _text(body)
        surface = v1.pptx_surface(node)
        need(surface["supported"])
        if text: need(surface["visible"])
    return {"key": nv.get("name") or "id" + nv.get("id", ""), "kind": "image" if picture else "shape",
            "box": bounds, "text": text, "depth": depth, "order": order, "margin": margin,
            "node": node, "layout": layout, "surface": surface, "refs": refs}


def pptx(page):
    from . import completion as c
    units = page["units"]
    need(type(units) is SceneUnits and type(getattr(units, "_mapped_proof", None)) is _Proof)
    proof = units._mapped_proof; need(proof.snapshot == digest(page))
    root = E.fromstring(proof.xml); refs, media = json.loads(proof.refs_json), json.loads(proof.media_json)
    need(set(root.attrib) <= {"show", "showMasterSp", "showMasterPhAnim"} and all(v in ("1", "true") for v in root.attrib.values()))
    need(all(ch.tag in (P + "cSld", P + "clrMapOvr") for ch in root) and len(root.findall(P + "cSld")) == 1)
    children(root, (P + "cSld",), (P + "clrMapOvr",))
    slide = root.find(P + "cSld"); tree = slide.find(P + "spTree"); need(tree is not None)
    children(slide, (P + "spTree",), (P + "bg",))
    need(not tree.attrib and [c.tag for c in list(tree)[:2]] == [P + "nvGrpSpPr", P + "grpSpPr"])
    nonvisual(tree[0], group=True)
    need(not tree[1].attrib and not len(tree[1]))
    children(tree, (P + "nvGrpSpPr", P + "grpSpPr"), repeated=(P + "sp", P + "pic", P + "grpSp"))
    leaves = []
    def walk(parent, transform=(1, 1, 0, 0), depth=0):
        need(depth <= 10)
        for node in parent:
            if node.tag in (P + "nvGrpSpPr", P + "grpSpPr"): continue
            if node.tag == P + "grpSp":
                need(not node.attrib and all(ch.tag in (P + "nvGrpSpPr", P + "grpSpPr", P + "sp", P + "pic", P + "grpSp") for ch in node))
                children(node, (P + "nvGrpSpPr", P + "grpSpPr"), repeated=(P + "sp", P + "pic", P + "grpSp"))
                need([c.tag for c in list(node)[:2]] == [P + "nvGrpSpPr", P + "grpSpPr"])
                nonvisual(node.find(P + "nvGrpSpPr"), group=True)
                gp = node.find(P + "grpSpPr"); need(gp is not None and not gp.attrib and len(gp) == 1)
                xf = gp.find(A + "xfrm"); need(xf is not None and not xf.attrib)
                _validate_transform(xf, group=True)
                need(all(xf.find(A + tag) is not None for tag in ("off", "ext", "chOff", "chExt")))
                current = c._affine(xf, transform, group=True)
                need(all(math.isfinite(v) for v in current) and current[0] > 0 and current[1] > 0)
                walk(node, current, depth + 1)
            else:
                need(node.tag in (P + "sp", P + "pic"))
                leaves.append(_pptx_leaf(node, refs, media, transform, depth, len(leaves)))
    walk(tree)
    need(len(leaves) == len(units))
    need([(r["text"], r["box"]) for r in leaves] == [(u["text"], box(u.get("box"))) for u in units])
    return leaves, proof.resources, root


def native(deck, page):
    from . import native_teacher_source as n, explicit_response_binding as v1
    raw = deck.get("native_source"); need(isinstance(raw, dict))
    need(raw.get("presentationId") == deck.get("native_source_id"))
    raw_page = raw["slides"][page["number"] - 1]; leaves = []
    need(raw_page.get("slideProperties", {}).get("isSkipped", False) is False)
    def walk(elements, transform=(1, 0, 0, 1, 0, 0), depth=0):
        need(depth <= 10 and isinstance(elements, list))
        for element in elements:
            need(set(element) <= {"objectId", "shape", "image", "elementGroup", "size", "transform", "description", "title"})
            need(set(element.get("transform", {})) <= {"scaleX", "scaleY", "shearX", "shearY", "translateX", "translateY", "unit"})
            matrix = n._compose(transform, n._transform(element.get("transform")))
            need(all(math.isfinite(v) for v in matrix) and matrix[0] > 0 and matrix[3] > 0 and matrix[1] == matrix[2] == 0)
            if "elementGroup" in element:
                need(set(element["elementGroup"]) == {"children"})
                walk(element["elementGroup"]["children"], matrix, depth + 1); continue
            picture = "image" in element
            need(picture != ("shape" in element))
            bounds = box(n._box(element, matrix))
            margin = 0
            if picture:
                image = element["image"]
                need(set(image) <= {"contentUrl", "sourceUrl", "imageProperties"})
                properties = image.get("imageProperties", {})
                need(set(properties) <= {"cropProperties"})
                crop = properties.get("cropProperties", {})
                need(set(crop) <= {"leftOffset", "rightOffset", "topOffset", "bottomOffset"})
                need(all(type(v) in (int, float) and 0 <= v < 1 for v in crop.values()))
                need(crop.get("leftOffset", 0) + crop.get("rightOffset", 0) < 1 and crop.get("topOffset", 0) + crop.get("bottomOffset", 0) < 1)
                text, paragraphs = "", []
            else:
                shape = element["shape"]; need(set(shape) <= {"shapeType", "shapeProperties", "text"})
                need(shape.get("shapeType") in ("TEXT_BOX", "RECTANGLE", "ROUND_RECTANGLE"))
                props = shape.get("shapeProperties", {})
                need(set(props) <= {"shapeBackgroundFill", "autofit", "outline", "contentAlignment", "shadow"})
                need(props.get("autofit", {}).get("autofitType") == "NONE")
                need(props.get("autofit", {}).get("fontScale", 1) == 1 and props.get("autofit", {}).get("lineSpacingReduction", 0) == 0)
                need(not props.get("shadow") or props["shadow"].get("propertyState") == "NOT_RENDERED")
                outline = props.get("outline", {}); need(set(outline) <= {"outlineFill", "weight", "dashStyle", "propertyState"})
                weight = outline.get("weight", {})
                if outline.get("propertyState") != "NOT_RENDERED" and weight:
                    need(weight.get("unit") in ("EMU", "PT") and type(weight.get("magnitude")) in (int, float))
                    margin = weight["magnitude"] * (12700 if weight["unit"] == "PT" else 1) / 2
                    need(0 <= margin <= 63500)
                    margin *= max(1, abs(matrix[0]), abs(matrix[3]))
                entries = shape.get("text", {}).get("textElements", [])
                text = "".join(e.get("textRun", {}).get("content", "") for e in entries).strip()
                paragraphs = [e["paragraphMarker"].get("style", {}) for e in entries if "paragraphMarker" in e and e["paragraphMarker"].get("style")]
                need(not paragraphs)  # unresolved native paragraph layout stays held
                normalized = deepcopy(element)
                normalized["transform"] = {"scaleX": 1, "scaleY": 1, "translateX": bounds[0], "translateY": bounds[1], "unit": "EMU"}
                normalized["shape"]["shapeProperties"].pop("shadow", None)
                if normalized["shape"]["shapeType"] == "ROUND_RECTANGLE": normalized["shape"]["shapeType"] = "RECTANGLE"
                profile = v1.native_surface(normalized, direct=True)
                need(profile["supported"] and (not text or profile["visible"]))
            leaves.append({"key": element["objectId"], "kind": "image" if picture else "shape", "box": bounds,
                           "text": text, "depth": depth, "order": len(leaves), "margin": margin, "node": element})
    walk(raw_page.get("pageElements", []))
    need([(r["text"], r["box"]) for r in leaves] == [(u["text"], box(u.get("box"))) for u in page["units"]])
    return leaves, raw_page


def _native_canonical(value, target_boxes, transform=(1, 0, 0, 1, 0, 0)):
    from . import native_teacher_source as n
    if isinstance(value, list): return [_native_canonical(v, target_boxes, transform) for v in value]
    if not isinstance(value, dict): return value
    value = deepcopy(value)
    value.pop("objectId", None)
    if "description" in value and (not value["description"] or _binding(value["description"])): value.pop("description")
    if "transform" in value and any(k in value for k in ("shape", "image", "elementGroup")):
        transform = n._compose(transform, n._transform(value["transform"]))
    if "image" in value:
        value["image"].pop("contentUrl", None); value["image"].pop("sourceUrl", None)
    if "shape" in value and tuple(box(n._box(value, transform))) in target_boxes:
        value["shape"].pop("text", None)
    return {k: _native_canonical(v, target_boxes, transform) for k, v in value.items()}


def scene(deck, page, targets):
    native_format = "native_source" in deck
    if native_format:
        leaves, raw = native(deck, page)
    else:
        leaves, resources, root = pptx(page)
    boxes = [tuple(f["box"]) for f in targets]
    need(len(boxes) == len(set(boxes)))
    selected = []
    for field in targets:
        matches = [r for r in leaves if r["box"] == field["box"]]
        need(len(matches) == 1 and matches[0]["kind"] == "shape" and matches[0]["depth"] == 0)
        selected.append(matches[0])
    need(all(not rb._intersects(a["box"], b["box"]) for i, a in enumerate(selected) for b in selected[i + 1:]))
    for r in leaves:
        need(rb.rectangle(r["box"], deck["size"]))
        if r in selected: continue
        x, y, w, h = r["box"]; m = r["margin"]
        need(all(not rb._intersects([x-m, y-m, w+2*m, h+2*m], f["box"]) for f in targets))
    if native_format:
        from . import explicit_response_binding as v1
        for r in selected:
            props = r["node"]["shape"]["shapeProperties"]
            fill = props.get("shapeBackgroundFill", {})
            solid = fill.get("solidFill", {})
            need(fill.get("propertyState") in (None, "RENDERED") and solid.get("alpha", 1) == 1
                 and v1._rgb(v1._native_rgb(solid.get("color")), light=True))
        canonical = _native_canonical(raw, set(boxes))
        # Page notes are not a question/response binding. Visible source scene,
        # masters and layouts remain exact; native signed image URLs do not.
        if "slideProperties" in canonical:
            canonical["slideProperties"].pop("notesPage", None)
        resources = _native_canonical({k: deck["native_source"].get(k) for k in ("masters", "layouts")}, set())
        return digest([canonical, resources]), [(r["kind"], r["box"], "TARGET" if r in selected else r["text"]) for r in leaves]
    tree = root.find(P + "cSld/" + P + "spTree")
    for r in selected:
        surface = r["surface"]
        need(surface and surface["supported"] and (not r["text"] or surface["visible"]))
        fill = r["node"].find(P + "spPr/" + A + "solidFill/" + A + "srgbClr")
        need(fill is not None and re.fullmatch(r"[A-Fa-f0-9]{6}", fill.get("val", "")) and min(int(fill.get("val")[i:i+2], 16) for i in (0,2,4)) >= 204)
        need(all(ch.tag == A + "alpha" and ch.get("val") == "100000" for ch in fill))
        _target_text_fits(r)
        body = r["node"].find(P + "txBody"); layout = _text_body(body)
        r["node"].remove(body)
        E.SubElement(r["node"], "mapped-target-layout").text = json.dumps(layout)
    # refs replace diagnostic image rIds with verified media/resource identity.
    refs = json.loads(page["units"]._mapped_proof.refs_json)
    return digest([_xml(root, refs), resources]), [(r["kind"], r["box"], "TARGET" if r in selected else r["text"]) for r in leaves]
