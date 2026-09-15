"""Join one lesson's completion, feedback batches and whole-class summary.

A read model for the existing Agenda dashboard, not a new executor or store.
No source file is read and no grade, email, print or research action is taken.
"""
import copy
from .class_summary import build_class_summary
from .learning_progress import build_learning_progress
from ..adapters.teaching_results import CAPABILITIES, SCHEMA, validate_result, validate_scope


def build_teaching_results(*, class_key, class_stable_id, assignment_key, day,
                           agenda_checked_at, checked_at, source_revision,
                           provenance, completion_result, feedback_batches=(),
                           compiled_records=(), expected_submitted_learners=None,
                           expected_batch_keys=None, contrasts=(), learning_observations=(), missing_feedback=()):
    """Use all Prompt 2 batches, or externally compiled Prompt 2 records.

    class_key is the UI course key; class_stable_id is the source's actual class
    identity. expected_submitted_learners names the known submitted set, not a
    fabricated list of completed students. Missing students remain in the
    completion result with explicit unscored/zero-proposed states.
    """
    validate_scope(day, class_key, agenda_checked_at)
    if (not isinstance(completion_result, dict)
            or completion_result.get("assignment_id") != assignment_key
            or completion_result.get("external_writes") != 0
            or not isinstance(completion_result.get("rows"), list)):
        raise ValueError("completion_result_scope_mismatch")
    if feedback_batches and compiled_records:
        raise ValueError("use_batches_or_compiled_records")
    reports, records = [], list(copy.deepcopy(compiled_records))
    for batch in feedback_batches:
        if (batch.get("schema_version") != "student-feedback-batch/v1"
                or batch.get("external_writes") != 0
                or not 1 <= len(batch.get("reports", ())) <= 20
                or len(batch["reports"]) != len(batch.get("compiled_records", ()))):
            raise ValueError("invalid_feedback_batch")
        reports.extend(copy.deepcopy(batch["reports"]))
        records.extend(copy.deepcopy(batch["compiled_records"]))
    rows = copy.deepcopy(completion_result["rows"])
    by_learner = {}
    for row in rows:
        if not row.get("learner_id") or row["learner_id"] in by_learner or row.get("post_enabled") is not False:
            raise ValueError("invalid_completion_row")
        by_learner[row["learner_id"]] = row
    identities = {}
    for record in records:
        if record.get("class_key") != class_stable_id or record.get("assignment_key") != assignment_key:
            raise ValueError("feedback_scope_mismatch")
        learner = record.get("learner_key")
        if learner not in by_learner or learner in identities:
            raise ValueError("feedback_completion_identity_mismatch")
        row = by_learner[learner]
        # A completion proposal tied to other writing cannot share a current report.
        if row.get("submission_revision") != record.get("submission_revision"):
            raise ValueError("feedback_completion_revision_mismatch")
        assessment = row.get("completion")
        if assessment and (assessment.get("stage") != record.get("stage") or
                           assessment.get("counts") != record.get("completion")):
            raise ValueError("completion_feedback_assessment_conflict")
        identities[learner] = record
        row["display_name"] = record["display_name"]
    for report in reports:
        record = identities.get(report.get("learner_key"))
        if record is None or any(report.get(k) != record.get(k) for k in (
                "class_key", "assignment_key", "submission_revision", "display_name", "prompt_version", "stage")):
            raise ValueError("report_compiled_record_mismatch")
    summary = build_class_summary(records, class_key=class_stable_id, assignment_key=assignment_key,
                                  expected_learners=expected_submitted_learners,
                                  expected_batch_keys=expected_batch_keys, contrasts=contrasts)
    progress = build_learning_progress(learning_observations, class_key=class_stable_id, provenance=provenance)
    for series in progress["series"]:
        series["display_name"] = identities.get(series["learner_key"], {}).get("display_name", "Student name unavailable")
    for report in reports:
        report["learning_progress"] = {**progress,
            "series": [s for s in progress["series"] if s["learner_key"] == report["learner_key"]],
            "distributions": []}
    summary["learning_progress"] = progress
    for report in missing_feedback:
        row = by_learner.get(report.get("learner_key"))
        if (report.get("report_version") != "missing-work/v1" or row is None
                or row.get("status") not in {"missing_unscored", "zero_proposed"}
                or report.get("class_key") != class_stable_id
                or report.get("assignment_key") != assignment_key
                or report.get("checked_at") != checked_at
                or report.get("grade_change") is not False
                or report.get("external_writes") != 0):
            raise ValueError("missing_feedback_scope_mismatch")
        if any(r.get("learner_key") == report["learner_key"] for r in reports):
            raise ValueError("duplicate_missing_feedback")
        row["display_name"] = report["display_name"]
        reports.append(copy.deepcopy(report))
    summary["missing_feedback_count"] = len(missing_feedback)
    result = {"schema_version": SCHEMA, "date": day, "class_key": class_key,
              "assignment_key": assignment_key, "class_stable_id": class_stable_id, "source_revision": source_revision,
              "agenda_checked_at": agenda_checked_at, "checked_at": checked_at,
              "provenance": provenance,
              "completion": {**copy.deepcopy(completion_result), "rows": rows},
              "feedback": {"reports": reports, "compiled_records": records},
              "summary": summary, "capabilities": dict(CAPABILITIES), "external_writes": 0}
    return validate_result(result, day=day, class_key=class_key,
                           agenda_checked_at=agenda_checked_at, allow_synthetic=True)
