"""Writing in authenticated original fields; metadata never supplies an answer."""
import copy
import difflib
import hashlib
import json
import re

from . import response_binding as rb
from . import workspace_binding as workspace
from . import original_field_graphics as graphics

KIND = "original-writing-fields"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
TOKEN = re.compile(r"\w+|[^\w\s]")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def native_surface(element):
    value = copy.deepcopy(element)
    value.pop("description", None)
    value.get("shape", {}).pop("text", None)
    return digest(value)


def xml_surface(element, movable=False):
    def value(node):
        attrs = dict(node.attrib)
        if node.tag == P + "cNvPr":
            for key in ("id", "name", "descr"):
                attrs.pop(key, None)
        if movable and node.tag == A + "off":
            attrs.pop("x", None); attrs.pop("y", None)
        return [node.tag, sorted(attrs.items()), node.text or "", [value(c) for c in node if c.tag != P + "txBody"]]
    body = element.find(P + "txBody")
    layout = [value(c) for c in body if c.tag in (A + "bodyPr", A + "lstStyle")] if body is not None else None
    # A new paragraph may repeat its authenticated original layout, but may
    # not introduce margins, indents, spacing, bullets or inherited typography.
    # Preserve the order of layout transitions as well as their exact values.
    paragraphs = []
    for paragraph in body.findall(A + "p") if body is not None else ():
        signature = [sorted(paragraph.attrib.items()),
                     [value(props) for props in paragraph.findall(A + "pPr")]]
        if not paragraphs or paragraphs[-1] != signature:
            paragraphs.append(signature)
    return digest([value(element), layout, paragraphs])


def _canonical(node):
    attrs = dict(node.attrib)
    if node.tag == P + "cNvPr":
        for key in ("id", "name", "descr"):
            attrs.pop(key, None)
    return [node.tag, sorted(attrs.items()), node.text or "", [_canonical(c) for c in node]]


def page_scene(tree, fields, allow_picture=False):
    """Every original object and painter order; only original field text may change."""
    out = []
    for node in tree:
        if node.tag in (P + "nvGrpSpPr", P + "grpSpPr"):
            continue
        if allow_picture and node.tag == P + "pic":
            continue  # The explicit image branch checks every new picture separately.
        nv = node.find(P + "nvSpPr/" + P + "cNvPr")
        name = nv.get("name") or "id" + nv.get("id", "") if nv is not None else None
        out.append(xml_surface(node, movable=fields.get(name) == "moved-ellipse")
                   if name in fields else digest(_canonical(node)))
    return out


def run_style(node):
    return digest(_canonical(node)) if node is not None else digest(None)


def visible(props, default):
    def leaf(tag):
        node = props.find(A + tag) if props is not None else None
        return node if node is not None else default.find(A + tag) if default is not None else None
    size = props.get("sz") if props is not None else None
    size = size or (default.get("sz") if default is not None else None)
    fill, no_fill, effects = leaf("solidFill"), leaf("noFill"), leaf("effectLst")
    alphas = [n.get("val") for n in fill.iter(A + "alpha")] if fill is not None else []
    rgb = fill.find(A + "srgbClr") if fill is not None else None
    return (no_fill is None and all(v == "100000" for v in alphas)
            and (rgb is None or rgb.get("val", "").upper() != "FFFFFF")
            and (size is None or size.isdigit() and int(size) >= 200)
            and (effects is None or len(effects) == 0))


def clean(text, plain):
    return plain(text).replace("\u200b", "").strip()


def tokens(text):
    return TOKEN.findall(text.lower())


def new_text(given, current):
    old, new = tokens(given), tokens(current)
    ranges = [(b, d) for tag, _, _, b, d in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
              if tag in ("insert", "replace")]
    if not ranges or not any(re.search(r"\w", word) for b, d in ranges for word in new[b:d]):
        return ""  # deleting supplied directions is not a newly authored answer
    spans = list(TOKEN.finditer(current))
    return " ".join(current[spans[ranges[0][0]].start():spans[ranges[-1][1] - 1].end()].split())


def read_surfaces(tree, name, modes):
    """Direct original shapes only; a grouped/hidden/unsupported field holds."""
    result = {}
    for shape in tree.findall(P + "sp"):
        key = name(shape, P + "nvSpPr")
        prop = shape.find(P + "nvSpPr/" + P + "cNvPr")
        hidden = prop is None or prop.get("hidden", "0") not in ("0", "false")
        supported = not shape.attrib and not hidden
        runs = []
        body = shape.find(P + "txBody")
        supported &= body is not None and all(c.tag in (A + "bodyPr", A + "lstStyle", A + "p") for c in body)
        for paragraph in body.findall(A + "p") if body is not None else ():
            default = paragraph.find(A + "endParaRPr")
            supported &= all(c.tag in (A + "pPr", A + "r", A + "br", A + "endParaRPr") for c in paragraph)
            for run in paragraph.findall(A + "r"):
                props = run.find(A + "rPr")
                supported &= all(c.tag in (A + "rPr", A + "t") for c in run)
                runs.append({"text": run.findtext(A + "t", ""), "visible": visible(props, default),
                             "style_sha256": run_style(props), "default_sha256": run_style(default)})
        record = {"sha256": xml_surface(shape, movable=modes.get(key) == "moved-ellipse"),
                  "supported": supported, "runs": runs,
                  "circle_supported": graphics.circle_supported(shape) if modes.get(key) == "moved-ellipse" else False}
        if record["circle_supported"]:
            record["stroke_width"] = int(shape.find(P + "spPr/" + A + "ln").get("w"))
        result[key] = record if key not in result else {"supported": False}
    return result


def attach(slide, tree, root, pictures, archive, box, name, *, part=None):
    units = slide["units"]
    boxes = {tuple(f["box"]) for u in units if u.get("binding", {}).get("kind") == KIND
             for f in (u["binding"].get("page_fields") if isinstance(u["binding"].get("page_fields"), list) else [])
             if isinstance(f, dict) and isinstance(f.get("box"), list) and len(f["box"]) == 4
             and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in f["box"])}
    fields = [f for u in units if u.get("binding", {}).get("kind") == KIND
              for f in (u["binding"].get("page_fields") if isinstance(u["binding"].get("page_fields"), list) else [])
              if isinstance(f, dict)]
    modes = {f["source_key"]: f.get("mode") for f in fields if isinstance(f.get("source_key"), str)}
    keys = {u["key"]: modes.get(u["key"]) for u in units if tuple(u.get("box") or ()) in boxes or u["key"] in modes}
    surfaces = read_surfaces(tree, name, modes)
    for unit in units:
        unit["writing_surface"] = surfaces.get(unit["key"], {"supported": False})
    resources = sorted([n, hashlib.sha256(archive.read(n)).hexdigest()
                        if archive.getinfo(n).file_size <= 2_000_000 else "UNREADABLE_STYLE"]
                       for n in archive.namelist()
                       if re.fullmatch(r"ppt/(?:theme|slideMasters|slideLayouts)/[^/]+\.xml", n))
    image_fields = [f for u in units if u.get("binding", {}).get("kind") == KIND
                    for f in (u["binding"].get("fields") if isinstance(u["binding"].get("fields"), list) else [])
                    if isinstance(f, dict) and f.get("mode") == "new-profile-image"]
    media = pictures
    if image_fields and graphics.field_valid(image_fields[0], (float('inf'), float('inf'))):
        wanted = dict(image_fields[0]["original_media"])
        media = {k: pictures.get(k) for k in wanted}
    media_binding, media_readable = graphics.original_media_binding(archive, part, media) if part else ([], not media)
    slide["writing_scene"] = {"shapes": page_scene(tree, keys, allow_picture=bool(image_fields)), "style": workspace.slide_style(tree, root),
                              "media": digest(media_binding), "media_readable": media_readable, "resources": digest(resources),
                              "resources_readable": all(value != "UNREADABLE_STYLE" for _, value in resources)}
    if image_fields:
        slide["writing_all_media"] = pictures
        slide["writing_new_pictures"] = graphics.read_pictures(tree, archive, part, box) if part else [{"supported": False}]


def source(marker, template, size, plain):
    data = marker.get("binding", {})
    if (not template or data.get("kind") != KIND or data.get("page_size") != list(size)
            or marker["category"] not in ("NOTES", "OPTIONAL")):
        return None
    fields = data.get("fields")
    if (not isinstance(fields, list) or not 1 <= len(fields) <= 2
            or not all(isinstance(f, dict) and isinstance(f.get("given"), str) and len(f["given"]) <= 4000
                       and rb.rectangle(f.get("box"), size)
                       and re.fullmatch(r"[0-9a-f]{64}", str(f.get("pptx_surface_sha256", "")))
                       and isinstance(f.get("visible_styles"), list) and f["visible_styles"]
                       and all(re.fullmatch(r"[0-9a-f]{64}", str(v)) for v in f["visible_styles"])
                       and isinstance(f.get("default_styles"), list) and f["default_styles"]
                       and all(re.fullmatch(r"[0-9a-f]{64}", str(v)) for v in f["default_styles"])
                       and graphics.field_valid(f, size)
                       for f in fields)):
        return None
    page_fields = data.get("page_fields")
    if (not isinstance(page_fields, list) or not 1 <= len(page_fields) <= 50
            or not all(isinstance(f, dict) and rb.rectangle(f.get("box"), size)
                       and re.fullmatch(r"[0-9a-f]{64}", str(f.get("pptx_surface_sha256", ""))) for f in page_fields)
            or len({tuple(f["box"]) for f in page_fields}) != len(page_fields)
            or any(not any(p["box"] == f["box"] and p["pptx_surface_sha256"] == f["pptx_surface_sha256"]
                           for p in page_fields) for f in fields)):
        return None
    for field in page_fields:
        units = [u for u in template.get("units", []) if list(u.get("box") or ()) == field["box"] and not u.get("picture")]
        if (len(units) != 1 or units[0].get("writing_surface", {}).get("sha256") != field["pptx_surface_sha256"]
                or field.get("mode") not in (None, *graphics.MODES)
                or (field.get("mode") == "moved-ellipse" and
                    (units[0]["key"] != field.get("source_key") or not units[0]["writing_surface"].get("circle_supported")))):
            return None
    token = "[[%s:%s:%s]]" % (marker["category"], marker["code"], marker["id"])
    trusted = [u for u in template.get("units", []) if u.get("binding") == data and token in u.get("text", "")]
    if len(trusted) != 1 or list(trusted[0].get("box") or ()) != fields[0]["box"]:
        return None
    result = []
    for field in fields:
        found = [u for u in template.get("units", []) if not u.get("picture") and list(u.get("box") or ()) == field["box"]]
        if len(found) != 1 or clean(found[0].get("text", ""), plain) != field["given"]:
            return None
        unit = found[0]
        if unit.get("writing_surface", {}).get("sha256") != field["pptx_surface_sha256"]:
            return None
        if field.get("mode") == "moved-ellipse" and (unit["key"] != field["source_key"]
                or graphics.circle_answer(field, unit, size, lambda text: clean(text, plain)) != []):
            return None
        if field.get("mode") == "new-profile-image" and (template.get("writing_new_pictures")
                or sorted(template.get("writing_all_media", {}).items()) != [tuple(x) for x in field["original_media"]]
                or not graphics.choice._contains(field["image_region"], field["box"])
                or any(p["box"] != field["box"] and graphics.choice._overlaps(p["box"], field["image_region"])
                       for p in page_fields)):
            return None
        result.append(field)
    if any(max(a["box"][0], b["box"][0]) < min(a["box"][0] + a["box"][2], b["box"][0] + b["box"][2])
           and max(a["box"][1], b["box"][1]) < min(a["box"][1] + a["box"][3], b["box"][1] + b["box"][3])
           for i, a in enumerate(fields) for b in fields[i + 1:]):
        return None
    supplied = data.get("supplied_text")
    actual = sorted({clean(u.get("text", ""), plain) for u in template.get("units", [])
                     if clean(u.get("text", ""), plain) and "[[MARKER-INVENTORY:" not in u.get("text", "")})
    if not isinstance(supplied, list) or supplied != actual:
        return None
    return result


def same_scene(data, page):
    scene = page.get("writing_scene", {})
    return (scene.get("resources_readable") is True and scene.get("media_readable") is True
            and scene.get("shapes") == data.get("page_scene") and scene.get("style") == data.get("page_style")
            and scene.get("media") == data.get("page_media") and scene.get("resources") == data.get("resources"))


def answers(markers, written, template, student, size, plain):
    result = {(m["category"], m["id"]): [] for m in markers}
    bad = (result, list(written), rb.REVIEW)
    if not student or len(result) != len(markers):
        return bad
    occupied, responses = set(), []
    for marker in markers:
        fields = source(marker, template, size, plain)
        if (fields is None or (fields[0].get("mode") != "moved-ellipse" and list(marker.get("box") or ()) != fields[0]["box"])
                or not same_scene(marker["binding"], template) or not same_scene(marker["binding"], student)):
            return bad
        for field in fields:
            box = field["box"]
            if tuple(box) in occupied:
                return bad
            occupied.add(tuple(box))
            found = [u for u in student.get("units", []) if not u.get("picture")
                     and (u["key"] == field["source_key"] if field.get("mode") == "moved-ellipse"
                          else list(u.get("box") or ()) == box)]
            if len(found) != 1:
                return bad
            unit = found[0]
            surface = unit.get("writing_surface", {})
            if surface.get("sha256") != field["pptx_surface_sha256"] or surface.get("supported") is not True:
                return bad
            current = clean(unit.get("text", ""), plain)
            responses.append(unit)
            if field.get("mode") in graphics.MODES:
                graphical = (graphics.circle_answer(field, unit, size, lambda text: clean(text, plain))
                             if field["mode"] == "moved-ellipse" else graphics.picture_answer(field, student, size))
                if graphical is None:
                    return bad
                if field["mode"] == "new-profile-image":
                    # The new visible image is this field's response, including
                    # when its center lies inside the original placeholder.
                    responses.extend(u for u in student.get("units", []) if any(
                        u["key"] == evidence["key"] and u.get("picture")
                        for evidence, _ in graphical))
                result[(marker["category"], marker["id"])].extend(graphical)
                continue
            words = new_text(field["given"], current)
            if not words:
                continue
            original = next(u for u in template["units"] if list(u.get("box") or ()) == box and not u.get("picture"))
            # The trusted teacher's visible zero-width final run provides the
            # actual normal typing style after Google serializes this field.
            # It cannot supply student words or authorize an invisible style.
            typing = {(r.get("style_sha256"), r.get("default_sha256"))
                      for r in original.get("writing_surface", {}).get("runs", [])
                      if r.get("text") == "\u200b" and r.get("visible") is True}
            if any(clean(r.get("text", ""), plain) and new_text(field["given"], clean(r["text"], plain))
                   and (not r.get("visible") or
                        ((r.get("style_sha256"), r.get("default_sha256")) not in typing and
                         (r.get("style_sha256") not in field["visible_styles"]
                          or r.get("default_sha256") not in field["default_styles"])))
                   for r in surface.get("runs", [])):
                return bad
            supplied = [tokens(t) for t in marker["binding"]["supplied_text"]]
            copied = tokens(current) in supplied or tokens(words) in supplied
            evidence = {**unit, "verified_workspace_answer": words}
            if copied:
                evidence["workspace_supplied_copy"] = True
            result[(marker["category"], marker["id"])].append((evidence, words))
    for unit, _ in written:
        if unit in responses:
            continue
        if unit.get("box") and any(rb.inside(rb.center(unit["box"]), box) for box in occupied):
            return bad
    return result, [item for item in written if item[0] not in responses], "authenticated original writing fields"
