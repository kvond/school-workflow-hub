"""Conservative source-field attribution for existing decks without descriptors.

This is a gate on legacy completion evidence, not a grading rule or a proof of
historical distribution. Source roles come from the complete original slide;
submitted text and exported object names cannot create a response role.
"""
import math
import re

from . import response_binding as rb, legacy_static_context, legacy_label_binding

REVIEW = "Legacy response attribution needs review"


def _placeholder(text):
    text = " ".join(text.split()).strip()
    return bool(re.fullmatch(
        r"(?i)(?:(?:type|write|enter)(?: your)?(?: answer| response| explanation| definition| text)? here[.!:]?"
        r"|[^\[\]]*\[\s*(?:type|write|enter)\b[^\[\]]{0,100}\])", text))


def _contains(outer, inner, size):
    margin = .001 * max(size)
    return (outer and inner and outer[2] > 0 and outer[3] > 0
            and inner[2] > 0 and inner[3] > 0
            and inner[0] >= outer[0] - margin and inner[1] >= outer[1] - margin
            and inner[0] + inner[2] <= outer[0] + outer[2] + margin
            and inner[1] + inner[3] <= outer[1] + outer[3] + margin)


def _finite_box(box):
    # Printed context may extend off-canvas and still contain a visible child.
    return (isinstance(box, (list, tuple)) and len(box) == 4
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in box)
            and box[2] > 0 and box[3] > 0)


def anchor_writing(original, student, size, syntax, plain):
    """Standalone code shapes are source anchors, never writable evidence.

    A parsed table cell has a separately established cell role; a source/copy
    name, student flag or a code appearing in text cannot create that role.
    """
    for anchor in (original or {}).get("units", []):
        code = syntax.fullmatch(anchor["text"].strip())
        if code is None or "binding" in anchor or legacy_static_context.is_table_cell(anchor):
            continue
        for unit in student["units"]:
            if not plain(unit["text"]):
                continue
            if rb.same_box(anchor.get("box"), unit.get("box"), size) or any(
                    m.groups() == code.groups() for m in syntax.finditer(unit["text"])):
                return True
    return False


def point_context_writing(original, written, size, syntax, plain):
    """A point inside a source backdrop cannot authenticate backdrop writing.

    Inspect only proposed credited rectangles against printed original context;
    merely being inside an unchanged blank backdrop is not a refusal reason.
    This gate is for the uncontracted point fallback, not graphical validators.
    """
    if not original:
        return False
    source = [u for u in original["units"] if not u.get("picture")]
    context = [u for u in source if plain(u["text"]) and not _placeholder(plain(u["text"]))
               and not (legacy_static_context.is_table_cell(u) and syntax.search(u["text"]))]
    for unit, words in written:
        if unit.get("picture") or words.startswith("moved: "):
            continue
        box = unit.get("box")
        exact = [u for u in source if rb.same_box(box, u.get("box"), size)]
        if len(exact) == 1 and syntax.search(exact[0]["text"]) and (
                _placeholder(plain(exact[0]["text"])) or legacy_static_context.is_table_cell(exact[0])):
            continue  # preserve direct in-field/source-cell response evidence
        if not rb.rectangle(box, size) or any(
                not _finite_box(u.get("box")) or rb._intersects(box, u["box"]) for u in context):
            return True
    return False


def _mixed_role_fields(source, owners, size):
    """Find ambiguous fallback cohorts, without assigning roles by box size.

    A disjoint row of blank regions plus a spanning, taller region below can
    contain different activity/writing roles. One generic identity cannot prove
    that every region is its answer. The complete original cohort is held;
    independently authenticated owners are preserved by the caller.
    """
    cohorts = {}
    for i, identities in owners.items():
        if len(identities) == 1 and rb.rectangle(source[i].get("box"), size):
            identity = next(iter(identities))
            if identity[0] in ("NOTES", "DRAFT"):
                cohorts.setdefault(identity, []).append(i)
    ambiguous = set()
    margin = .001 * max(size)
    for indices in cohorts.values():
        blanks = [i for i in indices if not source[i]["text"].strip()]
        for offset, i in enumerate(blanks):
            a = source[i]["box"]
            for j in blanks[offset + 1:]:
                b = source[j]["box"]
                if (abs(a[1] - b[1]) > margin or abs(a[3] - b[3]) > margin or rb._intersects(a, b)):
                    continue
                left, right = min(a[0], b[0]), max(a[0] + a[2], b[0] + b[2])
                bottom = max(a[1] + a[3], b[1] + b[3])
                for k in indices:
                    if k in (i, j): continue
                    box = source[k]["box"]
                    if (box[1] >= bottom and box[0] <= left + margin and box[0] + box[2] >= right - margin
                            and box[2] > max(a[2], b[2]) + margin and box[3] > max(a[3], b[3]) + margin):
                        ambiguous.update(indices)
    return ambiguous


def _disjoint_role_fields(source, owners, size):
    """Refuse unlike unbound body/auxiliary regions without selecting a role.

    A wide bottom strip or a small isolated blank and a separate large body
    can serve different roles or multipart work. Geometry cannot prove that
    either is unscored. Independent bindings are preserved by the caller.
    """
    cohorts = {}
    for i, own in owners.items():
        if len(own) == 1 and next(iter(own))[0] in ("NOTES", "DRAFT") and rb.rectangle(source[i].get("box"), size):
            cohorts.setdefault(next(iter(own)), []).append(i)
    ambiguous = set()
    for indices in cohorts.values():
        for i in indices:
            strip = source[i]["box"]
            footer = strip[2] >= .8 * size[0] and strip[3] <= .05 * size[1] and strip[1] >= .9 * size[1]
            small = strip[2] <= .08 * size[0] and strip[3] <= .08 * size[1]
            if source[i]["text"].strip() or not (footer or small):
                continue
            for j in indices:
                body = source[j]["box"]
                overlap = min(strip[0] + strip[2], body[0] + body[2]) - max(strip[0], body[0])
                gap = strip[1] - body[1] - body[3]
                separate_body = (j != i and body[2] >= .5 * size[0] and body[3] >= .2 * size[1]
                                 and not rb._intersects(strip, body))
                if separate_body and (small or (overlap >= .8 * min(strip[2], body[2]) and 0 <= gap <= .12 * size[1])):
                    ambiguous.update(indices)
    return ambiguous


def _singleton_role_fields(source, markers, owners, context, size):
    """One generic identity cannot prove roles across unlike original fields.

    Keep uniform multipart compatibility; dimensions only identify uncertainty,
    never which field is an answer. Proven ineligible printed backdrops remain
    in the raw census but cannot make a separate response ambiguous here.
    """
    if len(markers) != 1 or markers[0]["category"] not in ("NOTES", "DRAFT"):
        return set()
    marker = markers[0]
    identity = (marker["category"], marker["code"], marker["id"])
    cohort = [i for i, own in owners.items() if own == {identity} and rb.rectangle(source[i].get("box"), size)
              and not any(not _finite_box(source[j].get("box"))
                          or rb._intersects(source[i]["box"], source[j]["box"]) for j in context if j != i)]
    margin = .001 * max(size)
    if any(not rb._intersects(source[i]["box"], source[j]["box"])
           and any(abs(source[i]["box"][axis] - source[j]["box"][axis]) > margin for axis in (2, 3))
           for offset, i in enumerate(cohort) for j in cohort[offset + 1:]):
        return set(cohort)
    return set()


def answers(markers, written, original, student, size, candidate_assign, syntax, plain, squash, added, distance,
            *, validated_numeric_source=None, validated_numeric_identity=None):
    """Retain legacy routes only when the original establishes field ownership."""
    result = {(m["category"], m["id"]): [] for m in markers}
    def bad():
        return {key: [] for key in result}, list(written), REVIEW
    if not original or not student:
        return bad()
    static_members = legacy_static_context.plan(original, student, size, _placeholder)
    if static_members is None:
        return bad()
    static_source = {id(original["units"][i]) for i in static_members}
    static_student = {id(student["units"][i]) for i in static_members}
    if validated_numeric_source is not None and not any(validated_numeric_source is u for u in original["units"]):
        return bad()
    source = [u for u in original["units"] if not u.get("picture") and u is not validated_numeric_source]
    source_markers = [
        {"category": m.group(1), "code": m.group(2), "id": m.group(3).strip(),
         "slide": original["number"], "key": u["key"], "box": u.get("box"),
         "standalone": u["text"].strip() == m.group(0)}
        for u in source for m in syntax.finditer(u["text"])
        if (m.group(1), m.group(2), m.group(3).strip()) != validated_numeric_identity]
    identity = lambda m: (m["category"], m["code"], m["id"])
    current = {identity(m) for m in markers}
    originals = {identity(m) for m in source_markers}
    if not current <= originals or len(current) != len(markers):
        return bad()
    codes = {}
    for m in source_markers:
        codes.setdefault(identity(m), []).append(m)
    # A duplicated source identity cannot authenticate one response location.
    if any(len(codes[i]) != 1 for i in current):
        return bad()
    # Missing submitted codes never remove their original fields from the map
    # or lend those fields to one of the surviving identities.
    source_markers.sort(key=lambda m: (m["box"][1], m["box"][0]) if m.get("box") else (0, 0))
    wanted = {(m["category"], m["id"]): identity(m) for m in source_markers}
    fixed, fields, terms, context = {}, [], [], []
    for index, unit in enumerate(source):
        if id(unit) in static_source:
            continue  # both verified objects remain in source, never in field ownership
        text = plain(unit["text"])
        own = {m.groups() for m in syntax.finditer(unit["text"])}
        own = {(c, cycle, ident.strip()) for c, cycle, ident in own}
        eligible = not text or _placeholder(text)
        if unit.get("title"):
            eligible = False
        labels = {identity(m) for m in source_markers if m["category"] == "BANK"
                  and squash(text) and squash(text) == squash(m["id"])}
        if labels and not unit.get("title"):
            terms.append(index)
            fixed[index] = labels if not own or own == labels else set()
        elif eligible:
            if own and not text and not legacy_static_context.is_table_cell(unit):
                continue  # a standalone marker supplies location, not an answer field
            # A code-only label is an anchor, not an extra field in an ordered
            # census. It can still authenticate writing in its very own box.
            if not own:
                fields.append(index)
            anchors = own or {identity(m) for m in source_markers if m.get("box")
                              and unit.get("box") and rb.inside(rb.center(m["box"]), unit["box"])}
            if anchors:
                fixed[index] = anchors
        elif text:
            context.append(index)

    # Establish fallback ownership once using EVERY original field. A partial
    # submission cannot change a later field's owner by editing a smaller set.
    probe = [(source[i], "original field attribution probe") for i in fields + terms]
    proposed, _, basis = candidate_assign(source_markers, probe, original, size, student_slide=original)
    owners = {i: set() for i in fields + terms}
    for key, items in proposed.items():
        for unit, _ in items:
            for i in owners:
                if unit is source[i] and key in wanted:
                    owners[i].add(wanted[key])
    owners.update(fixed)
    if basis == "code on the box":
        for i in fields:
            if i in fixed or not source[i].get("box"):
                continue
            distances = [(distance(rb.center(m["box"]), source[i]["box"]),
                          identity(m)) for m in source_markers if m.get("box")]
            nearest = min((d for d, _ in distances), default=None)
            if len({owner for d, owner in distances if d == nearest}) > 1:
                owners[i] = set()  # source order cannot break a geometric tie
    if basis in (REVIEW, rb.REVIEW, "BANK term labels need review", "Response marker needs review"):
        # Exact in-field identities remain independently meaningful; uncertain
        # fallback assignments do not inherit that evidence.
        owners = {i: fixed.get(i, set()) for i in owners}
        if basis == "BANK term labels need review":
            # An untouched ambiguous BANK blank must not steal or suppress a
            # distant, independently established non-BANK response field.
            other = [m for m in source_markers if m["category"] != "BANK"]
            far = [i for i in fields if all(not source[t].get("box") or not source[i].get("box")
                    or distance(rb.center(source[i]["box"]), source[t]["box"]) > .08 * max(size)
                    for t in terms)]
            if other and far:
                fallback, _, how = candidate_assign(other, [(source[i], "original field attribution probe") for i in far],
                                                     original, size, student_slide=original)
                if how not in (rb.REVIEW, "BANK term labels need review"):
                    for key, items in fallback.items():
                        for unit, _ in items:
                            for i in far:
                                if unit is source[i] and key in wanted:
                                    owners[i].add(wanted[key])
    if basis == "order on the slide":
        fallback = [i for i in fields if not any(o[0] == "BANK" for o in owners[i])]
        if len(fallback) != sum(m["category"] != "BANK" for m in source_markers):
            owners = {i: fixed.get(i, set()) if not any(o[0] == "BANK" for o in own) else own
                      for i, own in owners.items()}

    # An inline whole-term definition is an established legacy response. A
    # separate printed label next to an original answer field is only context.
    contenders = {}
    for j in fields:
        distances = [(distance(rb.center(source[j]["box"]), source[t]["box"]), t)
                     for t in terms if source[j].get("box") and source[t].get("box")]
        nearest = min((d for d, _ in distances), default=None)
        if nearest is not None and (all(m["category"] == "BANK" for m in source_markers) or nearest <= .08 * max(size)):
            contenders[j] = {t for d, t in distances if d == nearest}
    for i in terms:
        separate = any(owners.get(j, set()) & owners[i] or (
            len(owners.get(j, set())) != 1 and i in contenders.get(j, set())) for j in fields)
        if separate:
            owners.pop(i, None)
            context.append(i)
    for i in list(owners):
        if re.fullmatch(r"(?i)(?:your )?(?:first |revised )?answer[.:]?", plain(source[i]["text"])) and any(
                j != i and not plain(source[j]["text"]) and owners.get(j, set()) & owners[i] for j in fields):
            owners.pop(i, None)
            context.append(i)

    managed, labelled, static_labels, protected, conflicts = legacy_label_binding.plan(
        source, source_markers, fields, terms, context, fixed, size, syntax, plain, squash, _contains, _finite_box, distance)
    for i in static_labels:
        owners.pop(i, None)
        if i not in context:
            context.append(i)
    for i in fields:
        if i not in protected:
            owners[i] = (owners.get(i, set()) - managed) | labelled.get(i, set())
    for i in conflicts:
        owners[i] = set()

    for i in _mixed_role_fields(source, owners, size):
        if i not in fixed:
            owners[i] = set()  # no largest-field guess or new activity scoring rule

    for i in _disjoint_role_fields(source, owners, size):
        if i not in protected and i not in labelled:
            owners[i] = set()

    for i in _singleton_role_fields(source, source_markers, owners, context, size):
        if i not in protected and i not in labelled:
            owners[i] = set()

    # Empty/placeholder shapes overlapping printed context, including overhangs,
    # cannot acquire a role from fallback proximity or a context marker center.
    # Retain it in the complete census above so refusing it cannot shift any
    # other field's identity. Direct in-field and validated routes stay separate.
    for i in fields:
        box = source[i].get("box")
        if not rb.rectangle(box, size) or any(
                not _finite_box(source[j].get("box")) or rb._intersects(box, source[j]["box"])
                for j in context):
            owners[i] = set()

    # Keep original context at its original location. Moving or rewriting a
    # heading into an answer rectangle cannot manufacture a response role.
    for i in context:
        unit = source[i]
        matching = [u for u in student["units"] if not u.get("picture")
                    and rb.same_box(u.get("box"), unit.get("box"), size)]
        if (len(matching) != 1 or " ".join(plain(matching[0]["text"]).split()) !=
                " ".join(plain(unit["text"]).split())):
            return bad()

    loose = []
    for item in written:
        unit, words = item
        if id(unit) in static_student:
            continue  # unchanged context can appear as a renamed-alias diff, never an answer
        if words.startswith("moved: "):
            loose.append(item)
            continue
        exact = [i for i, old in enumerate(source) if rb.same_box(unit.get("box"), old.get("box"), size)]
        if len(exact) > 1:
            return bad()
        matching = exact or [i for i in owners if _contains(source[i].get("box"), unit.get("box"), size)]
        if len(matching) != 1 or len(owners.get(matching[0], set())) != 1:
            return bad()
        owner = next(iter(owners[matching[0]]))
        if sum(rb.same_box(unit.get("box"), u.get("box"), size)
               for u in student["units"] if not u.get("picture")) != 1:
            return bad()
        # Original geometry and complete identity, never submitted marker text,
        # determine the recipient. Changed/added codes cannot transfer credit.
        submitted = {(m.group(1), m.group(2), m.group(3).strip()) for m in syntax.finditer(unit["text"])}
        if owner not in current or (submitted and submitted != {owner}):
            return bad()
        before = source[matching[0]]["text"]
        words = added(before, unit["text"])
        if words:
            verified = {**unit, "verified_legacy_answer": added(before, unit["text"], exact=True)}
            result[(owner[0], owner[2])].append((verified, words))
    return result, loose, "verified original legacy fields"
