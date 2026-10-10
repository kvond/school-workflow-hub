"""Explicit graphical completion bindings; never a source of student writing."""
import json
import math
import re


PREFIX = "HUB_COMPLETION_BINDING_V1\n"
HEADER = PREFIX.rstrip("\n")
REVIEW = "Response binding needs review"


def description(raw):
    if not isinstance(raw, str) or not raw.startswith(HEADER):
        return None
    try:
        suffix = raw[len(HEADER):]
        value = json.loads(suffix) if len(raw) <= 40_000 and suffix and suffix[0].isspace() else None
    except (ValueError, TypeError):
        value = None
    return value if isinstance(value, dict) else {"invalid": True}


def center(box):
    return (box[0] + box[2] / 2, box[1] + box[3] / 2)


def inside(point, box):
    return box[0] <= point[0] <= box[0] + box[2] and box[1] <= point[1] <= box[1] + box[3]


def rectangle(value, size):
    return (isinstance(value, (list, tuple)) and len(value) == 4
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in value)
            and value[2] > 0 and value[3] > 0 and value[0] >= 0 and value[1] >= 0
            and value[0] + value[2] <= size[0] and value[1] + value[3] <= size[1])


def same_box(a, b, size):
    return a and b and max(abs(x - y) for x, y in zip(a, b)) <= 0.001 * max(size)


def tiny_points(markers, size):
    """Only independent marker-only points; never BANK, shared or off-canvas lists."""
    return (bool(markers) and all(m.get("standalone") and m["category"] != "BANK"
            and not m.get("binding") and rectangle(m.get("box"), size)
            and m["box"][2] <= .03 * size[0] and m["box"][3] <= .01 * size[1] for m in markers)
            and len({center(m["box"]) for m in markers}) == len(markers))


def point_answers(markers, written, template, size, *, marker_pattern=None):
    result = {(m["category"], m["id"]): [] for m in markers}
    uncoded = []
    used = set()
    ambiguous = False
    for item in written:
        unit, words = item
        box = unit.get("box")
        candidates = [m for m in markers if box and inside(center(m["box"]), box)]
        # A graphical activity can expose both its silhouette and name line as
        # locations for the same response. They remain one authentic field.
        regions = {(m["category"], m["id"], "activity" if "response_region" in m else center(m["box"])) for m in candidates}
        if len(regions) == 1 and len(candidates) > 1:
            candidates = [next((m for m in candidates if m.get("graphical_response_box")), candidates[0])]
        # Existing response boxes may contain placeholders or their old code. A
        # thin new text box can fill a graphical underline absent from text parsing.
        anchored = box and any(same_box(box, u.get("box"), size) for u in (template or {}).get("units", []))
        if box and len(candidates) == 1 and marker_pattern is not None and not anchored:
            marker = candidates[0]
            anchored = any(u.get("box") and inside(center(marker["box"]), u["box"])
                           and any(m.group(1) == marker["category"] and m.group(3).strip() == marker["id"]
                                   for m in marker_pattern.finditer(u.get("text", "")))
                           for u in (template or {}).get("units", []))
        if box and len(candidates) == 1 and not anchored:
            point = center(candidates[0]["box"])
            blank = [u["box"] for u in (template or {}).get("units", []) if not u.get("picture")
                     and not u.get("text", "").strip() and u.get("box") and inside(point, u["box"])
                     and u["box"][2] * u["box"][3] < .6 * size[0] * size[1]]
            if blank:
                region = min(blank, key=lambda a: a[2] * a[3])
                margin = .001 * max(size)
                anchored = (box[0] >= region[0] - margin and box[1] >= region[1] - margin
                            and box[0] + box[2] <= region[0] + region[2] + margin
                            and box[1] + box[3] <= region[1] + region[3] + margin)
        thin = box and box[3] <= .03 * size[1]
        graphical = (box and len(candidates) == 1 and candidates[0].get("graphical_response_box")
                     and box[3] <= candidates[0]["graphical_response_box"][3] + .001 * max(size))
        if len(candidates) == 1 and (anchored or thin or graphical) and not words.startswith("moved: ") and not unit.get("picture"):
            m = candidates[0]
            point = center(m["box"])
            response_key = (m["category"], m["id"], m["response_region"]) if "response_region" in m else point
            if response_key in used:
                ambiguous = True
            used.add(response_key)
            result[(m["category"], m["id"])].append(item)
        else:
            uncoded.append(item)
            if len(candidates) > 1:
                ambiguous = True
    if ambiguous:
        return {key: [] for key in result}, list(written), REVIEW
    return result, uncoded, "independent response points"


def printed_prompts(student, template, markers, size):
    """Retained printed prompt objects remain a baseline for copied text.

    Only new explicit field bindings use this additional corpus. A response
    object, new student text, or a picture is never promoted to printed text.
    """
    if not student or not template:
        return []
    if tiny_points(markers, size):
        points = [center(m["box"]) for m in markers]
    elif len(markers) == 1 and markers[0].get("binding", {}).get("kind") == "picture-activity":
        targets = markers[0]["binding"].get("targets", [])
        points = [center(t[key]) for t in targets if isinstance(t, dict)
                  for key in ("box", "response_box") if rectangle(t.get(key), size)]
    else:
        return []
    texts = []
    for unit in student.get("units", []):
        # Google export can renumber names and reuse a printed prompt's name
        # for an answer. An exact unique rectangle is the source identity.
        old = [u for u in template.get("units", []) if not u.get("picture")
               and same_box(unit.get("box"), u.get("box"), size)]
        if (not unit.get("picture") and len(old) == 1 and old[0].get("text", "").strip()
                and not any(inside(point, old[0]["box"]) for point in points)):
            texts.append(unit.get("text", ""))
    return texts


def original_slide_matches(markers, template, size, plain):
    """A new activity ID can match only its explicit authentic source objects."""
    if len(markers) != 1 or not markers[0].get("standalone") or markers[0]["category"] != "NOTES":
        return False
    data = markers[0].get("binding", {})
    units = template.get("units", [])
    if data.get("kind") == "numeric-record-group":
        return numeric_source(data, template, size, plain) is not None
    if data.get("kind") == "raster-card-activity":
        return raster_source(data, template, size, plain) is not None

    def text_at(text, box):
        found = [u for u in units if not u.get("picture") and plain(u.get("text", "")) == text]
        return rectangle(box, size) and len(found) == 1 and same_box(found[0].get("box"), box, size)

    def picture_at(digest, box, unique=False):
        found = [u for u in units if u.get("picture", {}).get("sha256") == digest
                 and (unique or same_box(u.get("box"), box, size))]
        return (isinstance(digest, str) and len(digest) == 64 and rectangle(box, size)
                and len(found) == 1 and same_box(found[0].get("box"), box, size))

    targets = data.get("targets")
    if not isinstance(targets, list) or not 1 <= len(targets) <= 40 or not all(isinstance(t, dict) for t in targets):
        return False
    if data.get("kind") == "drag-sort":
        cards = data.get("cards")
        return (isinstance(cards, list) and 1 <= len(cards) <= 40 and len(targets) <= 12
                and all(isinstance(c, dict) and isinstance(c.get("text"), str) and c["text"].strip()
                        and text_at(c["text"], c.get("box")) for c in cards)
                and all(isinstance(t.get("label"), str) and text_at(t["label"], t.get("source_box")) for t in targets))
    if data.get("kind") == "picture-activity":
        cards = data.get("pictures")
        return (isinstance(cards, list) and 1 <= len(cards) <= 40
                and all(isinstance(c, dict) and picture_at(c.get("sha256"), c.get("box"), unique=True) for c in cards)
                and all(picture_at(t.get("target_picture_sha256"), t.get("source_box"))
                        and picture_at(t.get("response_picture_sha256"), t.get("source_response_box")) for t in targets))
    return False


def numeric_source(data, template, size, plain):
    """The three literal pulse blanks are one original empirical activity."""
    text, placeholder = data.get("source_text"), data.get("placeholder")
    if (not isinstance(text, str) or len(text) > 4000 or placeholder != "____"
            or data.get("fields") != 3 or data.get("unit") != "bpm"
            or text.count(placeholder) != 3 or not rectangle(data.get("source_box"), size)
            or not rectangle(data.get("box"), size)):
        return None
    parts = text.split(placeholder)
    if not all(part.lstrip().startswith("bpm") for part in parts[1:]):
        return None
    sources = [u for u in (template or {}).get("units", []) if not u.get("picture")
               and plain(u.get("text", "")) == text and same_box(u.get("box"), data["source_box"], size)]
    return sources[0] if len(sources) == 1 else None


def numeric_answers(markers, written, template, student, size, plain):
    """Extract only numbers replacing actual blanks, never instruction numbers."""
    result = {(m["category"], m["id"]): [] for m in markers}
    bad = (result, list(written), REVIEW)
    if len(markers) != 1 or not student:
        return bad
    marker = markers[0]
    data = marker.get("binding", {})
    if marker["category"] != "NOTES" or not marker.get("standalone") or numeric_source(data, template, size, plain) is None:
        return bad
    actual = [u for u in student.get("units", []) if not u.get("picture") and same_box(u.get("box"), data["box"], size)]
    if len(actual) != 1:
        return bad
    unit = actual[0]
    # Whitespace wrapping is immaterial; the printed instruction words and all
    # three measured slots must otherwise remain exact. No physiology grading.
    skeleton = " ".join(data["source_text"].split())
    chunks = skeleton.split(data["placeholder"])
    value = r"(____|[-+]?\d{1,16}(?:\.\d{1,8})?)"
    pattern = value.join(re.escape(part) for part in chunks)
    match = re.fullmatch(pattern, " ".join(plain(unit.get("text", "")).split()))
    if match is None:
        return bad
    loose = []
    for item in written:
        other, _ = item
        if other is unit:
            continue
        if other.get("box") and inside(center(other["box"]), data["box"]):
            return bad  # An overlay has no authenticated individual blank location.
        loose.append(item)
    for field, measured in enumerate(match.groups(), 1):
        if measured != data["placeholder"]:
            result[(marker["category"], marker["id"])].append(({
                **unit, "completion_only": "numeric record", "recorded_field": field,
                "recorded_value": measured}, measured))
    return result, loose, "original numeric response group"


def raster_source(data, template, size, plain):
    """Raster geometry must come from the authenticated teacher's own manifest."""
    if not template or data.get("kind") != "raster-card-activity" or data.get("page_size") != list(size):
        return None
    trusted = [u for u in template.get("units", []) if u.get("binding", {}).get("kind") == "raster-card-activity"]
    if len(trusted) != 1 or trusted[0]["binding"] != data:
        return None
    background = data.get("background")
    if (not isinstance(background, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(background.get("sha256", "")))
            or template.get("background") != background):
        return None
    cards, targets, given = data.get("cards"), data.get("targets"), data.get("given_cards")
    if (not isinstance(cards, list) or len(cards) != 14 or not isinstance(targets, list) or len(targets) != 14
            or not isinstance(given, list) or len(given) != 1):
        return None
    labels = set()
    for card in cards + given:
        if (not isinstance(card, dict) or not isinstance(card.get("text"), str) or not card["text"].strip()
                or card["text"] in labels or not rectangle(card.get("box"), size)):
            return None
        found = [u for u in template.get("units", []) if not u.get("picture") and plain(u.get("text", "")) == card["text"]]
        if len(found) != 1 or not same_box(found[0].get("box"), card["box"], size):
            return None
        labels.add(card["text"])
    if (type(data.get("given_target")) is not int or not 1 <= data["given_target"] <= 15
            or not all(isinstance(t, dict) and type(t.get("number")) is int and rectangle(t.get("box"), size) for t in targets)
            or {t["number"] for t in targets} != set(range(1, 16)) - {data.get("given_target")}):
        return None
    areas = [t["box"] for t in targets]
    if (len({tuple(box) for box in areas}) != 14 or any(inside(center(c["box"]), box) for c in cards + given for box in areas)
            or any(max(a[0], b[0]) < min(a[0] + a[2], b[0] + b[2])
                   and max(a[1], b[1]) < min(a[1] + a[3], b[1] + b[3])
                   for n, a in enumerate(areas) for b in areas[n + 1:])):
        return None
    return cards, targets, given


def raster_answers(markers, written, template, student, size, plain):
    """Only a unique unchanged original tray card moved into a real raster slot."""
    result = {(m["category"], m["id"]): [] for m in markers}
    bad = (result, list(written), REVIEW)
    if len(markers) != 1 or not student:
        return bad
    marker = markers[0]
    data = marker.get("binding", {})
    source = raster_source(data, template, size, plain)
    if marker["category"] != "NOTES" or not marker.get("standalone") or source is None or student.get("background") != data["background"]:
        return bad
    cards, targets, given = source
    actual, off_target = {}, []
    for card in cards + given:
        found = [u for u in student.get("units", []) if not u.get("picture") and plain(u.get("text", "")) == card["text"]]
        if len(found) != 1 or not rectangle(found[0].get("box"), size):
            return bad
        actual[card["text"]] = found[0]
    if any(not same_box(actual[c["text"]]["box"], c["box"], size) for c in given):
        return bad
    for card in cards:
        unit = actual[card["text"]]
        if math.dist(center(unit["box"]), center(card["box"])) <= .03 * max(size):
            continue
        landed = [t for t in targets if inside(center(unit["box"]), t["box"])]
        if len(landed) > 1:
            return bad
        if not landed:
            off_target.append(({**unit, "completion_only": "card placement"}, "Off-target original card placement"))
        else:
            result[(marker["category"], marker["id"])].append(({**unit, "completion_only": "card placement"}, "card placement"))
    for unit, _ in written:
        if unit in actual.values():
            continue
        if unit.get("box") and any(inside(center(unit["box"]), t["box"]) for t in targets):
            return bad  # Retyping or an unknown response needs review, never a zero.
    if any(u.get("picture") and u.get("box") and any(inside(center(u["box"]), t["box"]) for t in targets)
           for u in student.get("units", [])):
        return bad
    if off_target and not any(result.values()):
        return bad
    return result, off_target, "original raster card placement"


def drag_answers(markers, written, template, student, size, plain):
    """Validate original tray cards and actual role rectangles before any credit."""
    result = {(m["category"], m["id"]): [] for m in markers}
    bad = (result, list(written), REVIEW)
    if len(markers) != 1 or not template or not student:
        return bad
    marker = markers[0]
    data = marker.get("binding", {})
    if marker["category"] != "NOTES" or not marker.get("standalone") or data.get("kind") != "drag-sort":
        return bad
    cards, targets = data.get("cards"), data.get("targets")
    if not isinstance(cards, list) or not 1 <= len(cards) <= 40 or not isinstance(targets, list) or not 1 <= len(targets) <= 12:
        return bad
    before, after = template.get("units", []), student.get("units", [])
    allowed = {}
    for card in cards:
        if not isinstance(card, dict) or not isinstance(card.get("text"), str) or not card["text"].strip() or not rectangle(card.get("box"), size):
            return bad
        text = card["text"]
        originals = [u for u in before if plain(u.get("text", "")) == text]
        if text in allowed or len(originals) != 1 or not same_box(originals[0].get("box"), card["box"], size):
            return bad
        if len([u for u in after if plain(u.get("text", "")) == text]) != 1:
            return bad
        allowed[text] = card["box"]
    areas = []
    for target in targets:
        if not isinstance(target, dict) or not isinstance(target.get("label"), str) or not rectangle(target.get("box"), size) or not rectangle(target.get("source_box"), size):
            return bad
        original = [u for u in before if plain(u.get("text", "")) == target["label"]]
        actual = [u for u in after if plain(u.get("text", "")) == target["label"]]
        if len(original) != 1 or len(actual) != 1 or not same_box(original[0].get("box"), target["source_box"], size) or not same_box(actual[0].get("box"), target["box"], size):
            return bad
        areas.append(target["box"])
    if any(inside(center(card), target["source_box"]) for card in allowed.values() for target in targets):
        return bad
    off_target = []
    for item in written:
        unit, words = item
        text, box = plain(unit.get("text", "")), unit.get("box")
        if not words.startswith("moved: ") or text not in allowed or not rectangle(box, size):
            continue
        matches = [u for u in after if plain(u.get("text", "")) == text]
        source = allowed[text]
        if len(matches) != 1:
            return bad
        if math.dist(center(box), center(source)) <= .03 * max(size):
            continue
        landed = [a for a in areas if inside(center(box), a)]
        if not landed:
            off_target.append(({**unit, "completion_only": "card placement"}, "Off-target original card placement"))
            continue
        if len(landed) > 1:
            return bad
        if len(landed) == 1:
            result[(marker["category"], marker["id"])].append(({**unit, "completion_only": "card placement"}, "card placement"))
    # Neither revised labels, prompts nor typed organism names are substitutes
    # for the original unchanged card being moved out of its authentic tray.
    if off_target and not any(result.values()):
        return bad
    return result, off_target, "original card placement"


def picture_answers(markers, written, template, student, size):
    """Only allowed original media moved into an authentic response silhouette."""
    result = {(m["category"], m["id"]): [] for m in markers}
    bad = (result, list(written), REVIEW)
    if not template or not student or not markers:
        return bad
    before = [u for u in template.get("units", []) if u.get("picture")]
    after = [u for u in student.get("units", []) if u.get("picture")]
    areas, allowed, points = [], None, []
    aggregate = len(markers) == 1 and markers[0].get("binding", {}).get("kind") == "picture-activity"
    if aggregate:
        targets = markers[0]["binding"].get("targets")
        if not isinstance(targets, list) or not 1 <= len(targets) <= 40:
            return bad
        regions = [(markers[0], data, markers[0]["binding"].get("pictures")) for data in targets]
    else:
        regions = [(m, m.get("binding", {}), m.get("binding", {}).get("pictures")) for m in markers]
    for marker, data, cards in regions:
        if marker["category"] != "NOTES" or not marker.get("standalone") or not isinstance(data, dict) or (not aggregate and data.get("kind") != "picture-slot"):
            return bad
        if not rectangle(data.get("box"), size) or not rectangle(data.get("source_box"), size):
            return bad
        target_hash = data.get("target_picture_sha256")
        source_target = [u for u in before if u["picture"].get("sha256") == target_hash and same_box(u.get("box"), data["source_box"], size)]
        actual_target = [u for u in after if u["picture"].get("sha256") == target_hash and same_box(u.get("box"), data["box"], size)]
        if not target_hash or len(source_target) != 1 or len(actual_target) != 1 or source_target[0]["picture"] != actual_target[0]["picture"]:
            return bad
        if aggregate:
            if not rectangle(data.get("response_box"), size) or not rectangle(data.get("source_response_box"), size):
                return bad
            line_hash = data.get("response_picture_sha256")
            old_line = [u for u in before if u["picture"].get("sha256") == line_hash and same_box(u.get("box"), data["source_response_box"], size)]
            actual_line = [u for u in after if u["picture"].get("sha256") == line_hash and same_box(u.get("box"), data["response_box"], size)]
            if not line_hash or len(old_line) != 1 or len(actual_line) != 1 or old_line[0]["picture"] != actual_line[0]["picture"]:
                return bad
            for response in (data["response_box"], data["box"]):
                x, y = center(response)
                points.append({**marker, "box": (x, y, 0, 0), "response_region": len(areas),
                               "graphical_response_box": data["box"] if response == data["box"] else None})
        else:
            points.append(marker)
        if not isinstance(cards, list) or not 1 <= len(cards) <= 40:
            return bad
        manifest = {}
        for card in cards:
            if not isinstance(card, dict) or not isinstance(card.get("sha256"), str) or len(card["sha256"]) != 64 or not rectangle(card.get("box"), size):
                return bad
            originals = [u for u in before if u["picture"].get("sha256") == card["sha256"]]
            if card["sha256"] in manifest or len(originals) != 1 or not same_box(originals[0].get("box"), card["box"], size):
                return bad
            manifest[card["sha256"]] = originals[0]
        if allowed is None:
            allowed = manifest
        elif allowed != manifest:
            return bad
        areas.append((marker, data["box"], data["source_box"], target_hash))
    if len({tuple(box) for _, box, _, _ in areas}) != len(areas):
        return bad
    if any(inside(center(card["box"]), source) for card in allowed.values() for _, _, source, _ in areas):
        return bad
    # Actual writing may label either the authenticated silhouette or name line.
    result, uncoded, basis = point_answers(points, written, template, size)
    if basis == REVIEW:
        return bad
    off_target = []
    for digest, original in allowed.items():
        matches = [u for u in after if u["picture"].get("sha256") == digest]
        if len(matches) != 1:
            return bad
        unit = matches[0]
        if not rectangle(unit.get("box"), size):
            return bad
        if math.dist(center(unit["box"]), center(original["box"])) <= .03 * max(size):
            continue
        if unit["picture"] != original["picture"]:
            old_appearance = original["picture"].get("appearance", {})
            actual_appearance = unit["picture"].get("appearance", {})
            if aggregate and ({k: v for k, v in old_appearance.items() if k != "crop"}
                              == {k: v for k, v in actual_appearance.items() if k != "crop"}):
                off_target.append((unit, "Changed-crop original picture placement"))
                continue
            return bad
        landed = [m for m, box, _, _ in areas if inside(center(unit["box"]), box)]
        if len(landed) != 1:
            if aggregate and not landed:
                off_target.append((unit, "Off-target original picture placement"))
                continue
            return bad
        m = landed[0]
        result[(m["category"], m["id"])].append((unit, "picture placement"))
    for unit in after:
        if not unit.get("box") or not any(inside(center(unit["box"]), box) for _, box, _, _ in areas):
            continue
        digest = unit["picture"].get("sha256")
        if digest in allowed:
            continue
        unchanged = [u for u in before if u["picture"] == unit["picture"] and same_box(u.get("box"), unit["box"], size)]
        # The registered silhouettes can be translated together in the actual
        # received copy; their exact media+target geometry was validated above.
        target = any(digest == target_hash and same_box(unit["box"], box, size) for _, box, _, target_hash in areas)
        if not unchanged and not target:
            return bad
    if off_target and not any(result.values()):
        return bad
    return result, uncoded + off_target, "original picture placement"
