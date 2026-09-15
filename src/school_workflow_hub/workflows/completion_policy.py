"""Prompt-1 arithmetic over assessed evidence, not an assessor of student prose.

The authorized extraction/review layer must inspect actual writing and supply
meaningful_attempt/blank/printed_copy/review labels. Marker presence, string
length and a filled box are never used here to judge meaningful completion.
No provider, file, model, grade ledger or external system is changed.
"""
from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
import re

POLICY_VERSION = "completion-policy/2026-09-14"
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


def assess_completion(readiness, responses, *, stage):
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


def feedback_batches(items):
    """Parts 1/2: maximum twenty; callers pass the whole set to Part 3."""
    if not isinstance(items, (tuple, list)) or len(items) > 10000:
        raise ValueError("invalid_feedback_batch")
    return tuple(tuple(items[i:i + 20]) for i in range(0, len(items), 20))
