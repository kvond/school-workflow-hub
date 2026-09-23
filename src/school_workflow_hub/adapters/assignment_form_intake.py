"""Read each assignment's own Form; never fall back to a global/shared Form.

The Agenda catalog resolves the exact assignment-to-editable-Form binding.
Successful Forms remain usable when another Form is unavailable or incomplete.
File references preserve per-Form evidence; neither typed answers nor filenames
choose an assignment or student. This module makes no school writes.
"""
from datetime import datetime
import json
import re

from .dated_agenda import turnin_form
from .forms_submissions import FORM_ID, MAX_FILES, response_file_refs
from .hosted_agenda import ConnectionError
from .roster_matching import _aliases, _norm

MAX_FORMS = 342
ROLES = {"name", "section", "assignment"}


def _code(error, fallback):
    code = str(error)
    return code if re.fullmatch(r"[a-z0-9_]{1,80}", code) else fallback


def _binding(entry):
    """Validate a catalog binding without inventing an editable published ID."""
    if entry.get("form_mapping_verified") is not True:
        return None, _code(entry.get("form_review_reason") or "",
                           "assignment_form_mapping_required")
    form_id = entry.get("form_id")
    if not isinstance(form_id, str) or not FORM_ID.fullmatch(form_id):
        return None, "assignment_form_mapping_required"
    observed = entry.get("form_url") or ""
    url, _, observed_id = turnin_form(observed)
    if entry.get("form_source") != "private_assignment_mapping":
        if not url or observed_id != form_id or entry.get("form_binding_ambiguous"):
            return None, "assignment_form_mapping_conflict"
    return form_id, None


def _latest(event):
    # response_file_refs already requires a valid timestamp with timezone.
    return (datetime.fromisoformat(event["submitted_at"].replace("Z", "+00:00")),
            event["form_id"], event.get("response_id", ""))


def _merge(prior, ref, binding):
    event = {**ref, "form_id": binding["form_id"],
             "assignment_key": binding["assignment_key"],
             "form_url": binding.get("form_url", ""),
             "form_source": binding.get("form_source", ""),
             "form_source_reference": binding.get("form_source_reference", ""),
             "form_source_references": binding.get("form_source_references", [])}
    events = list(prior.get("source_events", [])) if prior else []
    if event not in events:
        events.append(event)
    events.sort(key=_latest)
    result = dict(events[-1])
    result["source_events"] = events
    form_ids = sorted({e["form_id"] for e in events})
    assignments = sorted({e["assignment_key"] for e in events})
    reasons = []
    result["identity_conflict"] = (any(e.get("identity_conflict") for e in events)
                                   or len({e.get("respondent_email", "") for e in events}) > 1)
    if result["identity_conflict"]:
        reasons.append("respondent_evidence_conflict")
    result["assignment_conflict"] = len(assignments) != 1
    if result["assignment_conflict"]:
        reasons.append("form_assignment_conflict")
        result["assignment_key"] = ""
    if len(form_ids) != 1:
        result["form_id"] = ""
    result["upload_evidence_conflict"] = len({(e.get("form_name"), e.get("form_mime_type"))
                                              for e in events}) > 1
    if result["upload_evidence_conflict"]:
        reasons.append("form_upload_evidence_conflict")
    result.update(form_ids=form_ids, assignment_keys=assignments, review_reasons=reasons)
    return result


def collect_assignment_forms(reader, catalog, config=None, *, assignment_key=None):
    """Return exact upload references and independent per-assignment coverage.

    ``reader.responses`` may be budget-wrapped by the caller. Each Form uses the
    existing complete-pagination collector. A failed/incomplete Form contributes
    no partial references. ``config`` is accepted for the host call convention;
    only verified catalog bindings are used, never HUB_TURNIN_FORM_ID.
    ``source_events`` retains each Form's latest file reference and its retained
    identity-conflict flag, not an invented complete history of response edits.
    """
    entries = [e for e in catalog if assignment_key in (None, "all", e.get("assignment_key"))]
    if assignment_key not in (None, "all") and not entries:
        raise ConnectionError("assignment_form_mapping_required")
    bindings, by_assignment = [], {}
    for entry in entries:
        key = entry.get("assignment_key")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", key):
            raise ConnectionError("assignment_form_mapping_required")
        form_id, error = _binding(entry)
        binding = {"assignment_key": key, "form_id": form_id or "",
                   "form_url": entry.get("form_url", ""),
                   "form_source": entry.get("form_source", ""),
                   "form_source_reference": entry.get("form_source_reference", ""),
                   "form_source_references": list(entry.get("form_source_references", [])),
                   "complete": False, "error": error, "pages": 0,
                   "responses_checked": 0, "files_referenced": 0}
        previous = by_assignment.get(key)
        if previous:
            if previous["form_id"] != binding["form_id"] or previous["error"] != error:
                previous["error"] = "assignment_form_mapping_conflict"
            previous["form_source_references"] = sorted(set(
                previous["form_source_references"] + binding["form_source_references"]))
            continue
        by_assignment[key] = binding
        bindings.append(binding)
    # The same Form cannot silently stand in for two assignments. Detect this
    # before reading it, including when two catalog entries were explicitly bound.
    by_form = {}
    for binding in bindings:
        if binding["form_id"]:
            by_form.setdefault(binding["form_id"], []).append(binding)
    for group in by_form.values():
        if len(group) > 1:
            for binding in group:
                binding["error"] = "assignment_form_mapping_conflict"
    refs, forms_checked = {}, 0
    for binding in bindings:
        if binding["error"]:
            continue
        if forms_checked >= MAX_FORMS:
            binding["error"] = "assignment_form_scan_incomplete"
            continue
        forms_checked += 1
        try:
            found, evidence = response_file_refs(reader, binding["form_id"], collected_identity=True)
            if len(set(refs) | set(found)) > MAX_FILES:
                raise ConnectionError("assignment_form_scan_incomplete")
            # Merge only after all pages passed validation; no partial Form is
            # allowed to prove an identity or cancel another assignment's result.
            for fid, ref in found.items():
                refs[fid] = _merge(refs.get(fid), ref, binding)
            binding.update(complete=True, pages=evidence["pages"],
                           responses_checked=evidence["responses_checked"], files_referenced=len(found))
        except ConnectionError as error:
            binding["error"] = _code(error, "submission_form_unavailable")
    errors = sorted({b["error"] for b in bindings if b["error"]})
    if not bindings:
        errors.append("assignment_form_mapping_required")
    review = [{"file_id": fid, "reason": reason,
               "form_ids": ref["form_ids"], "assignment_keys": ref["assignment_keys"]}
              for fid, ref in sorted(refs.items()) for reason in ref["review_reasons"]]
    return refs, {"source": "assignment_forms", "complete": bool(bindings) and not errors,
                  "forms_checked": forms_checked, "forms_complete": sum(b["complete"] for b in bindings),
                  "pages": sum(b["pages"] for b in bindings),
                  "responses_checked": sum(b["responses_checked"] for b in bindings),
                  "assignments": bindings, "errors": errors, "needs_review": review}


def cross_checks(ref, person, lesson, config):
    """Optional, explicitly configured typed fields never establish identity."""
    raw = config.get("HUB_ASSIGNMENT_FORM_QUESTION_IDS", {})
    try:
        mappings = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(mappings, dict):
            raise ValueError()
        key = ref.get("assignment_key") or lesson["assignment_key"]
        roles = mappings.get(key, {})
        if (not isinstance(roles, dict) or set(roles) - ROLES
                or any(not isinstance(qid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", qid)
                       for qid in roles.values())
                or len(set(roles.values())) != len(roles)):
            raise ValueError()
    except (ValueError, TypeError):
        return ["typed_crosscheck_configuration_invalid"]
    if ref.get("assignment_conflict"):
        return ["form_assignment_conflict"]
    if key != lesson["assignment_key"]:
        return ["form_deck_assignment_disagreement"]
    allowed = {"name": _aliases(person), "section": {_norm(person["section_name"])},
               "assignment": {_norm(lesson["assignment_title"]), _norm(lesson["assignment_key"]),
                              _norm(lesson.get("source_url", ""))}}
    holds = []
    for role, qid in roles.items():
        answers = ref.get("typed_answers", {}).get(qid, [])
        if len(answers) != 1 or _norm(answers[0]) not in allowed[role]:
            holds.append("typed_" + role + "_disagreement")
    return holds
