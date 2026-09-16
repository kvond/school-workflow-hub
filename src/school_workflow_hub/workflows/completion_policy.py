"""Prompt-1 arithmetic over assessed evidence, not an assessor of student prose.

The authorized extraction/review layer must inspect actual writing and supply
meaningful_attempt/blank/printed_copy/review labels. Marker presence, string
length and a filled box are never used here to judge meaningful completion.
No provider, file, model, grade ledger or external system is changed.
"""
from collections import Counter
from dataclasses import asdict, dataclass
from fractions import Fraction
import re

POLICY_VERSION = "completion-policy/2026-09-16"
CATEGORIES = ("NOTES", "BANK", "DRAFT")


@dataclass(frozen=True)
class ReadinessAssessment:
    inventory_line: str
    inventory_verified: bool
    required_ids: dict
    observed_ids: dict
    lesson_sequence_verified: bool
    conflict_required: bool
    conflict_after_bank_verified: bool
    evidence_reference: str


@dataclass(frozen=True)
class AssessedResponse:
    category: str
    question_id: str
    judgment: str
    evidence_reference: str
    optional: bool = False


@dataclass(frozen=True)
class ManualResponseMapping:
    """A reviewed link from one due prompt to one inspected response field.

    The response reference identifies the actual field, not its marker label.
    A reviewer can therefore resolve duplicate/missing marker labels without
    merging different answers or counting one field for several prompts.
    """
    category: str
    question_id: str
    response_evidence_reference: str
    evidence_reference: str


@dataclass(frozen=True)
class ManualReconciliation:
    """Explicit evidence for the distributed version and this checkpoint.

    References are opaque private-evidence keys, not proof by themselves. The
    caller must bind them to inspected immutable source/revision evidence.
    This record neither judges writing nor grants permission to post a grade.
    """
    readiness_evidence_reference: str
    submission_revision_reference: str
    requirements_reference: str
    stage_reference: str
    reviewer_reference: str
    evidence_reference: str
    stage: str
    required_ids: dict
    response_mappings: tuple
    requirements_verified: bool
    bank_sequence_verified: bool = False
    bank_sequence_evidence_reference: str = ""


def _key(value):
    return isinstance(value, str) and value != "UNVERIFIED" and bool(
        re.fullmatch(r"[A-Za-z0-9:_-]{1,160}", value))


def check_readiness(item):
    if not isinstance(item, ReadinessAssessment):
        raise ValueError("invalid_readiness_assessment")
    issues = []
    if (not isinstance(item.inventory_line, str)
            or not item.inventory_line.startswith("[[MARKER-INVENTORY:")
            or item.inventory_verified is not True or not _key(item.evidence_reference)):
        issues.append("inventory_unverified")
    if item.lesson_sequence_verified is not True:
        issues.append("lesson_sequence_unverified")
    if type(item.conflict_required) is not bool:
        issues.append("conflict_requirement_unverified")
    elif item.conflict_required and item.conflict_after_bank_verified is not True:
        issues.append("conflict_sequence_unverified")
    missing, extra = {}, {}
    if (not isinstance(item.required_ids, dict) or set(item.required_ids) != set(CATEGORIES)
            or not isinstance(item.observed_ids, dict) or set(item.observed_ids) != set(CATEGORIES)):
        issues.append("inventory_categories_invalid")
    else:
        for category in CATEGORIES:
            required, observed = item.required_ids[category], item.observed_ids[category]
            if (not isinstance(required, tuple) or not isinstance(observed, tuple)
                    or len(required) > 1000 or len(observed) > 1000
                    or any(not isinstance(k, str) or not k.strip() or len(k) > 160
                           for k in required + observed)):
                issues.append("inventory_identifiers_invalid")
                continue
            if len(set(required)) != len(required) or len(set(observed)) != len(observed):
                issues.append("duplicate_required_marker")
            missing[category] = sorted((Counter(required) - Counter(observed)).elements())
            extra[category] = sorted((Counter(observed) - Counter(required)).elements())
            if missing[category] or extra[category]:
                issues.append("marker_inventory_mismatch")
    return {"status": "NOT READY" if issues else "READY",
            "issues": sorted(set(issues)), "missing_ids": missing, "extra_ids": extra,
            "inventory_line": item.inventory_line,
            "evidence_reference": item.evidence_reference}


def _score(completed, required, *, due=True):
    value = Fraction(10 * completed, required) if due and required else None
    return {"completed": completed, "required": required,
            "status": "not_yet_due" if not due else "N/A" if not required else "proposed",
            "out_of": 10, "value": float(value) if value is not None else None,
            "exact_value": str(value) if value is not None else None}


def assess_completion(readiness, responses, *, stage, manual_reconciliation=None):
    """Return two proposed /10 scores with explicit stage denominators.

    This does not prove readiness itself: the upstream assessment must be
    revision-bound to the actual inventory, lesson plan and inspected responses.
    Earlier grades/timing are deliberately not inputs and cannot be overwritten
    or invented. All meaningful current attempts earn identical credit.
    """
    if stage not in {"initial", "final"}:
        raise ValueError("invalid_assessment_stage")
    gate = check_readiness(readiness)
    base = {"policy_version": POLICY_VERSION, "stage": stage, "readiness": gate,
            "status": "needs_review", "scores": None, "growth_claim": "not_inferred",
            "external_writes": 0}
    if manual_reconciliation is not None:
        return _assess_reconciled(readiness, responses, stage, manual_reconciliation, base)
    if gate["status"] != "READY":
        return dict(base, issues=gate["issues"])
    if not isinstance(responses, (tuple, list)) or len(responses) > 3000:
        raise ValueError("invalid_response_assessments")
    required = {(category, qid) for category in CATEGORIES
                for qid in readiness.required_ids[category]}
    assessed, optional, issues = {}, 0, []
    for response in responses:
        if (not isinstance(response, AssessedResponse) or response.category not in CATEGORIES
                or not isinstance(response.question_id, str)
                or response.judgment not in {"meaningful_attempt", "blank", "printed_copy", "review"}
                or not _key(response.evidence_reference) or type(response.optional) is not bool):
            raise ValueError("invalid_response_assessment")
        key = response.category, response.question_id
        if response.optional:
            if key in required:
                issues.append("required_item_marked_optional")
            optional += 1
            continue
        if key not in required or key in assessed:
            issues.append("ambiguous_response_mapping")
        assessed[key] = response
    if set(assessed) != required:
        issues.append("required_response_not_assessed")
    if any(r.judgment == "review" for r in assessed.values()):
        issues.append("unreadable_or_ambiguous_response")
    if issues:
        return dict(base, issues=sorted(set(issues)))
    counts = {c: {"completed": sum(r.judgment == "meaningful_attempt"
                                   for (category, _), r in assessed.items() if category == c),
                  "required": len(readiness.required_ids[c]),
                  "due": stage == "final" or c == "NOTES"} for c in CATEGORIES}
    included = ("NOTES", "BANK") if stage == "final" else ("NOTES",)
    scores = {"content_notes": _score(sum(counts[c]["completed"] for c in included),
                                       sum(counts[c]["required"] for c in included)),
              "revision": _score(counts["DRAFT"]["completed"], counts["DRAFT"]["required"],
                                 due=stage == "final")}
    return dict(base, status="proposed", issues=[], scores=scores, counts=counts,
                optional_items_reported=optional)


def _assess_reconciled(readiness, responses, stage, reconciliation, base):
    """Resolve structure only through a complete, source-bound manual map."""
    if not isinstance(reconciliation, ManualReconciliation):
        raise ValueError("invalid_manual_reconciliation")
    record = reconciliation
    issues = []
    references = (record.readiness_evidence_reference, record.submission_revision_reference,
                  record.requirements_reference, record.stage_reference,
                  record.reviewer_reference, record.evidence_reference)
    if (any(not _key(ref) for ref in references)
            or record.readiness_evidence_reference != readiness.evidence_reference):
        issues.append("manual_reconciliation_provenance_unverified")
    if record.stage != stage:
        issues.append("manual_reconciliation_stage_mismatch")
    if record.requirements_verified is not True:
        issues.append("distributed_requirements_unverified")
    if (not isinstance(record.required_ids, dict)
            or set(record.required_ids) != set(CATEGORIES)):
        issues.append("distributed_requirements_invalid")
    else:
        for ids in record.required_ids.values():
            if (not isinstance(ids, tuple) or len(ids) > 1000
                    or any(not isinstance(q, str) or not q.strip() or len(q) > 160 for q in ids)
                    or len(set(ids)) != len(ids)):
                issues.append("distributed_requirements_invalid")
    provenance = asdict(record)
    result = dict(base, assessment_method="manually_reconciled",
                  manual_reconciliation=provenance)
    if issues:
        return dict(result, issues=sorted(set(issues)))

    # BANK order cannot be a condition for a version with no BANK, or for an
    # initial NOTES-only checkpoint. Final BANK requirements still need review.
    if stage == "final" and record.required_ids["BANK"]:
        if (record.bank_sequence_verified is not True
                or not _key(record.bank_sequence_evidence_reference)):
            issues.append("manual_bank_sequence_unverified")
    due_categories = CATEGORIES if stage == "final" else ("NOTES",)
    required = {(category, qid) for category in due_categories
                for qid in record.required_ids[category]}
    if not isinstance(responses, (tuple, list)) or len(responses) > 3000:
        raise ValueError("invalid_response_assessments")
    by_reference = {}
    for response in responses:
        if (not isinstance(response, AssessedResponse) or response.category not in CATEGORIES
                or not isinstance(response.question_id, str)
                or response.judgment not in {"meaningful_attempt", "blank", "printed_copy", "review"}
                or not _key(response.evidence_reference) or type(response.optional) is not bool):
            raise ValueError("invalid_response_assessment")
        by_reference.setdefault(response.evidence_reference, []).append(response)
    if not isinstance(record.response_mappings, tuple) or len(record.response_mappings) > 3000:
        raise ValueError("invalid_manual_response_mappings")
    assessed, used_references = {}, set()
    for mapping in record.response_mappings:
        if (not isinstance(mapping, ManualResponseMapping) or mapping.category not in CATEGORIES
                or not isinstance(mapping.question_id, str)
                or not _key(mapping.response_evidence_reference)
                or not _key(mapping.evidence_reference)):
            raise ValueError("invalid_manual_response_mapping")
        key = mapping.category, mapping.question_id
        if key not in required:
            issues.append("manual_mapping_outside_due_requirements")
            continue
        if key in assessed:
            issues.append("duplicate_manual_item_mapping")
            continue
        source = by_reference.get(mapping.response_evidence_reference, ())
        if len(source) != 1:
            issues.append("manual_response_evidence_not_unique")
            continue
        if mapping.response_evidence_reference in used_references:
            issues.append("manual_response_field_reused")
            continue
        response = source[0]
        if response.optional:
            issues.append("required_item_marked_optional")
        if response.judgment == "review":
            issues.append("unreadable_or_ambiguous_response")
        assessed[key] = response
        used_references.add(mapping.response_evidence_reference)
    if set(assessed) != required:
        issues.append("required_response_not_assessed")
    if issues:
        return dict(result, issues=sorted(set(issues)))

    gate = dict(base["readiness"], status="READY", issues=[],
                resolution="manual_mapping_for_due_checkpoint",
                original_status=base["readiness"]["status"],
                original_issues=base["readiness"]["issues"],
                requirements_reference=record.requirements_reference)
    counts = {
        c: {"completed": sum(r.judgment == "meaningful_attempt" for (category, _), r in assessed.items()
                              if category == c) if c in due_categories else None,
            "required": len(record.required_ids[c]), "due": c in due_categories}
        for c in CATEGORIES
    }
    included = ("NOTES", "BANK") if stage == "final" else ("NOTES",)
    scores = {"content_notes": _score(sum(counts[c]["completed"] for c in included),
                                       sum(counts[c]["required"] for c in included)),
              "revision": _score(counts["DRAFT"]["completed"] or 0,
                                 counts["DRAFT"]["required"], due=stage == "final")}
    return dict(result, readiness=gate, status="proposed", issues=[], scores=scores,
                counts=counts, deferred_categories=[c for c in CATEGORIES if c not in due_categories],
                optional_items_reported=sum(r.optional for r in responses),
                unmapped_response_references=sorted(set(by_reference) - used_references))


def feedback_batches(items):
    """Parts 1/2: maximum twenty; callers pass the whole set to Part 3."""
    if not isinstance(items, (tuple, list)) or len(items) > 10000:
        raise ValueError("invalid_feedback_batch")
    return tuple(tuple(items[i:i + 20]) for i in range(0, len(items), 20))
