"""Bounded source-declared label associations for existing response fields.

No student text, source/copy alias, question index or distance rank supplies an
identity. Unsupported or competing associations cannot inherit a fallback.
"""
import re

from . import response_binding as rb

ROLE = re.compile(r"(?i)(?:question ([1-9][0-9]{0,2})\s*[-:–—]\s*)?"
                  r"(?:your )?(first|revised) answer"
                  r"(?:\s*[-:–—]\s*question ([1-9][0-9]{0,2}))?[.:]?")


def _adjacent(label, field, size, *, bank=False):
    """A label above its field or left of its BANK definition, never ranked."""
    if rb._intersects(label, field):
        return False, False
    margin = .001 * max(size)
    horizontal = min(label[0] + label[2], field[0] + field[2]) - max(label[0], field[0])
    above = (horizontal >= .8 * min(label[2], field[2])
             and -margin <= field[1] - label[1] - label[3] <= (.08 if bank else .05) * size[1])
    vertical = min(label[1] + label[3], field[1] + field[3]) - max(label[1], field[1])
    gap = field[0] - label[0] - label[2]
    side = vertical >= .8 * min(label[3], field[3]) and 0 <= gap <= .12 * size[0]
    return above, side


def plan(source, markers, fields, terms, context, fixed, size, syntax, plain, squash, contains, finite, distance):
    """Return managed identities, unique source owners, and new static labels."""
    identity = lambda m: (m["category"], m["code"], m["id"])
    bank = {identity(m) for m in markers if m["category"] == "BANK"}
    paired = [m for m in markers if m["category"] in ("NOTES", "DRAFT")]
    pair = {m["category"] for m in paired} == {"NOTES", "DRAFT"}
    mixed = pair and len({(m["code"], m["id"]) for m in paired}) > 1
    managed = set(bank) | ({identity(m) for m in paired} if pair else set())
    # Every original context/term remains in the census, including labels whose
    # old nearest-field fallback failed to recognize them as static context.
    printed = set(context) | set(terms)
    direct = set(fixed) - set(fields) - set(terms)
    original_fields = list(dict.fromkeys(fields + sorted(direct)))
    eligible = [i for i in original_fields if rb.rectangle(source[i].get("box"), size) and not any(
        not finite(source[j].get("box")) or rb._intersects(source[i]["box"], source[j]["box"])
        or contains(source[i]["box"], source[j]["box"], size)
        or contains(source[j]["box"], source[i]["box"], size) for j in printed)]
    # A genuine standalone source point uniquely inside a field retains its
    # independent role. A large printed prompt's center does not supply this.
    protected = set(direct)
    point_owners = {i: set(fixed[i]) for i in direct}
    for m in markers:
        if not m.get("standalone") or not rb.rectangle(m.get("box"), size):
            continue
        matches = [i for i in eligible if contains(source[i]["box"], m["box"], size)]
        if len(matches) == 1:
            protected.add(matches[0])
            point_owners.setdefault(matches[0], set()).add(identity(m))
    # Protected regions still participate in competing source claims. Excluding
    # them from the graph would hide a contradictory above-label identity.
    candidates = list(dict.fromkeys(eligible + [i for i in sorted(direct) if rb.rectangle(source[i].get("box"), size)]))
    labels = {}
    role_invalid = False
    for index, unit in enumerate(source):
        own = list(syntax.finditer(unit["text"]))
        text = " ".join(plain(unit["text"]).split())
        role = ROLE.fullmatch(text)
        declared_bank = index in terms
        if declared_bank:
            matches = {identity(m) for m in markers if m["category"] == "BANK" and squash(text) == squash(m["id"])}
            matching_labels = [i for i in terms if squash(plain(source[i]["text"])) == squash(text)]
            if len(matches) != 1 or len(matching_labels) != 1:
                continue
            ident = next(iter(matches))
            if own and (len(own) != 1 or (*own[0].groups()[:2], own[0].group(3).strip()) != ident):
                continue
            cat, cycle, qid = ident
        elif len(own) == 1:
            cat, cycle, qid = own[0].groups()
            ident = (cat, cycle, qid.strip())
        elif not own and role and pair and not mixed:
            # Established detached-code single-question labels declare the
            # category; exactly one original full identity can fill that role.
            cat = "NOTES" if role[2].lower() == "first" else "DRAFT"
            matches = {identity(m) for m in paired if m["category"] == cat}
            if len(matches) != 1:
                continue
            ident = next(iter(matches))
        else:
            continue
        declared_role = cat in ("NOTES", "DRAFT") and role is not None
        if not (declared_bank or declared_role):
            continue
        managed.add(ident)
        valid_role = (not declared_role or (
            cat == ("NOTES" if role[2].lower() == "first" else "DRAFT")
            and not (role[1] and role[3])))
        role_invalid |= not valid_role
        box = unit.get("box")
        valid = valid_role and not unit.get("title") and rb.rectangle(box, size) and box[3] <= .1 * size[1]
        edges = []
        if valid:
            for i in candidates:
                above, side = _adjacent(box, source[i]["box"], size, bank=declared_bank)
                if above or (declared_bank and side):
                    edges.append(i)
        labels[index] = {"identity": ident, "edges": edges, "bank": declared_bank}

    conflicts = {i for i in protected if any(i in label["edges"] and label["identity"] not in point_owners.get(i, set())
                                            for label in labels.values())}

    # Preserve established uniform single-question multipart groups. This
    # exception cannot rescue mixed identities or contradictory role labels.
    role_labels = [i for i, label in labels.items() if not label["bank"]]
    margin = .001 * max(size)
    uniform = (len(candidates) > len(role_labels) == 2 and not mixed and not role_invalid
               and {labels[i]["identity"][0] for i in role_labels} == {"NOTES", "DRAFT"}
               and len({(m["code"], m["id"]) for m in paired}) == 1
               and all(re.match(r"(?i)(?:your )?(?:first|revised) answer", plain(source[i]["text"])) for i in role_labels)
               and all(abs(source[i]["box"][axis] - source[candidates[0]]["box"][axis]) <= margin
                       for i in candidates for axis in (2, 3)))
    if uniform:
        for i in role_labels:
            managed.discard(labels[i]["identity"])
            labels.pop(i)

    degree = {i: sum(i in label["edges"] for label in labels.values()) for i in candidates}
    owners = {}
    # Even unsupported separate-field mappings keep printed term text static;
    # an adjacent original blank prevents an inline-label response fallback.
    static = {i for i in terms if rb.rectangle(source[i].get("box"), size) and any(
        any(_adjacent(source[i]["box"], source[j]["box"], size, bank=True)) for j in candidates)}
    bank_labels = [source[i]["box"] for i in terms if rb.rectangle(source[i].get("box"), size)]
    for index, label in labels.items():
        edges = label["edges"]
        if label["bank"] and edges:
            static.add(index)  # a declared separate field cannot become an inline label response
        if len(edges) != 1 or degree[edges[0]] != 1:
            continue
        i = edges[0]
        if i in protected or i not in eligible:
            continue
        if label["bank"]:
            distances = [distance(rb.center(source[i]["box"]), box) for box in bank_labels]
            if distances and sum(d == min(distances) for d in distances) > 1:
                continue  # preserve the existing geometric-tie refusal
        owners[i] = {label["identity"]}

    # Preserve a bounded established single pair: one canonical labelled
    # response plus its complementary standalone source point immediately
    # above the only other original blank. Count alone never supplies identity.
    if (pair and not mixed and not role_invalid and len(paired) == 2
            and len(original_fields) == len(fields) == len(candidates) == 2
            and len(role_labels) == len(owners) == 1 and not protected
            and not rb._intersects(source[candidates[0]]["box"], source[candidates[1]]["box"])):
        remaining = set(candidates) - owners.keys()
        bound = set().union(*owners.values())
        other = [m for m in paired if identity(m) not in bound]
        if len(remaining) == len(other) == 1:
            i, marker = next(iter(remaining)), other[0]
            box = marker.get("box")
            if (marker.get("standalone") and rb.rectangle(box, size)
                    and box[2] <= .03 * size[0] and box[3] <= .025 * size[1]
                    and 0 <= source[i]["box"][1] - box[1] - box[3] <= .025 * size[1]):
                claims = [j for j in candidates if rb._intersects(box, source[j]["box"]) or (
                    0 <= source[j]["box"][1] - box[1] - box[3] <= .025 * size[1]
                    and _adjacent(box, source[j]["box"], size)[0])]
                if claims == [i]:
                    owners[i] = {identity(marker)}
    return managed, owners, static, protected, conflicts
