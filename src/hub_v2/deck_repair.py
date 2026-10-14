"""Guarded deck repairs for the weekly preflight, through the Slides API.

Two operations, each dry-run by default and written only to the exact
revision just read (Slides refuses the batch if the deck changed since):

- replace_text: replace exact text on named slides. The whole request is
  refused, with nothing written, when a match sits in a shape that also holds
  a response code ([[NOTES:, [[DRAFT:, [[OPTIONAL:, [[BANK:) or the code list:
  those are answer areas and the inventory. A replacement already applied is
  reported as such, so a repeated request writes nothing.
- sync_inventory: rebuild the [[MARKER-INVENTORY:...]] line from the codes the
  deck actually carries, in their slide order, in the format
  completion.readiness reads. Only the inventory's own shape is rewritten; no
  code is created or removed anywhere.

Each write is read back: the old text must be gone and the new present, and
for the inventory completion.readiness must find no missing or extra IDs.
"""
import re

from . import completion, google

ANSWER_MARKER = re.compile(r"\[\[(?:NOTES|DRAFT|OPTIONAL|BANK):")
INVENTORY_MARKER = "[[MARKER-INVENTORY:"
DECK_ID = re.compile(r"[A-Za-z0-9_-]{20,200}")
MAX_REPLACEMENTS, MAX_TEXT = 20, 500
FIXABLE = {"marker_inventory_count_mismatch", "marker_inventory_id_mismatch", "marker_inventory_counts_unparsed"}


class Refused(ValueError):
    """Nothing was written; the message says why."""


def _texts(elements, slide, page, out, depth=0):
    for element in elements or []:
        if not isinstance(element, dict) or depth > 20:
            continue
        if "elementGroup" in element:
            _texts((element["elementGroup"] or {}).get("children"), slide, page, out, depth + 1)
        if "shape" in element:
            runs = ((element["shape"] or {}).get("text") or {}).get("textElements") or []
            out.append({"slide": slide, "page": page, "object_id": element.get("objectId"), "kind": "shape",
                        "text": "".join(((r or {}).get("textRun") or {}).get("content", "") for r in runs)})
        if "table" in element:
            for row in (element["table"] or {}).get("tableRows") or []:
                for cell in (row or {}).get("tableCells") or []:
                    runs = ((cell or {}).get("text") or {}).get("textElements") or []
                    out.append({"slide": slide, "page": page, "object_id": element.get("objectId"), "kind": "table",
                                "text": "".join(((r or {}).get("textRun") or {}).get("content", "") for r in runs)})


def _fresh(token, deck_id, expected_revision_id=None):
    """(revisionId, slides [(number, page id)], text containers) read just now."""
    if not isinstance(deck_id, str) or not DECK_ID.fullmatch(deck_id):
        raise Refused("Choose one exact Google Slides deck.")
    meta = google.drive_file(token, deck_id, "id,name,mimeType,trashed,version,modifiedTime")
    if meta.get("trashed") is not False:
        raise Refused("This deck is in trash. Nothing was changed.")
    if meta.get("mimeType") != completion.SLIDES:
        raise Refused("Only Google Slides decks can be repaired here.")
    presentation = google.native_presentation(token, deck_id)
    revision = presentation.get("revisionId") if isinstance(presentation, dict) else None
    slides = presentation.get("slides") if isinstance(presentation, dict) else None
    if presentation.get("presentationId") != deck_id or not isinstance(revision, str) or not revision \
            or not isinstance(slides, list):
        raise Refused("The deck's current revision couldn't be read. Nothing was changed.")
    if expected_revision_id is not None and expected_revision_id != revision:
        raise Refused("The deck changed since it was reviewed (revision %s, expected %s). Nothing was changed."
                      % (revision, expected_revision_id))
    pages, containers = [], []
    for number, page in enumerate(slides, 1):
        pages.append((number, (page or {}).get("objectId")))
        _texts((page or {}).get("pageElements"), number, (page or {}).get("objectId"), containers)
    return revision, pages, containers


def _replacements(values, slide_count):
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_REPLACEMENTS:
        raise Refused("Give 1 to %d replacements." % MAX_REPLACEMENTS)
    out = []
    for value in values:
        if not isinstance(value, dict) or set(value) != {"find", "replace", "slide_numbers"}:
            raise Refused("Each replacement needs find, replace and slide_numbers.")
        find, replace, numbers = value["find"], value["replace"], value["slide_numbers"]
        if (not isinstance(find, str) or not 1 <= len(find) <= MAX_TEXT or not isinstance(replace, str)
                or len(replace) > MAX_TEXT or "[[" in find + replace or "]]" in find + replace):
            raise Refused("Replacement text must be plain text without response codes.")
        if find in replace:
            raise Refused("“%s” would still be on the slide after the replacement, so it can't be confirmed." % find)
        if (not isinstance(numbers, list) or not numbers or len(numbers) != len(set(numbers))
                or any(not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= slide_count for n in numbers)):
            raise Refused("Name the slides by number (1 to %d)." % slide_count)
        out.append({"find": find, "replace": replace, "slides": sorted(numbers)})
    return out


def _count(containers, slide, value):
    return sum(c["text"].count(value) for c in containers if c["slide"] == slide) if value else 0


def replace_text(token, write_token, deck_id, replacements, *, expected_revision_id=None, dry_run=True):
    revision, pages, containers = _fresh(token, deck_id, expected_revision_id)
    wanted = _replacements(replacements, len(pages))
    page_ids = dict(pages)
    plan, receipts = [], []
    for item in wanted:
        for container in containers:
            if container["slide"] in item["slides"] and item["find"] in container["text"] \
                    and (ANSWER_MARKER.search(container["text"]) or INVENTORY_MARKER in container["text"]):
                raise Refused("“%s” on slide %d is inside a shape that holds response codes (an answer area or the "
                              "code list). Nothing was changed." % (item["find"], container["slide"]))
        before = {n: _count(containers, n, item["find"]) for n in item["slides"]}
        present = any(_count(containers, n, item["replace"]) for n in item["slides"])
        if not any(before.values()):
            if item["replace"] and present:
                receipts += [{"slide": n, "find": item["find"], "replace": item["replace"], "before_count": 0,
                              "after_count": 0, "status": "already_applied"} for n in item["slides"]]
                continue
            raise Refused("“%s” isn't on slide(s) %s. Nothing was changed." % (
                item["find"], ", ".join(str(n) for n in item["slides"])))
        plan.append({**item, "before": before})
    if not plan:
        return {"op": "replace_text", "status": "nothing_to_do", "changed": False, "dry_run": dry_run,
                "revision_id": revision, "receipts": receipts}
    if dry_run:
        return {"op": "replace_text", "status": "planned", "changed": False, "dry_run": True, "revision_id": revision,
                "receipts": receipts + [{"slide": n, "find": i["find"], "replace": i["replace"],
                                         "before_count": i["before"][n], "after_count": None, "status": "planned"}
                                        for i in plan for n in i["slides"]]}
    requests = [{"replaceAllText": {"containsText": {"text": i["find"], "matchCase": True}, "replaceText": i["replace"],
                                    "pageObjectIds": [page_ids[n] for n in i["slides"]]}} for i in plan]
    google.slides_batch_update(write_token, deck_id, requests, revision)
    after_revision, _, after = _fresh(token, deck_id)
    verified = True
    for item in plan:
        for n in item["slides"]:
            gone, shown = _count(after, n, item["find"]), _count(after, n, item["replace"])
            ok = gone == 0 and (not item["replace"] or not item["before"][n] or shown > 0)
            verified = verified and ok
            receipts.append({"slide": n, "find": item["find"], "replace": item["replace"],
                             "before_count": item["before"][n], "after_count": gone,
                             "status": "applied" if ok else "unconfirmed"})
    return {"op": "replace_text", "status": "applied_and_verified" if verified else "unconfirmed", "changed": True,
            "dry_run": False, "previous_revision_id": revision, "revision_id": after_revision, "receipts": receipts}


def _model(containers):
    """The deck as completion.readiness reads it."""
    slides = {}
    for index, container in enumerate(containers):
        slides.setdefault(container["slide"], []).append(
            {"key": "%s|%d" % (container["object_id"], index), "text": container["text"], "box": None})
    return {"slides": [{"number": n, "units": units} for n, units in sorted(slides.items())]}


def inventory_line(cycle, codes, bank_ids=False):
    """'[[MARKER-INVENTORY:C06]] NOTES=2 DRAFT=1 OPTIONAL=0 BANK_TERMS=0 NOTES_IDS=a,b DRAFT_IDS=a
    OPTIONAL_IDS=-' (one line), with '-' for an empty list, as completion.readiness reads it."""
    lists = {category: [ident for kind, ident in codes if kind == category] for category in completion.CATEGORIES}
    parts = ["[[MARKER-INVENTORY:%s]]" % cycle] + ["%s=%d" % ("BANK_TERMS" if c == "BANK" else c, len(lists[c]))
                                                  for c in ("NOTES", "DRAFT", "OPTIONAL", "BANK")]
    for category in ("NOTES", "DRAFT", "OPTIONAL") + (("BANK",) if bank_ids else ()):
        parts.append("%s_IDS=%s" % (category, ",".join(lists[category]) or "-"))
    return " ".join(parts)


def sync_inventory(token, write_token, deck_id, *, expected_revision_id=None, dry_run=True):
    revision, _, containers = _fresh(token, deck_id, expected_revision_id)
    holders = [c for c in containers if completion.INVENTORY.search(c["text"])]
    if len(holders) != 1:
        raise Refused("The deck needs exactly one code list ([[MARKER-INVENTORY:...]]); found %d. Nothing was changed."
                      % len(holders))
    holder = holders[0]
    if holder["kind"] != "shape" or ANSWER_MARKER.search(holder["text"]):
        raise Refused("The code list shares its box with a table or response codes. Edit it by hand; nothing was changed.")
    if any(c is not holder and c["slide"] == holder["slide"]
           and (completion.COUNT.search(c["text"]) or re.search(r"\b(?:NOTES|DRAFT|OPTIONAL|BANK)_IDS=", c["text"]))
           for c in containers):
        raise Refused("The code list is split across several boxes. Edit it by hand; nothing was changed.")
    match = completion.INVENTORY.search(holder["text"])
    cycle = match.group(1)
    codes, seen = [], set()
    for container in containers:
        for marker in completion.MARKER.finditer(container["text"]):
            category, code, ident = marker.group(1), marker.group(2), marker.group(3).strip()
            if code != cycle:
                raise Refused("A response code on slide %d names cycle %s, not %s. Nothing was changed."
                              % (container["slide"], code, cycle))
            if (category, ident) in seen:
                raise Refused("%s %s appears more than once; decide which question it belongs to. Nothing was changed."
                              % (category, ident))
            seen.add((category, ident))
            codes.append((category, ident))
    before = completion.readiness(_model(containers))
    segment = holder["text"][match.start():]
    line = inventory_line(cycle, codes, bank_ids="BANK_IDS=" in segment)
    reply = {"op": "sync_inventory", "slide": holder["slide"], "object_id": holder["object_id"],
             "before": " ".join(segment.split()), "after": line, "revision_id": revision,
             "missing_before": before["missing_ids"], "extra_before": before["extra_ids"], "dry_run": dry_run}
    consistent = not (set(before["issues"]) & FIXABLE) and not before["missing_ids"] and not before["extra_ids"]
    if " ".join(segment.split()) == line or consistent:
        return {**reply, "status": "nothing_to_do", "changed": False}
    if dry_run:
        return {**reply, "status": "planned", "changed": False}
    google.slides_batch_update(write_token, deck_id, [
        {"deleteText": {"objectId": holder["object_id"], "textRange": {"type": "ALL"}}},
        {"insertText": {"objectId": holder["object_id"], "insertionIndex": 0,
                        "text": holder["text"][:match.start()] + line}}], revision)
    after_revision, _, after = _fresh(token, deck_id)
    check = completion.readiness(_model(after))
    others_before = [(c["object_id"], c["text"]) for c in containers if c is not holder]
    others_after = [(c["object_id"], c["text"]) for c in after
                    if not (c["object_id"] == holder["object_id"] and c["slide"] == holder["slide"])]
    verified = (not check["missing_ids"] and not check["extra_ids"] and not set(check["issues"]) & FIXABLE
                and others_before == others_after)
    return {**reply, "status": "applied_and_verified" if verified else "unconfirmed", "changed": True,
            "previous_revision_id": revision, "revision_id": after_revision,
            "missing_after": check["missing_ids"], "extra_after": check["extra_ids"],
            "remaining_issues": [i for i in check["issues"] if i not in FIXABLE]}
