"""Prompt 3 aggregation of assessed compiled records, never a prose assessor.

Consumes all feedback batches without requiring decks again. The host verifies
source provenance and stores results privately. No model, file or school writes.
"""
from collections import Counter
import copy

from school_workflow_hub.workflows.completion_policy import CATEGORIES, _key

LEVELS = ("Developing", "Almost there", "Proficient")
TIMINGS = ("genuine_earlier_answer", "known_later_completion", "documented_revision", "unknown")


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 20000


def _keys(values):
    if (not isinstance(values, (list, tuple)) or len(values) > 10000
            or any(not _key(v) for v in values) or len(set(values)) != len(values)):
        raise ValueError("invalid_summary_membership")
    return set(values)


def build_class_summary(records, *, class_key, assignment_key, expected_learners=None,
                        expected_batch_keys=None, contrasts=()):
    """Aggregate compiled Prompt-2 lines with explicit coverage and evidence.

    Complete coverage requires an explicit expected student set. Unknown counts
    are not zero. Contrasts are supplied by a teacher/assessor and linked to
    exact evidence from a recurring misconception; no keyword-generated advice.
    """
    if not _key(class_key) or not _key(assignment_key):
        raise ValueError("invalid_summary_scope")
    if not isinstance(records, (list, tuple)) or len(records) > 10000:
        raise ValueError("invalid_summary_records")
    expected = None if expected_learners is None else _keys(expected_learners)
    batches = None if expected_batch_keys is None else _keys(expected_batch_keys)
    seen, received_batches, prompts = set(), set(), set()
    stages, question_counts, ideas, checks = {}, {}, {}, []
    timing_counts = Counter({k: 0 for k in TIMINGS})
    comparison_counts = Counter({k: 0 for k in ("known_later_completion", "documented_revision", "genuine_earlier_answer", "unknown_timing")})
    for row in records:
        if (not isinstance(row, dict) or row.get("schema_version") != "student-feedback-compiled/v1"
                or row.get("class_key") != class_key or row.get("assignment_key") != assignment_key):
            raise ValueError("compiled_record_scope_mismatch")
        for field in ("learner_key", "batch_key", "submission_revision", "prompt_version"):
            if not _key(row.get(field)):
                raise ValueError("invalid_compiled_identity")
        learner = row["learner_key"]
        if learner in seen:
            raise ValueError("duplicate_student_across_batches")
        if expected is not None and learner not in expected:
            raise ValueError("unexpected_summary_student")
        if batches is not None and row["batch_key"] not in batches:
            raise ValueError("unexpected_summary_batch")
        if not _text(row.get("display_name")):
            raise ValueError("invalid_compiled_display_name")
        seen.add(learner)
        received_batches.add(row["batch_key"])
        prompts.add(row["prompt_version"])
        stage, timing = row.get("stage"), row.get("timing_evidence")
        if stage not in {"initial", "final"} or timing not in TIMINGS:
            raise ValueError("invalid_compiled_stage_or_timing")
        timing_counts[timing] += 1
        comparisons = row.get("comparisons", [])
        if not isinstance(comparisons, (tuple, list)):
            raise ValueError("invalid_compiled_comparisons")
        student_timing, compared = set(), set()
        for comparison in comparisons:
            if (not isinstance(comparison, dict) or not _text(comparison.get("question_id"))
                    or comparison["question_id"] in compared
                    or comparison.get("basis") not in {"genuine_earlier_answer", "known_later_completion", "unknown_timing"}
                    or comparison.get("change") not in {"revised", "unchanged", "completed_later", "not_determined"}):
                raise ValueError("invalid_compiled_comparison")
            compared.add(comparison["question_id"])
            refs = _keys(comparison.get("evidence_references"))
            basis, change = comparison["basis"], comparison["change"]
            if ((change in {"revised", "unchanged"} and basis != "genuine_earlier_answer")
                    or (change == "completed_later" and basis != "known_later_completion")
                    or (basis != "unknown_timing" and not refs)):
                raise ValueError("unsupported_compiled_change")
            student_timing.add(basis)
            if change == "revised":
                student_timing.add("documented_revision")
        for label in student_timing:
            comparison_counts[label] += 1
        group = stages.setdefault(stage, {"decks": 0, "complete": 0, "completion_unavailable": 0,
            "categories": {c: {"completed_items": 0, "required_items": 0,
                "complete_students": 0, "unavailable_students": 0,
                "status": "not_yet_due" if stage == "initial" and c != "NOTES" else "due"}
                           for c in CATEGORIES}})
        group["decks"] += 1
        counts = row.get("completion")
        if counts is None:
            group["completion_unavailable"] += 1
            for category in CATEGORIES:
                group["categories"][category]["unavailable_students"] += 1
            checks.append({"learner_key": learner, "display_name": row["display_name"],
                           "reason": "Completion counts unavailable; completion cannot be determined."})
        else:
            if not isinstance(counts, dict) or set(counts) != set(CATEGORIES):
                raise ValueError("invalid_compiled_completion")
            all_complete = True
            for category, count in counts.items():
                due = stage == "final" or category == "NOTES"
                if (not isinstance(count, dict) or type(count.get("completed")) is not int
                        or type(count.get("required")) is not int
                        or not 0 <= count["completed"] <= count["required"]
                        or count.get("due") is not due):
                    raise ValueError("invalid_compiled_completion_counts")
                bucket = group["categories"][category]
                bucket["completed_items"] += count["completed"]
                bucket["required_items"] += count["required"]
                if due and count["required"]:
                    bucket["complete_students"] += count["completed"] == count["required"]
                    all_complete = all_complete and count["completed"] == count["required"]
            due_required = sum(c["required"] for c in counts.values() if c["due"])
            group["complete"] += bool(all_complete and due_required)
        understood = row.get("understanding", [])
        if not isinstance(understood, (list, tuple)):
            raise ValueError("invalid_compiled_understanding")
        questions = set()
        for item in understood:
            if (not isinstance(item, dict) or not _text(item.get("question_id"))
                    or item.get("level") not in LEVELS or item["question_id"] in questions):
                raise ValueError("invalid_compiled_question_level")
            questions.add(item["question_id"])
            question_counts.setdefault(item["question_id"], Counter())[item["level"]] += 1
        if not understood:
            checks.append({"learner_key": learner, "display_name": row["display_name"],
                           "reason": "Current understanding unavailable."})
        misconceptions = row.get("misconceptions", [])
        if not isinstance(misconceptions, (list, tuple)):
            raise ValueError("invalid_compiled_misconceptions")
        for item in misconceptions:
            if (not isinstance(item, dict) or not _key(item.get("idea_key"))
                    or not _key(item.get("evidence_reference"))
                    or any(not _text(item.get(k)) for k in ("description", "quote", "source_text", "question_id"))
                    or item["quote"] not in item["source_text"]
                    or item.get("timing") not in {"earlier", "current", "unknown"}):
                raise ValueError("invalid_compiled_misconception_evidence")
            idea = ideas.setdefault(item["idea_key"], {"idea_key": item["idea_key"],
                "description": item["description"], "evidence": [], "learners": set()})
            if idea["description"] != item["description"]:
                raise ValueError("conflicting_misconception_definition")
            idea["learners"].add(learner)
            evidence = {k: item[k] for k in ("quote", "timing", "question_id", "evidence_reference")}
            evidence.update(learner_key=learner, display_name=row["display_name"])
            if evidence not in idea["evidence"]:
                idea["evidence"].append(evidence)
        reasons = row.get("review_reasons", [])
        if not isinstance(reasons, (list, tuple)) or any(not _text(r) for r in reasons):
            raise ValueError("invalid_compiled_review_reasons")
        for reason in reasons:
            checks.append({"learner_key": learner, "display_name": row["display_name"], "reason": reason})
    if len(prompts) > 1:
        raise ValueError("mixed_compiled_prompt_versions")
    recurring = [{"idea_key": key, "description": value["description"],
                  "student_count": len(value["learners"]), "evidence": value["evidence"]}
                 for key, value in sorted(ideas.items()) if len(value["learners"]) > 1]
    if not isinstance(contrasts, (list, tuple)):
        raise ValueError("invalid_instructional_contrasts")
    accepted, contrast_ids = [], set()
    recurrence = {r["idea_key"]: r for r in recurring}
    for contrast in contrasts:
        if (not isinstance(contrast, dict) or contrast.get("idea_key") not in recurrence
                or contrast["idea_key"] in contrast_ids
                or any(not _text(contrast.get(k)) for k in ("case_a", "case_b", "rationale"))
                or contrast["case_a"] == contrast["case_b"]):
            raise ValueError("invalid_instructional_contrast")
        refs = _keys(contrast.get("evidence_references"))
        known_refs = {e["evidence_reference"] for e in recurrence[contrast["idea_key"]]["evidence"]}
        if not refs or not refs <= known_refs:
            raise ValueError("instructional_contrast_evidence_mismatch")
        contrast_ids.add(contrast["idea_key"])
        accepted.append(copy.deepcopy(contrast))
    for stage, group in stages.items():
        for category, bucket in group["categories"].items():
            if bucket["status"] == "due" and not bucket["required_items"] and not bucket["unavailable_students"]:
                bucket["status"] = "N/A"
    missing = sorted(expected - seen) if expected is not None else []
    missing_batches = sorted(batches - received_batches) if batches is not None else []
    complete = expected is not None and not missing and not missing_batches and bool(records)
    return {"schema_version": "class-summary/v1", "class_key": class_key,
        "assignment_key": assignment_key, "prompt_version": next(iter(prompts), None),
        "status": "prepared_for_review" if complete else "partial", "coverage": {
            "state": "complete" if complete else "partial", "received": len(seen),
            "expected": len(expected) if expected is not None else None,
            "missing_learners": missing, "missing_batches": missing_batches},
        "participation": {"decks_in": len(seen), "by_stage": stages,
                          "timing": dict(timing_counts), "timing_unit": "students_by_primary_evidence",
                          "comparison_timing": dict(comparison_counts),
                          "comparison_timing_unit": "students_with_evidence_can_overlap"},
        "proficiency_by_question": {q: {**{level: c[level] for level in LEVELS},
            "unavailable": len(seen) - sum(c.values())} for q, c in sorted(question_counts.items())},
        "recurring_misconceptions": recurring, "instructional_contrasts": accepted,
        "contrast_state": "prepared_for_review" if accepted else "needs_assessor" if recurring else "no_recurring_evidence",
        "teacher_checks": checks, "execution_supported": False, "external_writes": 0}
