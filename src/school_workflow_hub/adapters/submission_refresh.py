"""Fresh, read-only submission discovery for the existing Student work table.

Drive presence is distinct from identity, assignment matching and assessment.
This path never registers rows, persists a baseline, invokes a model or posts a
score, including when the separate intake writer is enabled in configuration.
"""
from datetime import datetime, timezone
import re

from .grading_records import CLASSES, read_records
from .google_sheets import SheetsReadError
from .hosted_agenda import ConnectionError, service_token
from .submission_inventory import (DRIVE_SCOPE, FOLDER, FORM, SHORTCUT,
    MAX_LOCATE_DEPTH, MAX_LOCATE_FOLDERS, MAX_LOCATE_PAGES)
from .submission_refresh_reader import BudgetedDrive as DriveReader
from .forms_submissions import FormsReader, FORMS_SCOPE
from . import assignment_form_intake as assignment_forms
from .roster_matching import read_roster, match_respondent
from . import shared_form_intake as intake

SCHEMA = "hub-submission-refresh/v1"
MAX_FILES = 500
FILE_ID = re.compile(r"[A-Za-z0-9_-]{20,128}")


class _BudgetedForms:
    def __init__(self, reader, check):
        self.reader, self.check = reader, check

    def responses(self, form_id, cursor=None):
        self.check()
        return self.reader.responses(form_id, cursor)


def _code(error, fallback):
    code = str(error)
    return code if re.fullmatch(r"[a-z0-9_]{1,64}", code) else fallback


def source_id(row):
    explicit = str(row.get("source_file_id") or "")
    if FILE_ID.fullmatch(explicit):
        return explicit
    from urllib.parse import urlparse, parse_qs
    url = urlparse(str(row.get("source_url") or ""))
    if url.scheme != "https" or url.hostname not in {"drive.google.com", "docs.google.com"}:
        return ""
    match = re.search(r"/d/([A-Za-z0-9_-]{20,128})(?:/|$)", url.path)
    candidate = match[1] if match else parse_qs(url.query).get("id", [""])[0]
    return candidate if FILE_ID.fullmatch(candidate) else ""


def discover_tree(reader, root_id):
    """Walk every page and child folder, regardless of saved submission IDs.

    Partial reads are explicit and cannot establish missing work or advance a
    baseline. Shortcuts are reported; they never escape the approved root.
    """
    root = reader.metadata(root_id)
    if root.get("mimeType") != FOLDER or root.get("trashed") is not False:
        raise ConnectionError("submission_folder_inactive")
    files, seen, level, pages, errors = {}, {root_id}, [root_id], 0, []
    try:
        for _ in range(MAX_LOCATE_DEPTH):
            if not level:
                break
            following = []
            for start in range(0, len(level), 25):
                batch, cursor, cursors = level[start:start + 25], None, set()
                while True:
                    pages += 1
                    if pages > MAX_LOCATE_PAGES:
                        raise ConnectionError("submission_scan_incomplete")
                    page = reader.children_of(batch, cursor)
                    if page.get("incompleteSearch") or not isinstance(page.get("files"), list):
                        raise ConnectionError("submission_scan_incomplete")
                    for item in page["files"]:
                        fid = item.get("id", "")
                        if (not FILE_ID.fullmatch(fid) or item.get("trashed") is not False
                                or not set(item.get("parents") or []) & set(batch)):
                            raise ConnectionError("submission_scan_incomplete")
                        mime = item.get("mimeType")
                        if mime == FOLDER:
                            if fid not in seen:
                                if len(seen) >= MAX_LOCATE_FOLDERS:
                                    raise ConnectionError("submission_scan_incomplete")
                                seen.add(fid)
                                following.append(fid)
                        elif mime == SHORTCUT:
                            errors.append("submission_shortcut_needs_review")
                        elif mime != FORM:
                            if fid in files and files[fid] != item:
                                raise ConnectionError("intake_upload_changed")
                            if fid not in files and len(files) >= MAX_FILES:
                                raise ConnectionError("submission_scan_incomplete")
                            files[fid] = item
                    cursor = page.get("nextPageToken")
                    if not cursor:
                        break
                    if cursor in cursors:
                        raise ConnectionError("submission_scan_incomplete")
                    cursors.add(cursor)
            level = following
        if level:
            errors.append("submission_scan_incomplete")
    except ConnectionError as error:
        errors.append(_code(error, "submission_source_unavailable"))
    return list(files.values()), {"drive_complete": not errors,
        "folders_checked": len(seen), "pages_checked": pages,
        "errors": sorted(set(errors))}


def _time(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.utcoffset() is not None else None
    except (ValueError, TypeError):
        return None


def _assessment_state(item, history):
    assessed = [r for r in history if r.get("status") == "assessed"]
    if not assessed:
        return "new_ungraded", []
    # A numeric Drive version is comparable with the stored capture version.
    version = str(item.get("version") or "")
    versions = {str(r.get("source_revision") or "") for r in assessed}
    if len(versions) > 1:
        return "needs_review", ["assessment_versions_differ"]
    if version.isdigit() and version in versions:
        return "unchanged", []
    if version.isdigit() and all(v.isdigit() for v in versions):
        return "changed_since_assessment", []
    # Legacy content hashes are not Drive versions. A strictly later change
    # proves staleness; an earlier timestamp alone cannot prove equal content.
    modified = _time(item.get("modifiedTime"))
    checked = [_time(r.get("checked_at")) for r in assessed]
    if modified and all(checked) and modified > max(checked):
        return "changed_since_assessment", []
    return "needs_review", ["assessment_revision_unverified"]


def _saved_person(history, roster, sections):
    eligible = [r for r in history if r.get("status") in {"assessed", "roster_matched"}
                and not r.get("hold_reason")]
    identities = {(r.get("class_key"), r.get("section_id"), r.get("learner_key"))
                  for r in eligible}
    if not identities:
        return None, None
    if len(identities) != 1:
        return None, "saved_identity_conflict"
    course, section, learner = next(iter(identities))
    matches = [p for p in roster if learner and p.get("learner_key") == learner
               and p.get("class_key") == course
               and str(sections.get(p["section_name"], "")) == section]
    return (matches[0], None) if len(matches) == 1 else (None, "saved_identity_needs_review")


def refresh(assertion, payload, config):
    if (not isinstance(payload, dict) or set(payload) != {"class_key", "assignment_key"}
            or payload["class_key"] not in CLASSES
            or not isinstance(payload["assignment_key"], str)
            or not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", payload["assignment_key"])):
        raise ValueError("invalid_submission_scope")
    class_key, selected = payload["class_key"], payload["assignment_key"]
    records = read_records(assertion, config)  # Authorize before any school read.
    assignments = {a["assignment_key"]: a for a in records["assignments"]}
    if selected != "all" and assignments.get(selected, {}).get("class_key") != class_key:
        raise ValueError("invalid_submission_scope")
    root_id = str(config.get("HUB_SUBMISSIONS_ROOT_FOLDER", ""))
    if not FILE_ID.fullmatch(root_id):
        raise ConnectionError("submission_root_not_configured")
    service = config.get("HUB_READER_SERVICE_ACCOUNT", "")
    token = service_token(service, DRIVE_SCOPE)
    drive = DriveReader(token)
    found, coverage = discover_tree(drive, root_id)
    errors = list(coverage["errors"])
    # Resolve assignment-specific Form links before reading source-deck content.
    # An inaccessible master cannot hide the independently readable Form index.
    original_catalog, catalog, catalog_complete = [], [], False
    try:
        drive.check()
        original_catalog = intake.agenda_catalog(assertion, config, records, class_key=class_key)
    except (ConnectionError, SheetsReadError) as error:
        errors.append(_code(error, "intake_catalog_needs_review"))
    refs, form_complete = {}, False
    try:
        forms = _BudgetedForms(FormsReader(service_token(service, FORMS_SCOPE)), drive.check)
        refs, evidence = assignment_forms.collect_assignment_forms(
            forms, original_catalog, config, assignment_key=selected)
        form_complete = evidence["complete"]
        coverage.update(forms_checked=evidence["forms_checked"],
                        forms_complete=evidence["forms_complete"],
                        responses_checked=evidence["responses_checked"],
                        form_results=evidence["assignments"])
        errors.extend(evidence["errors"])
    except ConnectionError as error:
        errors.append(_code(error, "submission_form_unavailable"))
    outside = set(refs) - {f["id"] for f in found}
    if outside:
        errors.append("form_reference_outside_approved_root" if coverage["drive_complete"]
                      else "form_references_unlocated")
    coverage["form_refs_outside_root"] = len(outside) if coverage["drive_complete"] else 0
    coverage["form_refs_unlocated"] = len(outside) if not coverage["drive_complete"] else 0
    roster, sections, roster_complete = [], {}, False
    try:
        drive.check()
        roster = read_roster(config)
        sections = intake.private_mapping(config, "HUB_ROSTER_SECTION_IDS")
        roster_complete = True
    except ConnectionError as error:
        errors.append(_code(error, "roster_source_unavailable"))
    if original_catalog:
        try:
            drive.check()
            catalog = intake.read_catalog_decks(token, drive, original_catalog,
                                                 deck_reader=drive.read_deck)
            catalog_complete = True
        except (ConnectionError, SheetsReadError) as error:
            errors.append(_code(error, "intake_catalog_needs_review"))
    catalog_by_key = {row["assignment_key"]: row for row in original_catalog}
    domains = str(config.get("HUB_SCHOOL_EMAIL_DOMAINS", "")).split(",")
    by_file = {}
    for row in records.get("submissions", []):
        fid = source_id(row)
        if fid:
            by_file.setdefault(fid, []).append(row)
    output = []
    ignored_non_decks = 0
    for item in sorted(found, key=lambda f: f["id"]):
        fid = item["id"]
        history = by_file.get(fid, [])
        # Generated Docs/Sheets reports in private class folders are not deck
        # submissions. A Form reference or saved source remains visible even
        # for an unsupported type, so an actual upload is never silently lost.
        if item.get("mimeType") not in {intake.NATIVE, intake.PPTX} and not history and fid not in refs:
            ignored_non_decks += 1
            continue
        active = [r for r in history if r.get("status") != "superseded"]
        holds = []
        if any(r.get("hold_reason") for r in active):
            holds.append("saved_record_needs_review")
        if history and not active:
            holds.append("prior_submission_superseded")
        person, reason = _saved_person(history, roster, sections)
        if reason:
            holds.append(reason)
        ref = refs.get(fid)
        if ref:
            holds.extend(ref.get("review_reasons", []))
            # A dedicated Form establishes the course, so a learner taking
            # another course as well is not an identity conflict. Both A2/A3
            # remain in this roster scope and still require a unique match.
            course_roster = [p for p in roster if p["class_key"] == class_key]
            fresh_person, reason = match_respondent(ref.get("respondent_email"), course_roster,
                domains, str(config.get("HUB_EMAIL_USERNAME_PREFIX", "s.")))
            if reason == "roster_identity_unmatched":
                other, _ = match_respondent(ref.get("respondent_email"), roster,
                    domains, str(config.get("HUB_EMAIL_USERNAME_PREFIX", "s.")))
                if other and other["class_key"] != class_key:
                    fresh_person, reason = other, None
            if reason:
                holds.append(reason)
            if ref.get("identity_conflict"):
                fresh_person = None
                holds.append("respondent_evidence_conflict")
            if person and fresh_person and person["learner_key"] != fresh_person["learner_key"]:
                holds.append("saved_identity_conflict")
                person = None
            else:
                person = fresh_person
        elif not person:
            holds.append("form_identity_unavailable")
        section = str(sections.get(person["section_name"], "")) if person else ""
        if person and not section:
            holds.append("section_mapping_unverified")
        # This reference came from a Form bound to the selected class. A
        # respondent from another class is a visible mismatch, not a reason
        # to hide the upload or assign that learner to this class.
        if person and ref and person["class_key"] != class_key:
            holds.append("roster_form_class_disagreement")
            person, section = None, ""
        lesson = None
        if catalog_complete:
            try:
                before = drive.metadata(fid)
                if before != item:
                    raise ConnectionError("intake_upload_changed")
                lesson, reason = intake.match_deck(drive.read_deck(item), catalog)
                if drive.metadata(fid) != before:
                    lesson = None
                    raise ConnectionError("intake_upload_changed")
                if reason:
                    holds.append(reason)
            except ConnectionError as error:
                code = _code(error, "uploaded_deck_unreadable")
                holds.append(code)
                if code == "submission_refresh_budget_exhausted":
                    errors.append(code)
        else:
            holds.append("intake_catalog_needs_review")
        # A verified dedicated Form is independent assignment evidence. Keep
        # that exact binding visible if source content needs review; do not
        # replace it with a title/date/folder guess or hide a readable Form.
        form_lesson = catalog_by_key.get(ref.get("assignment_key")) if ref else None
        content_assignment = lesson["assignment_key"] if lesson else ""
        if ref and ref.get("assignment_conflict"):
            lesson = None
        elif form_lesson and lesson and form_lesson["assignment_key"] != lesson["assignment_key"]:
            holds.append("form_deck_assignment_disagreement")
            lesson = form_lesson
        elif form_lesson and not lesson:
            lesson = form_lesson
        if person and lesson and person["class_key"] != lesson["class_key"]:
            holds.append("roster_deck_class_disagreement")
            person, section = None, ""
        if lesson and lesson["class_key"] != class_key:
            continue
        if person and person["class_key"] != class_key:
            continue
        if person and lesson and ref:
            holds.extend(assignment_forms.cross_checks(ref, person, lesson, config))
        key = lesson["assignment_key"] if lesson else ""
        previous = {r.get("assignment_key") for r in active}
        if key and previous and previous != {key}:
            holds.append("saved_assignment_conflict")
        if selected != "all" and key and key != selected:
            continue
        state, revision_holds = _assessment_state(item, history)
        holds.extend(revision_holds)
        if holds:
            state = "needs_review"
        native_ids = {r.get("student_id") for r in active if person
                      and r.get("learner_key") == person["learner_key"]
                      and r.get("section_id") == section and r.get("student_id")}
        student_id = next(iter(native_ids)) if len(native_ids) == 1 else person["display_id"] if person else ""
        if len(native_ids) > 1:
            holds.append("saved_identity_conflict")
            state = "needs_review"
        output.append({"file_id": fid, "name": item.get("name", ""),
            "source_url": "https://drive.google.com/file/d/" + fid + "/view",
            "class_key": lesson["class_key"] if lesson else person["class_key"] if person else "",
            "assignment_key": key, "assignment_title": lesson["assignment_title"] if lesson else "",
            "section_id": section, "section_name": person["section_name"] if person else "",
            "student_id": student_id,
            "learner_key": person["learner_key"] if person else "",
            "display_name": (person["first"] + " " + person["last"]) if person else "",
            "discovery_status": state, "review_reasons": sorted(set(holds)),
            "modified_at": item.get("modifiedTime", ""), "version": str(item.get("version") or ""),
            "saved_record_ids": [r["record_id"] for r in history],
            "form_ids": ref.get("form_ids", []) if ref else [],
            "form_assignment_keys": ref.get("assignment_keys", []) if ref else [],
            "content_assignment_key": content_assignment,
            "source_changed_since_assessment": _assessment_state(item, history)[0] == "changed_since_assessment"})
    groups = {}
    for row in output:
        if row["learner_key"] and row["assignment_key"] and "prior_submission_superseded" not in row["review_reasons"]:
            scope = (row["section_id"], row["learner_key"], row["assignment_key"])
            groups.setdefault(scope, []).append(row)
    for group in groups.values():
        if len(group) > 1:
            for row in group:
                row["review_reasons"] = sorted(set(row["review_reasons"] + ["competing_submission_versions"]))
                row["discovery_status"] = "needs_review"
                row["related_file_ids"] = [other["file_id"] for other in group if other is not row]
    if original_catalog:
        try:
            drive.check()
            if intake.agenda_catalog(assertion, config, records, class_key=class_key) != original_catalog:
                raise ConnectionError("submission_agenda_changed")
        except ConnectionError as error:
            if str(error) != "submission_refresh_budget_exhausted":
                raise
            errors.append(str(error))
            for row in output:
                row["review_reasons"] = sorted(set(row["review_reasons"] + ["catalog_recheck_incomplete"]))
                row["discovery_status"] = "needs_review"
    coverage.update(form_complete=form_complete, roster_complete=roster_complete,
                    catalog_complete=catalog_complete, files_checked=len(found),
                    ignored_non_deck_files=ignored_non_decks,
                    errors=sorted(set(errors)), complete=not errors)
    return {"schema_version": SCHEMA, "provenance": "source_read", "external_writes": 0,
            "live_model_calls": 0, "class_key": class_key, "assignment_key": selected,
            "discovered_at": datetime.now(timezone.utc).isoformat(),
            "records_checked_at": records.get("checked_at"), "coverage": coverage, "files": output}
