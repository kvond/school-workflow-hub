"""Exact native teacher text, styles and geometry; submitted copies use PPTX.

This reads an official presentations.get response without fetching images.
Explicit response activities and tables still require the original export.
"""
import copy
import math

from . import response_binding


class Unreadable(ValueError):
    pass


class _CollapsedGeometry(Unreadable):
    pass


def _empty_custom_decoration(element):
    """Recognize only an unlabelled CUSTOM shape containing paint, not content.

    A blank text box or placeholder can be a response region. Unknown native
    fields, links and even an empty text object therefore keep the normal hold.
    The complete original node remains in ``native_source`` in either case.
    """
    if set(element) != {"objectId", "size", "transform", "shape"}:
        return False
    shape = element.get("shape")
    if (not isinstance(shape, dict) or shape.get("shapeType") != "CUSTOM"
            or set(shape) - {"shapeType", "shapeProperties"}):
        return False
    color = {"rgbColor": {"red": None, "green": None, "blue": None}, "themeColor": None}
    fill = {"solidFill": {"color": color, "alpha": None}, "propertyState": None}
    dimension = {"magnitude": None, "unit": None}
    transform = {key: None for key in ("scaleX", "scaleY", "shearX", "shearY", "translateX", "translateY", "unit")}
    styles = {"shapeBackgroundFill": fill,
              "outline": {"outlineFill": fill, "weight": dimension, "dashStyle": None, "propertyState": None},
              "shadow": {"type": None, "transform": transform, "alignment": None, "blurRadius": dimension,
                         "color": color, "alpha": None, "rotateWithShape": None, "propertyState": None},
              "contentAlignment": None,
              "autofit": {"autofitType": None, "fontScale": None, "lineSpacingReduction": None}}

    def paint_only(value, fields):
        if fields is None:
            return isinstance(value, (str, bool, int, float)) and not (isinstance(value, float) and not math.isfinite(value))
        return (isinstance(value, dict) and not set(value) - set(fields)
                and all(paint_only(item, fields[key]) for key, item in value.items()))

    return paint_only(shape.get("shapeProperties", {}), styles)


def _mapping(value):
    if not isinstance(value, dict):
        raise Unreadable("The native teacher source has incomplete element metadata.")
    return value


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > 1e12:
        raise Unreadable("The native teacher source has unusable geometry.")
    return value


def _factor(unit):
    if unit not in ("EMU", "PT"):
        raise Unreadable("The native teacher source has unsupported geometry units.")
    return 12700 if unit == "PT" else 1


def _dimension(value):
    value = _mapping(value)
    number = _number(value.get("magnitude", 0)) * _factor(value.get("unit"))
    if number <= 0:
        raise Unreadable("The native teacher source has an empty dimension.")
    return number


def _transform(value):
    if not isinstance(value, dict) or not value:
        raise Unreadable("The native teacher source has no readable transform.")
    unit = _factor(value.get("unit"))
    return tuple(_number(value.get(key, 0)) * (unit if key.startswith("translate") else 1)
                 for key in ("scaleX", "shearX", "shearY", "scaleY", "translateX", "translateY"))


def _compose(parent, child):
    a, b, c, d, x, y = parent
    e, f, g, h, u, v = child
    return (a * e + b * g, a * f + b * h, c * e + d * g, c * f + d * h,
            a * u + b * v + x, c * u + d * v + y)


def _box(element, matrix):
    size = _mapping(element.get("size", {}))
    width, height = _dimension(size.get("width", {})), _dimension(size.get("height", {}))
    a, b, c, d, x, y = matrix
    corners = [(a * u + b * v + x, c * u + d * v + y)
               for u, v in ((0, 0), (width, 0), (0, height), (width, height))]
    left, right = min(p[0] for p in corners), max(p[0] for p in corners)
    top, bottom = min(p[1] for p in corners), max(p[1] for p in corners)
    if right <= left or bottom <= top:
        raise _CollapsedGeometry("The native teacher source has collapsed geometry.")
    return left, top, right - left, bottom - top


def read(presentation, *, presentation_id, revision, max_slides=200, max_slide_bytes=5_000_000,
         max_deck_bytes=60_000_000):
    """Teacher-only model retaining every slide, text run, native style and box."""
    import json
    if not isinstance(presentation, dict) or presentation.get("presentationId") != presentation_id:
        raise Unreadable("The native teacher source does not match the exact linked deck.")
    if not str(revision or "").strip():
        raise Unreadable("The native teacher source has no verified Drive revision.")
    if len(json.dumps(presentation).encode()) > max_deck_bytes:
        raise Unreadable("The complete native teacher source is too large to read.")
    pages = presentation.get("slides")
    if not isinstance(pages, list) or not pages or len(pages) > max_slides:
        raise Unreadable("The complete native teacher slide list could not be read.")
    size = _mapping(presentation.get("pageSize", {}))
    size = (_dimension(size.get("width", {})), _dimension(size.get("height", {})))
    seen, slides = set(), []

    def walk(elements, units, parent=(1, 0, 0, 1, 0, 0), depth=0):
        if not isinstance(elements, list) or depth > 20:
            raise Unreadable("The native teacher source has unusable groups.")
        for element in elements:
            element = _mapping(element)
            key = element.get("objectId")
            if not isinstance(key, str) or not key or key in seen:
                raise Unreadable("The native teacher source has duplicate or missing object IDs.")
            seen.add(key)
            if not isinstance(element.get("description", ""), str):
                raise Unreadable("The native teacher source has unreadable activity metadata.")
            if element.get("description") and response_binding.description(element["description"]) is not None:
                raise Unreadable("This native teacher activity needs its original export to verify response evidence.")
            if "table" in element:
                raise Unreadable("This native teacher table needs its original export to verify response geometry.")
            if "elementGroup" in element:
                matrix = _compose(parent, _transform(element.get("transform")))
                walk(_mapping(element["elementGroup"]).get("children"), units, matrix, depth + 1)
                continue
            if not any(kind in element for kind in ("shape", "image")):
                continue
            if "shape" in element and "image" in element:
                raise Unreadable("The native teacher source has ambiguous element metadata.")
            if "image" in element:
                _mapping(element["image"])
            matrix = _compose(parent, _transform(element.get("transform")))
            try:
                box = _box(element, matrix)
            except _CollapsedGeometry:
                # A zero-width painted CUSTOM leaf has no response area, even
                # inside a valid group. Never skip its parent or siblings, or
                # zero-height regions, text/metadata or unsupported content.
                if (matrix[0] == matrix[1] == matrix[2] == 0 and matrix[3] != 0
                        and _empty_custom_decoration(element)):
                    continue
                raise
            shape = _mapping(element.get("shape", {}))
            text = _mapping(shape.get("text", {})).get("textElements", [])
            if not isinstance(text, list):
                raise Unreadable("The native teacher text runs could not be read.")
            chunks = [_mapping(_mapping(run).get("textRun", {})).get("content", "") for run in text]
            if any(not isinstance(chunk, str) for chunk in chunks):
                raise Unreadable("The native teacher text runs could not be read.")
            value = "".join(chunks).strip()
            unit = {"key": key, "text": value, "box": box,
                    "title": _mapping(shape.get("placeholder", {})).get("type") in ("TITLE", "CENTERED_TITLE"),
                    "native_style": copy.deepcopy(shape if "shape" in element else element["image"]),
                    "native_transform": copy.deepcopy(element["transform"]),
                    "native_absolute_transform": matrix}
            if "image" in element:
                unit["picture"] = {"sha256": None, "appearance": {"native": copy.deepcopy(element["image"])}}
            units.append(unit)

    for number, page in enumerate(pages, 1):
        page = _mapping(page)
        if len(json.dumps(page).encode()) > max_slide_bytes:
            raise Unreadable("One native teacher slide is too large to read.")
        units = []
        walk(page.get("pageElements", []), units)
        slides.append({"number": number, "units": units})
    return {"slides": slides, "size": size, "source_kind": "native_teacher_source",
            "native_source_id": presentation_id, "native_source_revision": str(revision),
            "native_source": copy.deepcopy(presentation)}
