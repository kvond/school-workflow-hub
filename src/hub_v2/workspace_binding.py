"""One authenticated prefilled writing workspace, with no borrowed questions."""
from copy import deepcopy
from difflib import SequenceMatcher
import hashlib
import json
import re

from . import response_binding as binding

P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
KIND = "prefilled-writing-workspace"


def _canonical(node):
    attrs = dict(node.attrib)
    if node.tag == P + "cNvPr":
        attrs.pop("id", None)
        attrs.pop("name", None)
    if node.tag == A + "off":
        attrs.pop("x", None)
        attrs.pop("y", None)
    return [node.tag, sorted(attrs.items()), node.text or "", [_canonical(c) for c in node]]


def _sha(value):
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


def _text(body):
    lines = []
    if body is not None:
        for paragraph in body.findall(A + "p"):
            parts = []
            for child in paragraph:
                if child.tag == A + "br":
                    parts.append("\n")
                elif child.tag == A + "r":
                    parts.append(child.findtext(A + "t", ""))
                elif child.tag == A + "fld":
                    parts.append(child.findtext(A + "t", ""))
            lines.append("".join(parts))
    return "\n".join(lines).strip()


def read_shapes(tree, *, box, affine, name, transform=(1, 1, 0, 0), context=()):
    out = []
    for node in tree:
        if node.tag == P + "grpSp":
            properties = node.find(P + "grpSpPr")
            xfrm = properties.find(A + "xfrm") if properties is not None else None
            nv = node.find(P + "nvGrpSpPr/" + P + "cNvPr")
            state = []
            if node.attrib or (properties is not None and properties.attrib):
                state.append("unknown group attributes")
            if any(c.tag not in (P + "nvGrpSpPr", P + "grpSpPr", P + "sp", P + "pic", P + "grpSp", P + "graphicFrame", P + "cxnSp") for c in node):
                state.append("unknown group payload")
            if nv is not None and nv.get("hidden", "0") not in ("0", "false"):
                state.append("hidden group")
            if properties is not None:
                state.extend(_canonical(c) for c in properties if c.tag != A + "xfrm")
            if xfrm is not None:
                if set(xfrm.attrib) - {"rot", "flipH", "flipV"}:
                    state.append("unknown group transform")
                state.extend([k, xfrm.get(k)] for k in ("rot", "flipH", "flipV")
                             if xfrm.get(k) not in (None, "0", "false"))
            out.extend(read_shapes(node, box=box, affine=affine, name=name,
                                   transform=affine(xfrm, transform, group=True), context=context + tuple(state)))
        elif node.tag in (P + "sp", P + "pic", P + "graphicFrame", P + "cxnSp"):
            xfrm = node.find(P + "xfrm") if node.tag == P + "graphicFrame" else node.find(P + "spPr/" + A + "xfrm")
            body = node.find(P + "txBody")
            clone = deepcopy(node)
            if clone.find(P + "txBody") is not None:
                clone.remove(clone.find(P + "txBody"))
            props = node.find(P + "nvSpPr/" + P + "cNvPr")
            styles = []
            for run in (body.findall(A + "p/" + A + "r") if body is not None else []):
                if run.findtext(A + "t", "").strip():
                    style = run.find(A + "rPr")
                    styles.append(_sha(_canonical(style)) if style is not None else None)
            body_style = [_canonical(c) for c in body if c.tag in (A + "bodyPr", A + "lstStyle")] if body is not None else []
            paragraph_styles = [_sha([_canonical(c) for c in paragraph if c.tag in (A + "pPr", A + "endParaRPr")])
                                for paragraph in body.findall(A + "p")] if body is not None else []
            break_styles = [_sha(_canonical(c)) for c in body.iter(A + "br")] if body is not None else []
            known_body = (body is not None and all(c.tag in (A + "bodyPr", A + "lstStyle", A + "p") for c in body)
                          and all(c.tag in (A + "pPr", A + "r", A + "br", A + "endParaRPr")
                                  for paragraph in body.findall(A + "p") for c in paragraph))
            properties = node.find(P + "spPr")
            marker_runs = [r for r in body.findall(A + "p/" + A + "r") if r.findtext(A + "t", "").strip()] if body is not None else []
            transparent_marker = bool(properties is not None and properties.find(A + "noFill") is not None
                                      and properties.find(A + "ln/" + A + "noFill") is not None and marker_runs
                                      and all(r.find(A + "rPr") is not None and r.find(A + "rPr").get("sz") == "100"
                                              and r.find(A + "rPr/" + A + "solidFill/" + A + "srgbClr/" + A + "alpha") is not None
                                              and r.find(A + "rPr/" + A + "solidFill/" + A + "srgbClr/" + A + "alpha").get("val") == "0"
                                              for r in marker_runs))
            out.append({"box": box(xfrm, transform), "text": _text(body), "sha256": _sha([_canonical(node), context]),
                        "paint_sha256": _sha([_canonical(clone), context]), "body_style_sha256": _sha(body_style),
                        "run_style_sha256": styles, "text_shape": node.tag == P + "sp" and body is not None,
                        "paragraph_style_sha256": paragraph_styles, "break_style_sha256": break_styles,
                        "transparent_marker": transparent_marker,
                        "visible_context": known_body and not context and transform[0] == transform[1] == 1
                        and not node.attrib and (props is None or props.get("hidden", "0") in ("0", "false"))})
    for number, shape in enumerate(out):
        shape["order"] = number
    return out


def attach(slides, trees, archive, *, box, affine, name):
    marked = [s for s in slides if any(u.get("binding", {}).get("kind") == KIND for u in s["units"])]
    if not marked:
        return
    themes = sorted([n, hashlib.sha256(archive.read(n)).hexdigest() if archive.getinfo(n).file_size <= 2_000_000
                     else "UNREADABLE_THEME"] for n in archive.namelist() if re.fullmatch(r"ppt/theme/theme\d+\.xml", n))
    styles = sorted([n, hashlib.sha256(archive.read(n)).hexdigest() if archive.getinfo(n).file_size <= 2_000_000
                     else "UNREADABLE_STYLE"] for n in archive.namelist()
                    if re.fullmatch(r"ppt/(?:theme|slideMasters|slideLayouts)/[^/]+\.xml", n))
    for slide in marked:
        tree, root, pictures = trees[slide["number"]]
        slide["workspace_shapes"] = read_shapes(tree, box=box, affine=affine, name=name)
        slide["workspace_slide_style_signature"] = slide_style(tree, root)
        slide["workspace_media_signature"] = _sha(sorted(pictures.items()))
        slide["workspace_theme_signature"] = _sha(themes)
        slide["workspace_resource_signature"] = _sha(styles)
        references = {u["binding"].get("prompt_page") for u in slide["units"] if u.get("binding", {}).get("kind") == KIND}
        if len(references) == 1:
            page = next(iter(references))
            if isinstance(page, int) and not isinstance(page, bool) and page in trees:
                prompt_tree, prompt_root, prompt_pictures = trees[page]
                slide["workspace_prompt_shapes"] = read_shapes(prompt_tree, box=box, affine=affine, name=name)
                slide["workspace_prompt_style_signature"] = slide_style(prompt_tree, prompt_root)
                slide["workspace_prompt_scene_signature"] = scene(slide["workspace_prompt_shapes"], prompt_pictures)


def slide_style(tree, root):
    source = deepcopy(root)
    source_tree = source.find(P + "cSld/" + P + "spTree")
    for child in list(source_tree):
        if child.tag in (P + "sp", P + "pic", P + "grpSp", P + "graphicFrame", P + "cxnSp"):
            source_tree.remove(child)
    return _sha(_canonical(source))


def scene(shapes, pictures):
    """Every supplied object, painter order, geometry and actual image bytes."""
    return _sha([[{k: shape[k] for k in ("sha256", "box", "order")} for shape in shapes], sorted(pictures.items())])


def _same(a, b):
    return (isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)) and len(a) == len(b) == 4
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (*a, *b)) and list(a) == list(b))


def _overlap(a, b):
    return a and b and min(a[0] + a[2], b[0] + b[2]) > max(a[0], b[0]) and min(a[1] + a[3], b[1] + b[3]) > max(a[1], b[1])


def _digests(values, limit):
    return (isinstance(values, list) and 0 < len(values) <= limit
            and all(isinstance(v, str) and re.fullmatch(r"[a-f0-9]{64}", v) for v in values))


def _visible_text(shape, data):
    return (shape["visible_context"]
            and all(v in data["visible_run_styles"] for v in shape["run_style_sha256"])
            and all(v in data["visible_paragraph_styles"] for v in shape["paragraph_style_sha256"])
            and all(v in data["visible_break_styles"] for v in shape["break_style_sha256"]))


def source(marker, template, size, plain):
    data = marker.get("binding", {})
    if (marker.get("category") != "NOTES" or not marker.get("standalone") or not binding._trusted(marker, template)
            or data.get("kind") != KIND or data.get("page_size") != list(size)
            or not _same(marker.get("box"), data.get("marker_box"))):
        return None
    workspace, prompts, original = data.get("workspace"), data.get("prompts"), data.get("other_original_shapes")
    if (not isinstance(workspace, dict) or not isinstance(workspace.get("heading"), str) or not workspace["heading"]
            or not binding.rectangle(workspace.get("box"), size) or not isinstance(prompts, list) or len(prompts) != 3
            or not isinstance(original, list) or len(original) > 40
            or not _digests(data.get("theme_signatures"), 16) or not _digests(data.get("resource_signatures"), 16)
            or (template or {}).get("workspace_theme_signature") not in data["theme_signatures"]
            or (template or {}).get("workspace_resource_signature") not in data["resource_signatures"]
            or not _digests(data.get("slide_style_signatures"), 16)
            or not _digests(data.get("prompt_style_signatures"), 16)
            or not _digests(data.get("prompt_scene_signatures"), 16)
            or not _digests(data.get("workspace_media_signatures"), 16)
            or (template or {}).get("workspace_slide_style_signature") not in data["slide_style_signatures"]
            or (template or {}).get("workspace_prompt_style_signature") not in data["prompt_style_signatures"]
            or (template or {}).get("workspace_prompt_scene_signature") not in data["prompt_scene_signatures"]
            or (template or {}).get("workspace_media_signature") not in data["workspace_media_signatures"]
            or not _digests(data.get("visible_run_styles"), 30)
            or not _digests(data.get("visible_paragraph_styles"), 50)
            or not isinstance(data.get("visible_break_styles"), list)
            or (data["visible_break_styles"] and not _digests(data["visible_break_styles"], 20))):
        return None
    for prompt in prompts:
        if (not isinstance(prompt, dict) or not isinstance(prompt.get("text"), str) or not binding.rectangle(prompt.get("box"), size)
                or len([u for u in template.get("workspace_prompt_shapes", []) if u["sha256"] == prompt.get("sha256")
                        and u["text"] == prompt["text"] and _same(u["box"], prompt["box"]) and u["visible_context"]]) != 1):
            return None
    fields = [u for u in template.get("workspace_shapes", []) if u["text"] == workspace["heading"]
              and _same(u["box"], workspace["box"]) and u["visible_context"]
              and u["paint_sha256"] == workspace.get("paint_sha256")
              and u["body_style_sha256"] == workspace.get("body_style_sha256")]
    if len(fields) != 1:
        return None
    if fields[0]["order"] != workspace.get("order") or not _visible_text(fields[0], data):
        return None
    allowed = workspace.get("received_boxes")
    if (not isinstance(allowed, list) or not 1 <= len(allowed) <= 2 or allowed[0] != workspace["box"]
            or any(not binding.rectangle(b, size) or b[0] != workspace["box"][0] or b[2:] != workspace["box"][2:]
                   or b[1] - workspace["box"][1] not in (0, 12669) for b in allowed)):
        return None
    shapes = template.get("workspace_shapes", [])
    token = "[[%s:%s:%s]]" % (marker["category"], marker["code"], marker["id"])
    if len(shapes) != len(original) + 2 or len([u for u in shapes if u["text"] == token
            and _same(u["box"], data["marker_box"]) and u["transparent_marker"]]) != 1:
        return None
    for expected in original:
        if not isinstance(expected, dict) or len([u for u in shapes if u["sha256"] == expected.get("sha256")
                and _same(u["box"], expected.get("box")) and u["order"] == expected.get("order")]) != 1:
            return None
    return data


def answers(markers, written, template, student, size, plain):
    result = {(m["category"], m["id"]): [] for m in markers}
    bad = (result, list(written), binding.REVIEW)
    if len(markers) != 1 or not student:
        return bad
    marker = markers[0]
    data = source(marker, template, size, plain)
    if (data is None or student.get("workspace_theme_signature") not in data["theme_signatures"]
            or student.get("workspace_resource_signature") not in data["resource_signatures"]
            or student.get("workspace_slide_style_signature") not in data["slide_style_signatures"]
            or student.get("workspace_prompt_style_signature") not in data["prompt_style_signatures"]
            or student.get("workspace_prompt_scene_signature") not in data["prompt_scene_signatures"]
            or student.get("workspace_media_signature") not in data["workspace_media_signatures"]):
        return bad
    for prompt in data["prompts"]:
        allowed = prompt.get("received_sha256", [prompt["sha256"]])
        if (not _digests(allowed, 16) or prompt["sha256"] not in allowed or len([u for u in student.get("workspace_prompt_shapes", []) if u["sha256"] in allowed
                and u["text"] == prompt["text"] and _same(u["box"], prompt["box"]) and u["visible_context"]]) != 1):
            return bad
    remaining = list(student.get("workspace_shapes", []))
    for expected in data["other_original_shapes"]:
        allowed = expected.get("received_sha256", [expected["sha256"]])
        if not isinstance(allowed, list) or not 1 <= len(allowed) <= 16 or expected["sha256"] not in allowed:
            return bad
        matches = [u for u in remaining if u["sha256"] in allowed and _same(u["box"], expected["box"])
                   and u["text"] == expected.get("text") and u["order"] == expected["order"]]
        if len(matches) != 1:
            return bad
        remaining.remove(matches[0])
    token = "[[%s:%s:%s]]" % (marker["category"], marker["code"], marker["id"])
    points = [u for u in remaining if u["text"] == token and _same(u["box"], data["marker_box"]) and u["transparent_marker"]]
    if len(points) != 1:
        return bad
    remaining.remove(points[0])
    workspace = data["workspace"]
    fields = [u for u in remaining if u["text_shape"] and u["visible_context"]
              and any(_same(u["box"], b) for b in workspace["received_boxes"])
              and u["paint_sha256"] == workspace["paint_sha256"]
              and u["body_style_sha256"] == workspace["body_style_sha256"]]
    if len(fields) != 1:
        return bad
    field = fields[0]
    if field["order"] != workspace.get("order"):
        return bad
    remaining.remove(field)
    lines = field["text"].split("\n")
    if not lines or lines[0] != workspace["heading"]:
        return bad
    response = "\n".join(lines[1:]).strip()
    owner = field
    if remaining:
        extra = data.get("extra_response")
        if response or len(remaining) != 1 or not isinstance(extra, dict):
            return bad
        owner = remaining[0]
        if (not owner["visible_context"] or not owner["text_shape"] or not _same(owner["box"], extra.get("box"))
                or owner["paint_sha256"] != extra.get("paint_sha256")
                or owner["body_style_sha256"] != extra.get("body_style_sha256")
                or owner["order"] <= field["order"]
                or not binding.rectangle(owner["box"], size)
                or owner["box"][0] < workspace["box"][0] or owner["box"][1] < workspace["box"][1]
                or owner["box"][0] + owner["box"][2] > workspace["box"][0] + workspace["box"][2]
                or owner["box"][1] + owner["box"][3] > workspace["box"][1] + workspace["box"][3]
                or any(_overlap(owner["box"], v["box"]) for v in data["other_original_shapes"])):
            return bad
        response = owner["text"].strip()
    if any(not _visible_text(u, data) for u in (field, owner)):
        return bad
    if response:
        normalized = lambda value: tuple(re.findall(r"\w+", value.lower()))
        tokens = normalized(response)
        given = [workspace["heading"]] + [p["text"] for p in data["prompts"]] + [u.get("text", "") for u in data["other_original_shapes"]]
        if not tokens:
            return result, [], "authenticated prefilled writing workspace"
        covered = set()
        for value in given:
            printed = normalized(value)
            if tokens == printed:
                covered.update(range(len(tokens)))
            for block in SequenceMatcher(None, tokens, printed, autojunk=False).get_matching_blocks():
                if block.size >= 4:
                    covered.update(range(block.a, block.a + block.size))
        copied = len(covered) == len(tokens)
        result[(marker["category"], marker["id"])].append(({
            "key": "verified-workspace-response", "text": response, "box": owner["box"],
            "verified_workspace_answer": response, "workspace_supplied_copy": copied}, " ".join(response.split())))
    return result, [], "authenticated prefilled writing workspace"
