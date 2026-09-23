"""Read-only shared-Form intake. No typed answer establishes identity.

All uploaded files are root-verified before content is read. A lesson requires
one unique match to the current Agenda student decks; uncertain versions stay
in review. Nothing here assesses, moves a file, changes a roster or posts grades.
"""
import json
import re
from hashlib import sha256
from urllib.parse import urlsplit

from .dated_agenda import (COURSES, DECK_URL, grid, value, link, turnin_form,
                           explicit_assignment_forms, assignment_source_links)
from .agenda_rows import cell_links
from .hosted_agenda import authenticated_reader, service_token, ConnectionError
from .forms_submissions import FormsReader, FORMS_SCOPE, response_file_refs, FORM_ID
from .submission_inventory import DriveReader, DRIVE_SCOPE, FOLDER, locate_files_under_root
from .roster_matching import read_roster, match_respondent, _norm, _aliases
from .assessment_save import _drive, _pptx_text, PPTX
from ..core.runs import content_hash

NATIVE = "application/vnd.google-apps.presentation"
MARKER = re.compile(r"\[\[(NOTES|DRAFT|OPTIONAL|BANK):([^:\]\s]+):([^\]\r\n]+)\]\]")


def private_mapping(config, name):
    raw = config.get(name, {})
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        raise ConnectionError("intake_configuration_invalid") from None
    if not isinstance(value, dict):
        raise ConnectionError("intake_configuration_invalid")
    return value


def _deck_bindings(config, assignments):
    """Explicitly reviewed deck-to-registry bindings, never resource titles.

    Additional material in Agenda O may be a resource or a second assignment.
    Only a private binding to an existing assignment verifies the latter.
    """
    bindings = private_mapping(config, "HUB_INTAKE_DECK_ASSIGNMENTS")
    for source_id, key in bindings.items():
        if (not isinstance(source_id, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", source_id)
                or not isinstance(key, str) or key not in assignments):
            raise ConnectionError("intake_registry_mapping_required")
    return bindings


def _catalog_assignment(course, week, topic, source_id, assignments, bindings):
    explicit = bindings.get(source_id)
    if explicit:
        known = assignments[explicit]
        if known["class_key"] != course:
            raise ConnectionError("intake_registry_conflict")
        return known
    proposed = f"{course}:W{int(week[1]):02d}{week[2]}"
    # Existing week/cycle keys are a reason to request a binding, never evidence
    # that two assignments in one week are interchangeable. Preserve F03-A/F03B
    # (and W/C conventions) rather than silently creating a third W03 registry.
    same_week = [a for key, a in assignments.items()
                 if a["class_key"] == course and
                 re.match(re.escape(course) + r":[WFCBA]0*" + str(int(week[1]))
                          + r"(?:[^0-9]|$)", key)]
    known = [a for key, a in assignments.items()
             if a["class_key"] == course and
             (key == proposed or _norm(a.get("assignment_title")) == _norm(topic))]
    if len(known) > 1:
        raise ConnectionError("intake_registry_ambiguous")
    if len(same_week) > 1 or (same_week and not known):
        raise ConnectionError("intake_registry_mapping_required")
    return known[0] if known else {"assignment_key": proposed, "class_key": course,
                                  "assignment_title": topic}


def _assignment_form_bindings(config, assignments):
    """Private, teacher-reviewed assignment bindings; errors stay per entry."""
    try:
        configured = private_mapping(config, "HUB_ASSIGNMENT_FORM_IDS")
    except ConnectionError:
        return {}, {}, "assignment_form_mapping_invalid"
    if any(not isinstance(key, str) or key not in assignments for key in configured):
        return {}, {}, "assignment_form_mapping_invalid"
    bindings, errors, by_form = {}, {}, {}
    for key, form_id in configured.items():
        if not isinstance(form_id, str) or not FORM_ID.fullmatch(form_id):
            errors[key] = "assignment_form_mapping_invalid"
            continue
        bindings[key] = form_id
        by_form.setdefault(form_id, []).append(key)
    for keys in by_form.values():
        if len(keys) > 1:
            errors.update((key, "assignment_form_reused") for key in keys)
    return bindings, errors, ""


def _form_destination(url):
    """Compare the exact Form resource, preserving published vs editable IDs."""
    valid, _, _ = turnin_form(url)
    return urlsplit(valid).path.rsplit("/", 1)[0] if valid else ""


def _turnin_cell(cells, row, column):
    cell = cells.get((row, column), {})
    try:
        links = cell_links(cell)
    except (ValueError, TypeError, AttributeError):
        return "", "assignment_form_turn_in_link_invalid"
    if not links:
        # T may contain a display label; U is a raw URL column.
        return "", "assignment_form_invalid" if column == 21 and value(cells, row, column) else ""
    forms = [turnin_form(url)[0] for url in links]
    if not all(forms):
        return "", "assignment_form_turn_in_link_invalid"
    if len({_form_destination(url) for url in forms}) != 1:
        return "", "assignment_form_turn_in_links_disagree"
    return forms[0], ""


def _catalog_form(entry, row_entries, cells, row, title, turnins, map_valid,
                  bindings, binding_errors, configuration_error, assignments, deck_bindings):
    """Read the weekly tab's visible T link and cross-check its raw U link.

    The observed link is retained even when a private mapping resolves an
    ambiguous multi-assignment row to a different Form. It is not a fallback
    global Form and its published /d/e/ identifier is never used as an API ID.
    """
    key = entry["assignment_key"]
    explicit_row = explicit_assignment_forms(cells, row, title, row_entries, deck_bindings, bindings)
    if explicit_row is not None:
        reason = configuration_error or binding_errors.get(key) or explicit_row["error"]
        selected = next((form for form in explicit_row["forms"] if form["assignment_key"] == key), {})
        return {"form_url": selected.get("url", ""), "form_id": selected.get("form_id", "") if not reason else "",
                "form_source": "private_assignment_mapping" if not reason else "",
                "form_mapping_verified": not reason, "form_binding_ambiguous": bool(reason),
                "form_multi_assignment_row": True,
                "form_source_reference": f"{title}!T{row}",
                "form_source_references": selected.get("source_references", [f"{title}!T{row}", f"{title}!U{row}"]),
                "form_visible_url": selected.get("url", ""),
                "form_visible_urls": explicit_row["visible_urls"], "form_raw_url": explicit_row["raw_url"],
                "form_review_reason": reason, "assignment_label": "",
                "form_review_action": (f"Verify one distinct Form link for each explicitly mapped assignment at {title}!T{row}; U{row} must match the primary P{row} assignment."
                                       if reason else "")}
    visible, visible_error = _turnin_cell(cells, row, 20)
    hidden, hidden_error = _turnin_cell(cells, row, 21)
    link_error = visible_error or hidden_error
    if visible and hidden and _form_destination(visible) != _form_destination(hidden):
        link_error = "assignment_form_turn_in_links_disagree"
    raw = visible or hidden
    form_url, _, observed_id = turnin_form(raw)
    column = "T" if visible else "U"
    references = [f"{title}!{c}{row}" for c, found in (("T", visible), ("U", hidden)) if found]
    targets = [r for r in range(2, 161) if map_valid
               and value(turnins, r, 3) == title and value(turnins, r, 4) == entry["week"]]
    label = value(turnins, targets[0], 1) if len(targets) == 1 else ""
    matches = {item["assignment_key"] for item in row_entries
               if label and label in {item["assignment_key"], item["assignment_title"]}}
    multiple = len({item["assignment_key"] for item in row_entries}) > 1
    labels = {value(turnins, target, 1) for target in targets}
    # Map labels may be combined weekly text in another namespace. They need
    # not equal this assignment's title, but an exact other known assignment
    # is a contradiction worth retaining for review.
    mapped_keys = {known_key for known_key, known in assignments.items()
                   if known_key in labels or (known["class_key"] == entry["class_key"]
                       and known["assignment_title"] in labels)}
    result = {"form_url": form_url or "", "form_id": "",
              "form_source": "", "form_mapping_verified": False,
              "form_binding_ambiguous": multiple and len(matches) != 1,
              "form_source_reference": f"{title}!{column}{row}",
              "form_source_references": references or [f"{title}!{column}{row}"],
              "form_visible_url": visible, "form_raw_url": hidden,
              "form_review_reason": "", "assignment_label": label}
    reason = ""
    if link_error:
        reason = link_error
    elif ((visible and _norm(value(cells, 3, 20)) != "turn it in")
          or (hidden and value(cells, 3, 21) != "_turnInURL")
          or (not visible and value(cells, 3, 21) != "_turnInURL")):
        reason = "assignment_form_layout_changed"
    elif not form_url:
        reason = "assignment_form_missing" if not raw else "assignment_form_invalid"
    elif not multiple and mapped_keys - {key}:
        reason = "assignment_form_mapping_conflict"
    elif multiple and (not map_valid or not targets):
        reason = "assignment_form_mapping_required"
    elif multiple and (len(targets) != 1 or len(matches) != 1):
        reason = "assignment_form_mapping_ambiguous"
    elif multiple and matches != {key}:
        reason = "assignment_form_belongs_to_other_assignment"
    elif not observed_id:
        reason = "assignment_form_api_id_required"
    explicit = bindings.get(key)
    error = configuration_error or binding_errors.get(key)
    if link_error:
        pass  # A private multi-deck mapping cannot override contradictory links.
    elif error:
        reason = error
    elif explicit:
        if reason == "assignment_form_mapping_conflict" or (not reason and observed_id != explicit):
            reason = "assignment_form_mapping_conflict"
        else:
            result.update(form_id=explicit, form_source="private_assignment_mapping",
                          form_mapping_verified=True)
            reason = ""
    elif not reason:
        origin = "agenda_t" if visible else "agenda_u"
        result.update(form_id=observed_id, form_source=origin + "_turnin_map" if matches == {key} else origin,
                      form_mapping_verified=True)
    result["form_review_reason"] = reason
    result["form_review_action"] = (f"Verify the visible Turn it in link at {title}!T{row} and raw link at U{row}; both must identify the same assignment Form."
                                    if link_error else "")
    return result


def _merge_catalog_forms(evidence):
    verified = [item for item in evidence if item["form_mapping_verified"]]
    ids = {item["form_id"] for item in verified}
    result = dict((verified or [item for item in evidence if item["form_url"]] or evidence)[0])
    result["form_source_references"] = list(dict.fromkeys(
        ref for item in evidence for ref in item.get("form_source_references", [item["form_source_reference"]])))
    link_conflicts = [item for item in evidence if item["form_review_reason"] in {
        "assignment_form_turn_in_links_disagree", "assignment_form_turn_in_link_invalid"}
        or (item.get("form_multi_assignment_row") and item["form_review_reason"])]
    conflicts = [item for item in evidence
                 if item["form_review_reason"] == "assignment_form_mapping_conflict"]
    if link_conflicts:
        result.update(form_id="", form_source="", form_mapping_verified=False,
                      form_review_reason=link_conflicts[0]["form_review_reason"],
                      form_review_action=link_conflicts[0]["form_review_action"])
    elif len(ids) > 1 or conflicts:
        result.update(form_id="", form_source="", form_mapping_verified=False,
                      form_review_reason="assignment_form_mapping_conflict")
    return result


def discovery_assignment(course, topic, source_id, assignments, bindings):
    """Source identity is stable; an Agenda week/title is not a saved target.

    Only an explicit deck binding or a stored exact source ID reuses a registry
    key. New Agenda lessons are visible immediately, without registering them
    or granting approval to assess/write against an invented week key.
    """
    keys = {bindings[source_id]} if source_id in bindings else set()
    for key, row in assignments.items():
        match = DECK_URL.fullmatch(str(row.get("source_url") or ""))
        if row.get("source_deck_id") == source_id or (match and match[1] == source_id):
            keys.add(key)
    if len(keys) > 1:
        raise ConnectionError("intake_registry_ambiguous")
    if keys:
        known = assignments[next(iter(keys))]
        if known["class_key"] != course:
            raise ConnectionError("intake_registry_conflict")
        return {**known, "discovery_only": False, "registry_mapping_verified": True,
                "registry_assignment_key": known["assignment_key"], "review_reasons": []}
    key = "discovery:" + course + ":" + sha256(source_id.encode()).hexdigest()[:32]
    return {"assignment_key": key, "class_key": course, "assignment_title": topic,
            "discovery_only": True, "registry_mapping_verified": False,
            "registry_assignment_key": "", "review_reasons": ["assignment_registry_binding_required"]}


def agenda_catalog(assertion, config, records, *, class_key=None, discovery=False):
    courses = tuple((course, title) for course, title in COURSES
                    if class_key is None or course == class_key)
    if not courses:
        raise ConnectionError("invalid_submission_scope")
    reader, source = authenticated_reader(assertion, config)
    metadata = reader.read(source)
    has_map = any(sheet.get("properties", {}).get("title") == "🗂 TurnIn Map"
                  for sheet in metadata.get("sheets", []))
    ranges = tuple("'%s'!A3:U60" % t for _, t in courses)
    if has_map:
        ranges += ("'🗂 TurnIn Map'!A1:D160",)
    data = reader.read(source, ranges)
    turnins = grid(data, "🗂 TurnIn Map") if has_map else {}
    map_valid = has_map and [value(turnins, 1, c) for c in (1, 2, 3, 4)] == [
        "assignment key", "target folder id", "course", "week"]
    assignments = {}
    for assignment in records.get("assignments", []):
        key = assignment.get("assignment_key")
        if not key or key in assignments:
            raise ConnectionError("intake_registry_ambiguous")
        assignments[key] = assignment
    bindings = _deck_bindings(config, assignments)
    forms, form_errors, form_config_error = _assignment_form_bindings(config, assignments)
    registry, by_deck, by_assignment, form_evidence, catalog_issues = [], {}, {}, {}, []
    for course, title in courses:
        cells = grid(data, title)
        if value(cells, 3, 16) != "_studentURL":
            raise ConnectionError("agenda_layout_changed")
        for row in range(4, 61):
            week, topic = value(cells, row, 1), value(cells, row, 3)
            row_entries = []
            sources, source_issues = assignment_source_links(cells, row, bindings)
            if source_issues and not discovery:
                raise ConnectionError("intake_catalog_needs_review")
            for column in source_issues:
                catalog_issues.append({"source_reference": f"{title}!{'P' if column == 16 else 'O'}{row}",
                    "error": "intake_catalog_needs_review",
                    "next_action": "Verify the exact primary deck and every additional assignment deck link and binding."})
            for source_entry in sources:
                column, raw, source_id = (source_entry[name] for name in ("column", "source_url", "source_deck_id"))
                number = re.fullmatch(r"(?:Wk\s*)?0*(\d{1,2})([a-z]?)", week, re.I)
                if not number or not topic:
                    if discovery:
                        catalog_issues.append({"source_reference": f"{title}!{'P' if column == 16 else 'O'}{row}",
                            "error": "intake_catalog_needs_review",
                            "next_action": "Verify this Agenda row's current lesson title, week and student-deck link."})
                        continue
                    raise ConnectionError("intake_catalog_needs_review")
                known = (discovery_assignment(course, topic, source_id, assignments, bindings)
                         if discovery else _catalog_assignment(course, number, topic, source_id, assignments, bindings))
                key = known["assignment_key"]
                if ((source_id in by_deck and by_deck[source_id] != key)
                        or (key in by_assignment and by_assignment[key] != source_id)):
                    raise ConnectionError("intake_registry_ambiguous")
                entry = {"assignment_key": key,
                         "class_key": course, "assignment_title": known["assignment_title"],
                         "week": week, "source_deck_id": source_id, "source_url": raw}
                if discovery:
                    entry.update({name: known[name] for name in (
                        "discovery_only", "registry_mapping_verified", "registry_assignment_key", "review_reasons")})
                    entry.update(source_reference=f"{title}!{'P' if column == 16 else 'O'}{row}",
                                 source_references=[f"{title}!{'P' if column == 16 else 'O'}{row}"])
                    if key in by_assignment:
                        prior = next(item for item in registry if item["assignment_key"] == key)
                        prior["source_references"].extend(entry["source_references"])
                row_entries.append(entry)
                if source_id not in by_deck:
                    by_deck[source_id], by_assignment[key] = key, source_id
                    registry.append(entry)
            if discovery and not row_entries and (value(cells, row, 21) or _turnin_cell(cells, row, 20)[0]):
                catalog_issues.append({"source_reference": f"{title}!P{row}",
                    "error": "intake_source_deck_unavailable",
                    "next_action": "Restore this assignment's current student-deck link in the Agenda."})
            for entry in {item["assignment_key"]: item for item in row_entries}.values():
                form_evidence.setdefault(entry["assignment_key"], []).append(_catalog_form(
                    entry, row_entries, cells, row, title, turnins, map_valid,
                    forms, form_errors, form_config_error, assignments, bindings))
    if not registry or len(registry) > 342:
        raise ConnectionError("intake_catalog_needs_review")
    by_form = {}
    for entry in registry:
        entry.update(_merge_catalog_forms(form_evidence[entry["assignment_key"]]))
        if discovery:
            entry["catalog_issues"] = catalog_issues
        if entry["form_mapping_verified"]:
            by_form.setdefault(entry["form_id"], []).append(entry)
    for entries in by_form.values():
        if len({entry["assignment_key"] for entry in entries}) > 1:
            for entry in entries:
                entry.update(form_id="", form_source="", form_mapping_verified=False,
                             form_review_reason="assignment_form_reused")
    return registry


def signature(text):
    """Version-specific markers AND unchanged question paragraphs, not title.

    Deliberately conservative: image-only or unmarked older decks cannot prove
    identity here. A changed question is an unmatched version, never a guess.
    """
    markers = frozenset(m.group(0) for m in MARKER.finditer(text))
    questions = frozenset(_norm(line) for line in text.splitlines()
                          if '?' in line and 30 <= len(line) <= 2000)
    return markers, questions


def match_deck(text, catalog):
    markers, questions = signature(text)
    matches = []
    for lesson in catalog:
        required, prompts = signature(lesson["source_text"])
        if (len(required) >= 3 and len(prompts) >= 3 and required == markers
                and prompts <= questions):
            matches.append(lesson)
    if len(matches) != 1:
        return None, "deck_ambiguous" if matches else "deck_unmatched_or_revision_changed"
    return matches[0], None


def read_deck(token, item):
    mime, file_id = item.get("mimeType"), item["id"]
    if mime == NATIVE:
        raw = _drive(token, "files/" + file_id + "/export", {"mimeType": PPTX}, binary=True)
    elif mime == PPTX:
        raw = _drive(token, "files/" + file_id, {"alt": "media", "supportsAllDrives": "true"}, binary=True)
    else:
        raise ConnectionError("intake_deck_unsupported")
    return _pptx_text(raw)


def read_catalog_decks(token, drive, catalog, *, deck_reader=None):
    """Read every candidate before claiming uniqueness; never drop a failure.

    This helper is also used by read-only discovery when Form access is absent.
    Content evidence then identifies a lesson only, not the submitting learner.
    The supplied catalog is not mutated and no grade or intake row is written.
    """
    sources, result = {}, []
    for lesson in catalog:
        source_id = lesson["source_deck_id"]
        if source_id not in sources:
            before = drive.metadata(source_id)
            if before.get("trashed") is not False:
                raise ConnectionError("intake_source_deck_unavailable")
            sources[source_id] = deck_reader(before) if deck_reader else read_deck(token, before)
            if drive.metadata(source_id) != before:
                raise ConnectionError("intake_source_deck_changed")
        result.append({**lesson, "source_text": sources[source_id]})
    return result


def cross_checks(ref, person, lesson, question_ids):
    holds = []
    expected = {
        "name": _aliases(person),
        "section": {_norm(person["section_name"])},
        "assignment": {_norm(lesson["assignment_title"]), _norm(lesson["assignment_key"]),
                       _norm(lesson["source_url"])},
    }
    for role, allowed in expected.items():
        qid = question_ids.get(role)
        if not qid:
            holds.append("typed_" + role + "_crosscheck_unconfigured")
            continue
        values = ref.get("typed_answers", {}).get(qid, [])
        if len(values) != 1 or _norm(values[0]) not in allowed:
            holds.append("typed_" + role + "_disagreement")
    return holds


def collect_shared(assertion, config, records):
    form_id = str(config.get("HUB_TURNIN_FORM_ID", ""))
    if not FORM_ID.fullmatch(form_id):
        raise ConnectionError("submission_form_not_configured")
    service = config.get("HUB_READER_SERVICE_ACCOUNT", "")
    refs, evidence = response_file_refs(FormsReader(service_token(service, FORMS_SCOPE)),
                                        form_id, collected_identity=True)
    token = service_token(service, DRIVE_SCOPE)
    drive = DriveReader(token)
    root_id = str(config.get("HUB_SUBMISSIONS_ROOT_FOLDER", ""))
    root = drive.metadata(root_id)
    if root.get("trashed") is not False or root.get("mimeType") != FOLDER:
        raise ConnectionError("submission_folder_inactive")
    found, checked = locate_files_under_root(drive, list(refs), root_id) if refs else ({}, 0)
    catalog = agenda_catalog(assertion, config, records)
    # Complete candidate catalog is required to make a uniqueness claim. Never
    # silently discard inaccessible source decks, which could also match.
    catalog = read_catalog_decks(token, drive, catalog)
    roster = read_roster(config)
    domains = str(config.get("HUB_SCHOOL_EMAIL_DOMAINS", "")).split(',')
    sections = private_mapping(config, "HUB_ROSTER_SECTION_IDS")
    questions = private_mapping(config, "HUB_TURNIN_QUESTION_IDS")
    files, review = [], []
    for fid in sorted(refs):
        ref = refs[fid]
        if fid not in found:
            review.append({"file_id": fid, "reason": "form_reference_outside_approved_root"})
            continue
        item = found[fid]
        holds = []
        person, reason = match_respondent(ref.get("respondent_email"), roster, domains,
                                          str(config.get("HUB_EMAIL_USERNAME_PREFIX", "s.")))
        if reason:
            holds.append(reason)
        if ref.get("identity_conflict"):
            person = None
            holds.append("respondent_evidence_conflict")
        try:
            before = drive.metadata(fid)
            if before != item:
                raise ConnectionError("intake_upload_changed")
            lesson, reason = match_deck(read_deck(token, item), catalog)
            if drive.metadata(fid) != before:
                raise ConnectionError("intake_upload_changed")
        except ConnectionError:
            lesson, reason = None, "uploaded_deck_unreadable"
        if reason:
            holds.append(reason)
        if person and lesson and person["class_key"] != lesson["class_key"]:
            holds.append("roster_deck_class_disagreement")
            person = None
        section = str(sections.get(person["section_name"], "")) if person else ""
        if person and not section:
            holds.append("section_mapping_unverified")
        if person and lesson:
            holds.extend(cross_checks(ref, person, lesson, questions))
        for reason in holds:
            review.append({"file_id": fid, "reason": reason})
        files.append({**item, "submitted_at": ref.get("submitted_at"),
                      "assignment_key": lesson["assignment_key"] if lesson else None,
                      "class_key": lesson["class_key"] if lesson else None,
                      "respondent_email": ref.get("respondent_email"),
                      "roster_match": person, "section_id": section,
                      "intake_holds": holds, "identity_basis": "collected_email_deck_roster"})
    # Detect edits to Agenda mapping while source decks were being inspected.
    fresh = agenda_catalog(assertion, config, records)
    original = [{k:v for k,v in r.items() if k != "source_text"} for r in catalog]
    if fresh != original:
        raise ConnectionError("submission_agenda_changed")
    return files, {**evidence, "root_folders_checked": checked,
                   "root_verified_files": len(files), "form_source": "shared_collected_identity",
                   "form_refs_outside_root": len(set(refs) - set(found)),
                   "needs_review": review, "catalog": original,
                   "catalog_hash": content_hash(original)}
