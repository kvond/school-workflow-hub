"""Read-only shared-Form intake. No typed answer establishes identity.

All uploaded files are root-verified before content is read. A lesson requires
one unique match to the current Agenda student decks; uncertain versions stay
in review. Nothing here assesses, moves a file, changes a roster or posts grades.
"""
import json
import re

from .dated_agenda import COURSES, grid, value, link
from .hosted_agenda import authenticated_reader, service_token, ConnectionError
from .forms_submissions import FormsReader, FORMS_SCOPE, response_file_refs, FORM_ID
from .submission_inventory import DriveReader, DRIVE_SCOPE, FOLDER, locate_files_under_root
from .roster_matching import read_roster, match_respondent, _norm, _aliases
from .assessment_save import _drive, _pptx_text, PPTX
from ..core.runs import content_hash

NATIVE = "application/vnd.google-apps.presentation"
MARKER = re.compile(r"\[\[(NOTES|DRAFT|OPTIONAL|BANK):([^:\]\s]+):([^\]\r\n]+)\]\]")
DECK_URL = re.compile(r"https://docs\.google\.com/presentation/d/([\w-]{20,128})/(?:copy|edit|view|preview)(?:[?#].*)?")


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


def agenda_catalog(assertion, config, records, *, class_key=None):
    courses = tuple((course, title) for course, title in COURSES
                    if class_key is None or course == class_key)
    if not courses:
        raise ConnectionError("invalid_submission_scope")
    reader, source = authenticated_reader(assertion, config)
    data = reader.read(source, tuple("'%s'!A3:U60" % t for _, t in courses))
    assignments = {}
    for assignment in records.get("assignments", []):
        key = assignment.get("assignment_key")
        if not key or key in assignments:
            raise ConnectionError("intake_registry_ambiguous")
        assignments[key] = assignment
    bindings = _deck_bindings(config, assignments)
    registry, by_deck, by_assignment = [], {}, {}
    for course, title in courses:
        cells = grid(data, title)
        if value(cells, 3, 16) != "_studentURL":
            raise ConnectionError("agenda_layout_changed")
        for row in range(4, 61):
            week, topic = value(cells, row, 1), value(cells, row, 3)
            for column in (16, 15):
                raw = link(cells.get((row, column), {}).get("hyperlink", "")) or link(value(cells, row, column))
                if not raw:
                    continue
                match = DECK_URL.fullmatch(raw)
                if column == 15 and (not match or match[1] not in bindings):
                    continue  # A link alone does not make O a student assignment.
                number = re.fullmatch(r"(?:Wk\s*)?0*(\d{1,2})([a-z]?)", week, re.I)
                if not match or not number or not topic:
                    raise ConnectionError("intake_catalog_needs_review")
                known = _catalog_assignment(course, number, topic, match[1], assignments, bindings)
                key, source_id = known["assignment_key"], match[1]
                if ((source_id in by_deck and by_deck[source_id] != key)
                        or (key in by_assignment and by_assignment[key] != source_id)):
                    raise ConnectionError("intake_registry_ambiguous")
                if source_id in by_deck:
                    continue  # Same exact lesson link repeated in the Agenda.
                by_deck[source_id], by_assignment[key] = key, source_id
                registry.append({"assignment_key": key,
                                 "class_key": course, "assignment_title": known["assignment_title"],
                                 "week": week, "source_deck_id": source_id, "source_url": raw})
    if not registry or len(registry) > 342:
        raise ConnectionError("intake_catalog_needs_review")
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
