"""An authentic circled choice is completion evidence, never authored prose."""
import hashlib
import json
import math
import re

from . import response_binding as binding

P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
KIND = "circled-choice"


def _canonical(node):
    attrs = dict(node.attrib)
    if node.tag == P + "cNvPr":
        attrs.pop("id", None)
        attrs.pop("name", None)
    if node.tag == A + "off":
        attrs.pop("x", None)
        attrs.pop("y", None)
    return [node.tag, sorted(attrs.items()), node.text or "", [_canonical(c) for c in node]]


def _opaque(color):
    return (color is not None and re.fullmatch(r"[0-9A-Fa-f]{6}", color.get("val", ""))
            and all(c.tag == A + "alpha" and c.get("val") == "100000" for c in color))


def _filled_rgb(node):
    sp = node.find(P + "spPr")
    solid = sp.find(A + "solidFill") if sp is not None else None
    color = solid.find(A + "srgbClr") if solid is not None else None
    return color.get("val").upper() if solid is not None and len(solid) == 1 and _opaque(color) else None


def _circle(node, text, context):
    """Only a visible unfilled, opaque solid-outline ellipse is supported."""
    props = node.find(P + "nvSpPr/" + P + "cNvPr")
    sp = node.find(P + "spPr")
    if (node.tag != P + "sp" or node.attrib or text or sp is None or sp.attrib or context
            or any(c.tag not in (P + "nvSpPr", P + "spPr", P + "txBody") for c in node)):
        return False
    if props is None or props.get("hidden", "0") not in ("0", "false"):
        return False
    xfrm, geom, line = sp.find(A + "xfrm"), sp.find(A + "prstGeom"), sp.find(A + "ln")
    if (xfrm is None or any(xfrm.get(k) not in (None, "0", "false") for k in ("rot", "flipH", "flipV"))
            or geom is None or geom.get("prst") != "ellipse" or any(list(c) for c in geom)
            or sp.find(A + "noFill") is None or line is None):
        return False
    if any(c.tag not in (A + "xfrm", A + "prstGeom", A + "noFill", A + "ln") for c in sp):
        return False
    try:
        width = int(line.get("w", "0"))
    except ValueError:
        return False
    solid = line.find(A + "solidFill")
    if (not 6350 <= width <= 127000 or solid is None or len(solid) != 1 or not _opaque(solid.find(A + "srgbClr"))
            or any(c.tag not in (A + "solidFill", A + "prstDash", A + "round", A + "headEnd", A + "tailEnd") for c in line)
            or any(c.get("val") != "solid" for c in line.findall(A + "prstDash"))
            or any(c.get("type", "none") != "none" for c in line if c.tag in (A + "headEnd", A + "tailEnd"))):
        return False
    # Styling can supply an inherited fill/effect even when spPr seems plain.
    return node.find(P + "style") is None


def read_shapes(tree, *, box, affine, text, name, transform=(1, 1, 0, 0), prefix="", context=()):
    """Parse this explicit activity only; inherited hidden/effect state is kept."""
    out = []
    for child in tree:
        if child.tag == P + "grpSp":
            group = child.find(P + "grpSpPr")
            xfrm = group.find(A + "xfrm") if group is not None else None
            nv = child.find(P + "nvGrpSpPr/" + P + "cNvPr")
            state = []
            if nv is not None and nv.get("hidden", "0") not in ("0", "false"):
                state.append(["hidden", nv.get("hidden")])
            if xfrm is not None:
                state.extend([[k, xfrm.get(k)] for k in ("rot", "flipH", "flipV")
                              if xfrm.get(k) not in (None, "0", "false")])
            if group is not None:
                state.extend(_canonical(c) for c in group if c.tag != A + "xfrm")
                if group.attrib:
                    state.append(["group-property-attributes", sorted(group.attrib.items())])
            state.extend(_canonical(c) for c in child if c.tag not in (
                P + "nvGrpSpPr", P + "grpSpPr", P + "sp", P + "pic", P + "graphicFrame", P + "cxnSp", P + "grpSp"))
            if child.attrib:
                state.append(["group-attributes", sorted(child.attrib.items())])
            out += read_shapes(child, box=box, affine=affine, text=text, name=name,
                               transform=affine(xfrm, transform, group=True),
                               prefix=prefix + name(child, P + "nvGrpSpPr") + "/", context=context + tuple(state))
        elif child.tag in (P + "sp", P + "pic", P + "graphicFrame", P + "cxnSp"):
            nv = {P + "sp": "nvSpPr", P + "pic": "nvPicPr", P + "graphicFrame": "nvGraphicFramePr",
                  P + "cxnSp": "nvCxnSpPr"}[child.tag]
            xfrm = child.find(P + "xfrm") if child.tag == P + "graphicFrame" else child.find(P + "spPr/" + A + "xfrm")
            words = text(child.find(P + "txBody"))
            digest = hashlib.sha256(json.dumps([_canonical(child), context], separators=(",", ":")).encode()).hexdigest()
            # A parent group can make an opaque raw stroke subpixel-thin.
            # The supported circle slice uses original page units only.
            circle = bool(transform[0] == transform[1] == 1 and _circle(child, words, context))
            line = child.find(P + "spPr/" + A + "ln/" + A + "solidFill/" + A + "srgbClr")
            out.append({"key": prefix + name(child, P + nv), "text": words, "box": box(xfrm, transform),
                        "sha256": digest, "circle": circle,
                        "outline_rgb": line.get("val").upper() if circle else None, "fill_rgb": _filled_rgb(child)})
    for n, shape in enumerate(out):
        shape["order"] = n
    return out


def _same(a, b):
    return (isinstance(a, (tuple, list)) and isinstance(b, (tuple, list)) and len(a) == len(b) == 4
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in (*a, *b))
            and max(abs(x - y) for x, y in zip(a, b)) <= 1)


def _overlaps(a, b):
    return a and b and min(a[0] + a[2], b[0] + b[2]) > max(a[0], b[0]) and min(a[1] + a[3], b[1] + b[3]) > max(a[1], b[1])


def _contains(outer, inner):
    return (inner and outer[0] <= inner[0] and outer[1] <= inner[1]
            and inner[0] + inner[2] <= outer[0] + outer[2] and inner[1] + inner[3] <= outer[1] + outer[3])


def _text_at(slide, text, box, plain):
    units = [u for u in (slide or {}).get("units", []) if not u.get("picture")
             and plain(u.get("text", "")) == text and _same(u.get("box"), box)]
    return len(units) == 1


def source(data, template, size, plain):
    """The descriptor is trusted only on the authenticated exact teacher slide."""
    trusted = [u for u in (template or {}).get("units", []) if u.get("binding", {}).get("kind") == KIND]
    if (len(trusted) != 1 or trusted[0]["binding"] != data or data.get("page_size") != list(size)
            or not isinstance(data.get("marker_text"), str)
            or not re.fullmatch(r"\[\[NOTES:[A-Za-z0-9_-]{1,40}:[A-Za-z0-9 _-]{1,80}\]\]", data["marker_text"])
            or trusted[0].get("text", "").strip() != data["marker_text"]):
        return None
    region, prompt, options, original, revision = (data.get(k) for k in ("region", "prompt", "options", "original_shapes", "revision"))
    if (not binding.rectangle(region, size) or not isinstance(prompt, dict) or not isinstance(prompt.get("text"), str)
            or not binding.rectangle(prompt.get("box"), size) or not _text_at(template, prompt["text"], prompt["box"], plain)
            or not isinstance(options, list) or len(options) != 2 or not all(isinstance(o, dict) for o in options)
            or [o.get("text") for o in options] != ["YES", "NO"]
            or not isinstance(original, list) or not 1 <= len(original) <= 40 or not isinstance(revision, dict)):
        return None
    if not all(binding.rectangle(o.get("box"), size) and _text_at(template, o["text"], o["box"], plain)
               and binding.inside(binding.center(o["box"]), region) for o in options):
        return None
    shapes = [s for s in template.get("choice_shapes", []) if _overlaps(s.get("box"), region)]
    if len(shapes) != len(original):
        return None
    # Actual paper colors come from original opaque source rectangles. Unknown
    # or inherited fills cannot establish whether a new outline is visible.
    paper = [s for s in shapes if s.get("fill_rgb")]
    if (not any(_contains(s["box"], region) for s in paper)
            or not all(len([s for s in paper if not s["text"] and _same(s["box"], o["box"])]) == 1 for o in options)):
        return None
    remaining = list(shapes)
    original_orders = []
    for expected in original:
        if (not isinstance(expected, dict) or not binding.rectangle(expected.get("box"), size)
                or not re.fullmatch(r"[0-9a-f]{64}", str(expected.get("sha256", "")))):
            return None
        matches = [s for s in remaining if s["sha256"] == expected["sha256"] and _same(s["box"], expected["box"])]
        if len(matches) != 1:
            return None
        original_orders.append(matches[0]["order"])
        remaining.remove(matches[0])
    if original_orders != sorted(original_orders):
        return None
    if (revision.get("category") != "DRAFT" or not isinstance(revision.get("id"), str)
            or not binding.rectangle(revision.get("box"), size) or not isinstance(revision.get("prompt"), dict)
            or not _text_at(template, revision["prompt"].get("text"), revision["prompt"].get("box"), plain)
            or not _text_at(template, "", revision["box"], plain)
            or _overlaps(revision["box"], region)):
        return None
    return data


def answers(markers, written, template, student, size, plain):
    result = {(m["category"], m["id"]): [] for m in markers}
    bad = (result, list(written), binding.REVIEW)
    choices = [m for m in markers if m.get("binding", {}).get("kind") == KIND]
    if len(choices) != 1 or not student:
        return bad
    marker = choices[0]
    data = marker["binding"]
    if (marker["category"] != "NOTES" or not marker.get("standalone") or source(data, template, size, plain) is None
            or f'[[{marker["category"]}:{marker["code"]}:{marker["id"]}]]' != data["marker_text"]):
        return bad
    revision = data["revision"]
    ordinary = [m for m in markers if m is not marker]
    if (len(ordinary) != 1 or "binding" in ordinary[0]
            or (ordinary[0]["category"], ordinary[0]["id"]) != (revision["category"], revision["id"])):
        return bad
    if not _text_at(student, data["prompt"]["text"], data["prompt"]["box"], plain) or not all(
            _text_at(student, o["text"], o["box"], plain) for o in data["options"]):
        return bad
    remaining = [s for s in student.get("choice_shapes", []) if _overlaps(s.get("box"), data["region"])]
    baseline_orders = []
    for expected in data["original_shapes"]:
        matches = [s for s in remaining if s["sha256"] == expected["sha256"] and _same(s["box"], expected["box"])]
        if len(matches) != 1:
            return bad
        baseline_orders.append(matches[0]["order"])
        remaining.remove(matches[0])
    if baseline_orders != sorted(baseline_orders):
        return bad  # Unchanged paper moved above the printed question can hide it.
    if len(remaining) > 1:
        return bad  # Two marks, including two competing circles, need review.
    if remaining:
        mark = remaining[0]
        box = mark.get("box")
        if not mark.get("circle") or not binding.rectangle(box, size) or not _contains(data["region"], box):
            return bad
        if mark["order"] <= max(baseline_orders):
            return bad  # An opaque supplied card/button can conceal an earlier mark.
        paper = [s["fill_rgb"] for s in template["choice_shapes"] if s.get("fill_rgb") and _overlaps(s.get("box"), box)]
        rgb = [int(mark["outline_rgb"][n:n + 2], 16) for n in (0, 2, 4)]
        if not paper or any(max(abs(rgb[n] - int(color[n * 2:n * 2 + 2], 16)) for n in range(3)) < 64 for color in paper):
            return bad  # An outline matching the authentic paper is not visible evidence.
        cx, cy = binding.center(box)
        targets = [o for o in data["options"] if binding.inside((cx, cy), o["box"])
                   and ((binding.center(o["box"])[0] - cx) / (box[2] / 2)) ** 2
                   + ((binding.center(o["box"])[1] - cy) / (box[3] / 2)) ** 2 <= 1
                   and .3 * o["box"][2] <= box[2] <= 1.5 * o["box"][2]
                   and .6 * o["box"][3] <= box[3] <= 2.5 * o["box"][3]]
        if len(targets) != 1 or sum(((binding.center(o["box"])[0] - cx) / (box[2] / 2)) ** 2
                                  + ((binding.center(o["box"])[1] - cy) / (box[3] / 2)) ** 2 <= 1 for o in data["options"]) != 1:
            return bad
        result[(marker["category"], marker["id"])].append(({
            **mark, "completion_only": "circled choice", "recorded_choice": targets[0]["text"]}, "circled choice"))
    loose = []
    revision_items = []
    for item in written:
        unit, words = item
        if _overlaps(unit.get("box"), data["region"]):
            return bad  # Typed overlays are not a circled graphical choice.
        if binding.rectangle(unit.get("box"), size) and _contains(revision["box"], unit["box"]):
            revision_items.append(item)
        elif _overlaps(unit.get("box"), revision["box"]):
            return bad
        else:
            loose.append(item)
    if len(revision_items) > 1 or not _text_at(student, revision["prompt"]["text"], revision["prompt"]["box"], plain):
        return bad
    result[(revision["category"], revision["id"])] = revision_items
    return result, loose, "original circled choice and separate written revision"
