"""Opt-in visible above-field labels, with exact source-declared text roles.

V1 supports direct fixed text shapes only. It proves semantic source/role
identity, not physical object lineage across Google/PPTX serialization.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import re

from . import response_binding as rb, writing_fields

KIND = "above-field-text/v1"
REVIEW = "Explicit response field needs review"
NORMAL_FACES = frozenset({"arial", "aptos", "calibri", "courier new", "georgia", "helvetica",
                          "times new roman", "trebuchet ms", "verdana"})
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _xml(node, *, style=False):
    if node is None:
        return None
    return ["run-style" if style else node.tag, sorted(node.attrib.items()),
            [_xml(child) for child in node]]


def decode(raw):
    """Reject duplicate keys, including ones the ordinary JSON reader replaces."""
    if not isinstance(raw, str) or KIND not in raw or not raw.startswith(rb.HEADER):
        return None
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate binding key")
            result[key] = value
        return result
    try:
        value = json.loads(raw[len(rb.HEADER):], object_pairs_hook=pairs)
        if len(raw) > 40_000 or not isinstance(value, dict) or value.get("kind") != KIND:
            raise ValueError("invalid explicit binding")
        return value
    except (ValueError, TypeError):
        return {"kind": KIND, "invalid": True}


def _rgb(value, *, light=False):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return False
    return (all(type(c) in (int, float) and 0 <= c <= 1 for c in value)
            and (min(value) >= .8 if light else max(value) <= .5))


def _native_rgb(color):
    if not isinstance(color, dict) or set(color) != {"rgbColor"} or not isinstance(color["rgbColor"], dict):
        return None
    rgb = color["rgbColor"]
    if set(rgb) - {"red", "green", "blue"}:
        return None
    return [rgb.get(c, 0) for c in ("red", "green", "blue")]


def native_surface(element, *, direct):
    """Opaque resolved normal text and fixed native surfaces; unknown => hold."""
    shape = element.get("shape", {})
    mapping = lambda v: v if isinstance(v, dict) else {}
    props = mapping(shape.get("shapeProperties", {}))
    text = shape.get("text", {}).get("textElements", [])
    styles = [r["textRun"].get("style", {}) for r in text if "textRun" in r]
    paras = [r["paragraphMarker"].get("style", {}) for r in text if "paragraphMarker" in r]
    fill = mapping(mapping(props.get("shapeBackgroundFill")).get("solidFill"))
    light = fill.get("alpha", 1) == 1 and _rgb(_native_rgb(fill.get("color")), light=True)
    def visible(style):
        if not isinstance(style, dict) or ("weightedFontFamily" in style and not isinstance(style["weightedFontFamily"], dict)):
            return False
        size = mapping(style.get("fontSize"))
        foreground = mapping(style.get("foregroundColor"))
        weighted = style.get("weightedFontFamily", {})
        face = style.get("fontFamily", weighted.get("fontFamily"))
        return (not set(style) - {"fontSize", "fontFamily", "weightedFontFamily", "foregroundColor", "bold", "italic", "underline", "strikethrough"}
                and isinstance(face, str) and face.casefold() in NORMAL_FACES
                and (not weighted or weighted.get("fontFamily") == face)
                and size.get("unit") == "PT" and type(size.get("magnitude")) in (int, float)
                and 10 <= size["magnitude"] <= 72 and set(foreground) == {"opaqueColor"}
                and _rgb(_native_rgb(foreground.get("opaqueColor")))
                and not any(k in style for k in ("link", "baselineOffset")))
    transform = element.get("transform", {})
    autofit = mapping(props.get("autofit"))
    fixed = (autofit.get("autofitType") == "NONE"
             and autofit.get("fontScale", 1) == 1 and autofit.get("lineSpacingReduction", 0) == 0)
    supported = (direct and shape.get("shapeType") in ("TEXT_BOX", "RECTANGLE")
                 and not set(shape) - {"shapeType", "shapeProperties", "text", "placeholder"}
                 and not set(props) - {"shapeBackgroundFill", "autofit", "outline", "contentAlignment"}
                 and not set(element) - {"objectId", "shape", "size", "transform", "description", "title"}
                 and transform.get("scaleX") == transform.get("scaleY") == 1
                 and transform.get("shearX", 0) == transform.get("shearY", 0) == 0 and fixed)
    return {"format": "native", "supported": supported,
            "visible": bool(supported and light and mapping(props.get("shapeBackgroundFill")).get("propertyState") in (None, "RENDERED")
                            and styles and all(visible(s) for s in styles)),
            "paint": digest({"shape": {k: v for k, v in shape.items() if k != "text"},
                             "styles": sorted({digest(s) for s in styles}),
                             "paragraphs": sorted({digest(s) for s in paras})})}


def pptx_surface(shape):
    props, body = shape.find(P + "spPr"), shape.find(P + "txBody")
    if props is None or body is None:
        return {"format": "pptx", "supported": False, "visible": False, "paint": ""}
    def color(node):
        if node is None or node.tag != A + "srgbClr" or not re.fullmatch(r"[0-9A-Fa-f]{6}", node.get("val", "")):
            return None
        if any(c.tag != A + "alpha" or c.get("val") != "100000" for c in node):
            return None
        return [int(node.get("val")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    fill = props.find(A + "solidFill")
    light = fill is not None and len(fill) == 1 and _rgb(color(fill[0]), light=True)
    run_props = list(body.iter(A + "rPr")) + list(body.iter(A + "defRPr")) + list(body.iter(A + "endParaRPr"))
    def visible(style):
        size = style.get("sz", "")
        paint = style.find(A + "solidFill")
        latin = style.find(A + "latin")
        return (not set(style.attrib) - {"sz", "b", "i", "u", "strike", "lang", "dirty", "baseline"}
                and all(node.tag in (A + "solidFill", A + "latin", A + "ea", A + "cs") for node in style)
                and len(style.findall(A + "latin")) == 1 and len(style.findall(A + "solidFill")) == 1
                and len(style.findall(A + "ea")) <= 1 and len(style.findall(A + "cs")) <= 1
                and size.isdigit() and 1000 <= int(size) <= 7200 and paint is not None and len(paint) == 1
                and latin is not None and latin.get("typeface", "").casefold() in NORMAL_FACES
                and all(node.get("typeface", "").casefold() in NORMAL_FACES for node in style if node.tag in (A + "ea", A + "cs"))
                and _rgb(color(paint[0])) and not any(style.find(A + k) is not None for k in
                    ("hlinkClick", "hlinkMouseOver", "effectLst", "effectDag"))
                and style.get("baseline", "0") == "0")
    body_props = body.find(A + "bodyPr")
    fixed = body_props is not None and body_props.find(A + "noAutofit") is not None
    supported = (fixed and not shape.attrib
                 and not any(c.tag not in (P + "nvSpPr", P + "spPr", P + "style", P + "txBody") for c in shape)
                 and not any(c.tag not in (A + "xfrm", A + "solidFill", A + "noFill", A + "ln", A + "prstGeom") for c in props)
                 and not any(c.tag not in (A + "bodyPr", A + "lstStyle", A + "p") for c in body))
    paint = deepcopy(props)
    transform = paint.find(A + "xfrm")
    if transform is not None:
        paint.remove(transform)  # exact absolute geometry is in the ordered scene
    return {"format": "pptx", "supported": bool(supported),
            "visible": bool(supported and light and run_props and all(visible(s) for s in run_props)),
            "paint": digest({"shape": _xml(paint), "style": _xml(shape.find(P + "style")),
                             "body": _xml(body_props),
                             "styles": sorted({digest(_xml(s, style=True)) for s in run_props}),
                             "paragraphs": sorted({digest(_xml(s)) for s in body.iter(A + "pPr")})})}


def attach_pptx(page, tree, root, name):
    # Animations and slide-level display branches can hide otherwise normal
    # shapes. This v1 contract never interprets an animation timeline.
    if (set(root.attrib) - {"show", "showMasterSp", "showMasterPhAnim"}
            or any(v not in ("1", "true") for v in root.attrib.values())
            or len(root.findall(P + "cSld")) != 1 or len(root.findall(P + "clrMapOvr")) > 1
            or any(c.tag not in (P + "cSld", P + "clrMapOvr") for c in root)):
        page["explicit_invalid"] = True
    slide = root.find(P + "cSld")
    if (slide is None or len(slide.findall(P + "spTree")) != 1 or len(slide.findall(P + "bg")) > 1
            or any(c.tag not in (P + "spTree", P + "bg") for c in slide)):
        page["explicit_invalid"] = True
    colors = root.find(P + "clrMapOvr")
    if colors is not None and (len(colors) != 1 or colors[0].tag not in (A + "masterClrMapping", A + "overrideClrMapping") or len(colors[0])):
        page["explicit_invalid"] = True
    background = slide.find(P + "bg") if slide is not None else None
    if background is not None and (len(background) != 1 or background[0].tag != P + "bgPr"
            or len(background[0]) != 1 or background[0][0].tag not in (A + "solidFill", A + "noFill")):
        page["explicit_invalid"] = True
    page["explicit_page_surface"] = digest({"attributes": sorted(root.attrib.items()),
        "slide": sorted(slide.attrib.items()) if slide is not None else [],
        "background": _xml(slide.find(P + "bg")) if slide is not None else None,
        "colors": _xml(root.find(P + "clrMapOvr"))})
    # The ordinary reader omits some objects. An explicit full-scene claim must
    # never omit a connector, video, table, group or other possible cover.
    if any(child.tag not in (P + "sp", P + "nvGrpSpPr", P + "grpSpPr") for child in tree):
        page["explicit_invalid"] = True
    for props in tree.findall(P + "grpSpPr"):
        for transform in props:
            if (transform.tag != A + "xfrm" or set(transform.attrib) - {"rot", "flipH", "flipV"}
                    or any(c.tag not in (A + "off", A + "chOff", A + "ext", A + "chExt") for c in transform)
                    or any(transform.get(k, "0") not in ("0", "false") for k in ("rot", "flipH", "flipV"))):
                page["explicit_invalid"] = True
                continue
            for tag in ("off", "chOff"):
                node = transform.find(A + tag)
                if node is not None and any(node.get(k, "0") != "0" for k in ("x", "y")):
                    page["explicit_invalid"] = True
            ext, child_ext = transform.find(A + "ext"), transform.find(A + "chExt")
            if (ext is None) != (child_ext is None) or (ext is not None and ext.attrib != child_ext.attrib):
                page["explicit_invalid"] = True
    direct_properties = {id(s.find(P + "nvSpPr/" + P + "cNvPr")) for s in tree.findall(P + "sp")}
    if any(decode(p.get("descr", "")) is not None and id(p) not in direct_properties for p in tree.iter(P + "cNvPr")):
        page["explicit_invalid"] = True
    for shape in tree.findall(P + "sp"):
        matches = [u for u in page["units"] if u["key"] == name(shape, P + "nvSpPr")]
        if len(matches) == 1:
            unit = matches[0]
            unit["explicit_surface"] = pptx_surface(shape)
            props = shape.find(P + "nvSpPr/" + P + "cNvPr")
            binding = decode(props.get("descr", "") if props is not None else "")
            if binding is not None:
                unit["binding"] = binding


def bound(page):
    return [u for u in (page or {}).get("units", []) if u.get("binding", {}).get("kind") == KIND]


def _box(value):
    return [round(v) for v in value] if value and all(type(v) in (int, float) and abs(v - round(v)) <= .01 for v in value) else None


def _unit(page, box):
    matches = [u for u in page["units"] if _box(u.get("box")) == box]
    if len(matches) != 1:
        raise ValueError(REVIEW)
    return matches[0]


def _scene(page, size, label, target, practice):
    if page.get("explicit_invalid"):
        raise ValueError(REVIEW)
    roles = [("label", label), ("target", target)] + [("practice%d" % i, f) for i, f in enumerate(practice)]
    boxes = [r[1]["box"] for r in roles]
    if (len({tuple(b) for b in boxes}) != len(boxes) or any(not rb.rectangle(b, size) for b in boxes)
            or any(rb._intersects(a, b) for i, a in enumerate(boxes) for b in boxes[i + 1:])):
        raise ValueError(REVIEW)
    scene, paint, formats = [], [page.get("explicit_page_surface")], set()
    for unit in page["units"]:
        box, surface = _box(unit.get("box")), unit.get("explicit_surface", {})
        if (box is None or not rb.rectangle(box, size) or not surface.get("supported")
                or unit.get("panel_geometry_unverified") or unit.get("picture")):
            raise ValueError(REVIEW)
        formats.add(surface.get("format"))
        role = next((name for name, field in roles if field["box"] == box), None)
        if role:
            if _unit(page, box) is not unit or not surface.get("visible"):
                raise ValueError(REVIEW)
        elif any(rb._intersects(box, b) for b in boxes):
            raise ValueError(REVIEW)  # covers, overlays and ambiguous backgrounds
        scene.append({"type": "text", "box": box,
                      "text": role if role and role != "label" else unit["text"], "role": role})
        paint.append(surface["paint"])
    if len(formats) != 1 or not set(map(tuple, boxes)) <= {tuple(u["box"]) for u in scene}:
        raise ValueError(REVIEW)
    return digest(scene), next(iter(formats)), digest(paint)


def _fields(page, label_box, target_box, practice_boxes):
    return [{"box": box, "given": _unit(page, box)["text"]} for box in [label_box, target_box, *practice_boxes]]


def author(native, exported, *, label_key, target_key, practice_keys=()):
    """Derive, never invent, a contract from independently captured same-source reads.

    The trusted author must bookend both reads against the actual exact source
    identity/revision, then attach this result only to the selected label and
    verify native/export readback. This pure helper performs no source write.
    """
    from .completion import MARKER
    source_id = native.get("native_source_id")
    if (not isinstance(source_id, str) or not source_id or native["size"] != exported["size"]
            or not isinstance(practice_keys, (list, tuple)) or len(practice_keys) > 20):
        raise ValueError(REVIEW)
    pages = [p for p in native["slides"] if any(u["key"] == label_key for u in p["units"])]
    if len(pages) != 1:
        raise ValueError(REVIEW)
    page = pages[0]
    def select(key):
        matches = [u for u in page["units"] if u["key"] == key]
        if len(matches) != 1:
            raise ValueError(REVIEW)
        return _box(matches[0]["box"])
    fields = _fields(page, select(label_key), select(target_key), [select(k) for k in practice_keys])
    label, target, *practice = fields
    matches = list(MARKER.finditer(label["given"]))
    if len(matches) != 1 or matches[0].group(1) != "NOTES":
        raise ValueError(REVIEW)
    category, cycle, ident = matches[0].groups()
    result = {"kind": KIND, "source_id": source_id, "page_number": page["number"],
              "page_size": list(native["size"]), "category": category, "cycle": cycle, "id": ident,
              "label": {"box": label["box"], "text": label["given"]}, "target": target, "practice": practice}
    surfaces, scene = {}, None
    for deck in (native, exported):
        candidates = [p for p in deck["slides"] if p["number"] == page["number"]]
        if len(candidates) != 1:
            raise ValueError(REVIEW)
        p = candidates[0]
        if _fields(p, label["box"], target["box"], [f["box"] for f in practice]) != fields:
            raise ValueError(REVIEW)
        current, form, paint = _scene(p, native["size"], result["label"], target, practice)
        if scene is not None and current != scene:
            raise ValueError(REVIEW)
        scene, surfaces[form] = current, paint
    if set(surfaces) != {"native", "pptx"}:
        raise ValueError(REVIEW)
    result.update(scene_sha256=scene, surface_sha256=surfaces)
    _validate(result, page, native["size"], original=True)
    return result


def _validate(data, page, size, *, original):
    from .completion import MARKER
    keys = {"kind", "source_id", "page_number", "page_size", "category", "cycle", "id", "label", "target", "practice", "scene_sha256", "surface_sha256"}
    if (not isinstance(data, dict) or set(data) != keys or data["kind"] != KIND
            or data["category"] != "NOTES" or data["page_size"] != list(size)
            or type(data["page_number"]) is not int or page["number"] != data["page_number"]
            or not isinstance(data["source_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", data["source_id"])
            or not isinstance(data["practice"], list) or len(data["practice"]) > 20
            or not isinstance(data["label"], dict) or set(data["label"]) != {"box", "text"}
            or not isinstance(data["surface_sha256"], dict) or set(data["surface_sha256"]) != {"native", "pptx"}
            or any(not isinstance(v, str) or not re.fullmatch(r"[a-f0-9]{64}", v) for v in
                   [data["scene_sha256"], *data["surface_sha256"].values()])):
        raise ValueError(REVIEW)
    label, target, practice = data["label"], data["target"], data["practice"]
    for field in [target, *practice]:
        if (not isinstance(field, dict) or set(field) != {"box", "given"}
                or not isinstance(field["given"], str) or len(field["given"]) > 4000 or "[[" in field["given"]):
            raise ValueError(REVIEW)
        if original and _unit(page, field["box"])["text"] != field["given"]:
            raise ValueError(REVIEW)
    box, target_box = label["box"], target["box"]
    if (not rb.rectangle(box, size) or not rb.rectangle(target_box, size)
            or box[1] + box[3] > target_box[1] or target_box[1] - box[1] - box[3] > .05 * size[1]
            or max(box[0], target_box[0]) >= min(box[0] + box[2], target_box[0] + target_box[2])):
        raise ValueError(REVIEW)
    unit = _unit(page, box)
    tags = [m.groups() for u in page["units"] for m in MARKER.finditer(u["text"])]
    if unit["text"] != label["text"] or tags != [(data["category"], data["cycle"], data["id"])]:
        raise ValueError(REVIEW)
    scene, form, surface = _scene(page, size, label, target, practice)
    if scene != data["scene_sha256"] or data["surface_sha256"].get(form) != surface:
        raise ValueError(REVIEW)
    return _unit(page, target_box)


class _Reference:
    def __init__(self, data, page):
        self.data = deepcopy(data)
        self.source_page = deepcopy(page)


def _snapshot(native):
    return digest({k: v for k, v in native.items() if k != "_explicit_export_witness"})


@dataclass(frozen=True)
class _ExportWitness:
    """Host-only current paired-read evidence; never deserialize from a deck."""
    snapshot: str


def with_export_witness(native, exported):
    """Prove current native content against a current, root-validated export.

    Both actual file IDs/revisions must come from trusted host metadata
    bookends. A raw exported deck or descriptor-provided identity cannot issue
    this capability. This proves no historical distribution or school action.
    """
    source_id, revision = native.get("native_source_id"), native.get("native_source_revision")
    if (not source_id or not revision or exported.get("explicit_source_id") != source_id
            or exported.get("explicit_source_revision") != revision or native["size"] != exported["size"]
            or len(native["slides"]) != len(exported["slides"])):
        raise ValueError(REVIEW)
    seen = False
    for page, export_page in zip(native["slides"], exported["slides"]):
        actual = lambda p: [(u["text"], _box(u.get("box"))) for u in p["units"]]
        if (page["number"] != export_page["number"] or actual(page) != actual(export_page)
                or any(box is None for _, box in actual(page))):
            raise ValueError(REVIEW)
        labels, exported_labels = bound(page), bound(export_page)
        if not labels and not exported_labels:
            continue
        if len(labels) != 1 or len(exported_labels) != 1 or labels[0]["binding"] != exported_labels[0]["binding"]:
            raise ValueError(REVIEW)
        data = labels[0]["binding"]
        for candidate in (page, export_page):
            _validate(data, candidate, native["size"], original=False)
        seen = True
    if not seen:
        raise ValueError(REVIEW)
    result = deepcopy(native)
    result.pop("_explicit_export_witness", None)
    result["_explicit_export_witness"] = _ExportWitness(_snapshot(result))
    return result


def _witnessed(deck):
    formats = {u.get("explicit_surface", {}).get("format")
               for page in deck["slides"] if bound(page) for u in page["units"]}
    native = (any(k in deck for k in ("native_source", "native_source_id", "native_source_revision"))
              or "native" in formats or str(deck.get("source_kind", "")).startswith("native_"))
    if not native:
        return formats == {"pptx"} and deck.get("source_kind") in (None, "pptx")
    if (formats != {"native"} or deck.get("source_kind") not in ("native_teacher_source", "native_student_source")
            or not all(k in deck for k in ("native_source", "native_source_id", "native_source_revision"))):
        return False
    witness = deck.get("_explicit_export_witness")
    return type(witness) is _ExportWitness and witness.snapshot == _snapshot(deck)


def plan(student, template):
    """Fresh teacher source drives dispatch; missing submitted metadata holds."""
    if any(p.get("explicit_invalid") for d in (student, template) if d for p in d["slides"]):
        return {}, True
    expected = [(p, u) for p in (template or {}).get("slides", []) for u in bound(p)]
    received = [(p, u) for p in student["slides"] for u in bound(p)]
    if not expected and not received:
        return {}, False
    references = {}
    try:
        if (not template or not _witnessed(template) or not _witnessed(student)
                or student["size"] != template["size"] or len(student["slides"]) != len(template["slides"]) or len(expected) != len(received)):
            raise ValueError(REVIEW)
        source_id = template.get("explicit_source_id", template.get("native_source_id"))
        for page, label in expected:
            data = label["binding"]
            if page["number"] in references or len(bound(page)) != 1 or data.get("source_id") != source_id:
                raise ValueError(REVIEW)
            _validate(data, page, template["size"], original=True)
            matches = [(p, u) for p, u in received if p["number"] == page["number"] and u.get("binding") == data]
            if len(matches) != 1:
                raise ValueError(REVIEW)
            _validate(data, matches[0][0], student["size"], original=False)
            references[page["number"]] = _Reference(data, page)
        return references, False
    except (ValueError, KeyError, TypeError, IndexError, OverflowError):
        return {}, True


def answers(markers, written, template, student, size, plain, *, reference=None):
    result = {(m["category"], m["id"]): [] for m in markers}
    bad = (result, list(written), REVIEW)
    if type(reference) is not _Reference or template != reference.source_page or not student:
        return bad
    data = reference.data
    try:
        if len(bound(student)) != 1 or bound(student)[0].get("binding") != data:
            return bad
        target = _validate(data, student, size, original=False)
    except (ValueError, KeyError, TypeError, IndexError, OverflowError):
        return bad
    words = writing_fields.new_text(data["target"]["given"], plain(target["text"]))
    if words:
        evidence = {**target, "verified_workspace_answer": words}
        if writing_fields.tokens(words) in [writing_fields.tokens(plain(u["text"])) for u in template["units"] if plain(u["text"])]:
            evidence["workspace_supplied_copy"] = True
        result[(data["category"], data["id"])].append((evidence, words))
    return result, [item for item in written if item[0] is not target], "explicit above-field response"
