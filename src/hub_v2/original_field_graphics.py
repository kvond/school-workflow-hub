"""Visible evidence in authenticated original worksheet graphics only."""
import io
from collections import Counter
import posixpath
import re
import xml.etree.ElementTree as E

from . import graphical_choice as choice, response_binding as rb

P, A = choice.P, choice.A
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"
MODES = {"moved-ellipse", "new-profile-image"}


def field_valid(field, size):
    if field.get("mode") == "moved-ellipse":
        options = field.get("options")
        return (field.get("given") == "" and isinstance(field.get("source_key"), str)
                and isinstance(options, list) and len(options) == 4
                and all(isinstance(o, dict) for o in options)
                and [o.get("label") for o in options] == list("ABCD")
                and all(rb.rectangle(o.get("box"), size) for o in options)
                and not any(choice._overlaps(a["box"], b["box"])
                            for i, a in enumerate(options) for b in options[i + 1:]))
    if field.get("mode") == "new-profile-image":
        media = field.get("original_media")
        return (rb.rectangle(field.get("image_region"), size) and isinstance(media, list)
                and all(isinstance(x, list) and len(x) == 2 and isinstance(x[0], str)
                        and isinstance(x[1], str) and re.fullmatch(r"[0-9a-f]{64}", x[1]) for x in media)
                and len({x[0] for x in media}) == len(media))
    return field.get("mode") is None


def circle_supported(node):
    # The original marker and zero-width typing run are technical metadata.
    # A typed letter never substitutes for visibly moving the original ellipse.
    return choice._circle(node, "", ())


def ellipse_contains(ellipse, rectangle):
    x, y, width, height = ellipse
    if width <= 0 or height <= 0:
        return False
    cx, cy = x + width / 2, y + height / 2
    return all(((px - cx) / (width / 2)) ** 2 + ((py - cy) / (height / 2)) ** 2 <= 1
               for px in (rectangle[0], rectangle[0] + rectangle[2])
               for py in (rectangle[1], rectangle[1] + rectangle[3]))


def circle_answer(field, unit, size, clean):
    if not unit.get("writing_surface", {}).get("circle_supported") or clean(unit.get("text", "")):
        return None
    box = unit.get("box")
    if not rb.rectangle(box, size) or list(box[2:]) != field["box"][2:]:
        return None
    if list(box) == field["box"]:
        return []  # A supplied parked ellipse is never a student choice.
    selected = [o for o in field["options"] if ellipse_contains(box, o["box"])]
    stroke = unit["writing_surface"]["stroke_width"] / 2
    painted = [box[0] - stroke, box[1] - stroke, box[2] + 2 * stroke, box[3] + 2 * stroke]
    touched = [o for o in field["options"] if choice._overlaps(painted, o["box"])]
    if len(selected) != 1 or len(touched) != 1:
        return None
    return [({**unit, "completion_only": "original raster option", "recorded_choice": selected[0]["label"]},
             "circled original option " + selected[0]["label"])]


def _declared_type(archive, target):
    root = E.fromstring(archive.read("[Content_Types].xml"))
    if root.tag != CT + "Types" or root.attrib or any(
            node.tag not in (CT + "Default", CT + "Override") or list(node)
            or set(node.attrib) != ({"Extension", "ContentType"} if node.tag == CT + "Default"
                                    else {"PartName", "ContentType"}) for node in root):
        return None
    overrides = [n.get("ContentType") for n in root if n.tag == CT + "Override" and n.get("PartName") == "/" + target]
    defaults = [n.get("ContentType") for n in root if n.tag == CT + "Default"
                and n.get("Extension") == target.rsplit(".", 1)[-1]]
    if len(overrides) > 1 or len(defaults) > 1:
        return None
    declarations = overrides or defaults
    return declarations[0] if len(declarations) == 1 else None


def _visible_payload(raw, mime):
    # Pillow is already a hash-pinned hosting dependency. Unknown/animated or
    # undecodable assets remain review cases rather than counted pictures.
    from PIL import Image
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if (image.format not in ("PNG", "JPEG") or mime != {"PNG": "image/png", "JPEG": "image/jpeg"}[image.format]
                    or getattr(image, "n_frames", 1) != 1 or min(image.size) < 16
                    or max(image.size) > 4096 or image.width * image.height > 8_000_000):
                return False
            image.load()
            sample = image.convert("RGBA")
            sample.thumbnail((96, 96))
            opaque = [p[:3] for p in sample.getdata() if p[3] >= 240]
            return (len(opaque) >= sample.width * sample.height * .5 and len(set(opaque)) >= 4
                    and Counter(opaque).most_common(1)[0][1] <= len(opaque) * .95
                    and any(max(p[n] for p in opaque) - min(p[n] for p in opaque) >= 32 for n in range(3)))
    except (OSError, ValueError, Image.DecompressionBombError):
        return False


def original_media_binding(archive, part, media):
    """Declared type and decoded format belong to each original raster role."""
    if not media:
        return [], True
    from PIL import Image
    relname = posixpath.dirname(part) + "/_rels/" + posixpath.basename(part) + ".rels"
    try:
        rels = E.fromstring(archive.read(relname))
    except (KeyError, E.ParseError):
        return [], False
    result, readable = [], True
    for rid, payload_sha in sorted(media.items()):
        matches = [r for r in rels if r.get("Id") == rid]
        mime, decoded = None, None
        if (len(matches) == 1 and matches[0].get("TargetMode") != "External"
                and matches[0].get("Type", "").endswith("/image")):
            target = posixpath.normpath(posixpath.join(posixpath.dirname(part), matches[0].get("Target", "")))
            try:
                if target.startswith("ppt/media/") and archive.getinfo(target).file_size <= 12_000_000:
                    mime = _declared_type(archive, target)
                    if mime in ("image/png", "image/jpeg"):
                        with Image.open(io.BytesIO(archive.read(target))) as image:
                            if image.width * image.height <= 16_000_000:
                                image.load()
                                expected = {"image/png": "PNG", "image/jpeg": "JPEG"}[mime]
                                if image.format == expected and getattr(image, "n_frames", 1) == 1:
                                    decoded = image.format
            except (KeyError, E.ParseError, OSError, ValueError, Image.DecompressionBombError):
                pass
        readable &= decoded is not None
        result.append([rid, payload_sha, mime, decoded])
    return result, readable


def read_pictures(tree, archive, part, box):
    result = []
    relname = posixpath.dirname(part) + "/_rels/" + posixpath.basename(part) + ".rels"
    try:
        rels = E.fromstring(archive.read(relname))
    except (KeyError, E.ParseError):
        rels = []
    originals_after_picture = False
    for order, node in enumerate(tree):
        if node.tag != P + "pic":
            if result and node.tag not in (P + "nvGrpSpPr", P + "grpSpPr"):
                originals_after_picture = True
            continue
        record = {"order": order, "supported": False}
        result.append(record)
        nv = node.find(P + "nvPicPr/" + P + "cNvPr")
        native = node.find(P + "nvPicPr")
        props, fill = node.find(P + "spPr"), node.find(P + "blipFill")
        xf = props.find(A + "xfrm") if props is not None else None
        blip = fill.find(A + "blip") if fill is not None else None
        if (node.attrib or nv is None or nv.get("hidden", "0") not in ("0", "false")
                or set(nv.attrib) - {"id", "name", "descr", "hidden"}
                or list(nv) or native is None or native.attrib
                or any(c.tag not in (P + "cNvPr", P + "cNvPicPr", P + "nvPr") for c in native)
                or any(len(native.findall(tag)) != 1 for tag in (P + "cNvPr", P + "cNvPicPr", P + "nvPr"))
                or any(list(c) for c in native if c.tag == P + "nvPr")
                or props is None or props.attrib or xf is None or xf.attrib
                or any(c.tag not in (A + "off", A + "ext") for c in xf)
                or any(len(xf.findall(tag)) != 1 for tag in (A + "off", A + "ext"))
                or set(xf.find(A + "off").attrib) != {"x", "y"}
                or set(xf.find(A + "ext").attrib) != {"cx", "cy"}
                or any(c.tag not in (A + "xfrm", A + "prstGeom") for c in props)
                or fill is None or fill.attrib or blip is None or list(blip)
                or set(blip.attrib) != {R + "embed"}
                or any(c.tag not in (A + "blip", A + "stretch") for c in fill)
                or fill.find(A + "stretch/" + A + "fillRect") is None
                or fill.find(A + "stretch/" + A + "fillRect").attrib
                or any(c.tag not in (P + "nvPicPr", P + "blipFill", P + "spPr") for c in node)):
            continue
        geom = props.find(A + "prstGeom")
        pic_props = native.find(P + "cNvPicPr")
        if (pic_props.attrib or any(n.tag != A + "picLocks" or list(n)
                or set(n.attrib) - {"noChangeAspect", "noGrouping"}
                or any(v not in ("0", "1", "false", "true") for v in n.attrib.values()) for n in pic_props)
                or native.find(P + "nvPr").attrib):
            continue
        if geom is not None and (geom.attrib != {"prst": "rect"}
                                 or any(c.tag != A + "avLst" or c.attrib or list(c) for c in geom)):
            continue
        rid = blip.get(R + "embed")
        relationships = [n for n in rels if n.get("Id") == rid]
        if len(relationships) != 1 or relationships[0].get("TargetMode") == "External" or not relationships[0].get("Type", "").endswith("/image"):
            continue
        target = posixpath.normpath(posixpath.join(posixpath.dirname(part), relationships[0].get("Target", "")))
        if not target.startswith("ppt/media/"):
            continue
        try:
            if archive.getinfo(target).file_size > 12_000_000:
                continue
            raw = archive.read(target)
            visible = _visible_payload(raw, _declared_type(archive, target))
        except (KeyError, E.ParseError, OSError, ValueError):
            continue
        record.update(supported=visible, relationship=rid, box=box(xf, (1, 1, 0, 0)))
    if originals_after_picture:
        for picture in result:
            picture["supported"] = False
    return result


def picture_answer(field, page, size):
    pictures = page.get("writing_new_pictures", [])
    media = page.get("writing_all_media", {})
    originals = dict(field["original_media"])
    if any(media.get(k) != v for k, v in originals.items()):
        return None
    new = {k: v for k, v in media.items() if k not in originals}
    if not pictures and not new:
        return []
    if len(pictures) != 1 or len(new) != 1:
        return None
    picture = pictures[0]
    region, location = field["image_region"], picture.get("box")
    if (picture.get("supported") is not True or picture.get("relationship") not in new
            or new[picture["relationship"]] in originals.values() or not rb.rectangle(location, size)
            or not choice._contains(region, location) or location[2] < .2 * region[2] or location[3] < .2 * region[3]):
        return None
    units = [u for u in page.get("units", []) if u.get("picture", {}).get("sha256") == new[picture["relationship"]]
             and list(u.get("box") or ()) == list(location)]
    if len(units) != 1:
        return None
    return [({**units[0], "completion_only": "new original profile image"}, "new visible profile picture")]
