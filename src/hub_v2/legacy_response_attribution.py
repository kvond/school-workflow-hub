"""Conservative source-field attribution for existing decks without descriptors.

This is a gate on legacy completion evidence, not a grading rule or a proof of
historical distribution. Source roles come from the complete original slide;
submitted text and exported object names cannot create a response role.
"""
import re

from . import response_binding as rb

REVIEW = "Legacy response attribution needs review"


def _placeholder(text):
    text = " ".join(text.split()).strip()
    return bool(re.fullmatch(
        r"(?i)(?:(?:type|write|enter)(?: your)?(?: answer| response| explanation| definition| text)? here[.!:]?"
        r"|(?:your )?(?:first |revised )?answer[.:]?"
        r"|[^\[\]]*\[\s*(?:type|write|enter)\b[^\[\]]{0,100}\])", text))


def _contains(outer, inner, size):
    margin = .001 * max(size)
    return (outer and inner and outer[2] > 0 and outer[3] > 0
            and inner[2] > 0 and inner[3] > 0
            and inner[0] >= outer[0] - margin and inner[1] >= outer[1] - margin
            and inner[0] + inner[2] <= outer[0] + outer[2] + margin
            and inner[1] + inner[3] <= outer[1] + outer[3] + margin)


def answers(markers, written, original, student, size, candidate_assign, syntax, plain, squash, added, distance,
            *, validated_numeric_source=None, validated_numeric_identity=None):
    """Retain legacy routes only when the original establishes field ownership."""
    result = {(m["category"], m["id"]): [] for m in markers}
    def bad():
        return {key: [] for key in result}, list(written), REVIEW
    if not original or not student:
        return bad()
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
