"""Deterministic synthetic feedback built on the existing student-work ledger.

Only bounded fixture identifiers, enumerated signals and counts are accepted.
Counts describe supplied synthetic evidence; they do not assess student prose.
Every output remains a teacher-review proposal. No provider or network is used.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
import re
import sqlite3
from typing import Any, Mapping, Sequence, Tuple

from school_workflow_hub.adapters.contracts import LocalSyntheticAdapter
from school_workflow_hub.core.actions import create_review_item, propose_action
from school_workflow_hub.core.models import AdapterResult, AssignmentRecord, GradeProposal, SubmissionRevision
from school_workflow_hub.core.runs import canonical_json, create_or_get_run, deterministic_id, finish_run
from school_workflow_hub.core.sources import record_source_revision
from school_workflow_hub.core.workflow import EvidenceRef, PreviewChange, WorkflowIssue, WorkflowPreview
from school_workflow_hub.workflows.student_work import (
    StudentWorkPlan, StudentWorkWorkflowRequest, plan_student_work,
)

EVIDENCE_SOURCE = "synthetic_feedback_evidence"
ASSIGNMENT_SOURCE = "synthetic_feedback_assignment"
SIGNALS = frozenset(("none", "evidence_boundary", "cause_correlation", "vocabulary_connection"))
_SLUG = r"[a-z0-9][a-z0-9_-]{0,63}"


def require_fixture_id(value: Any, kind: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(kind + r":synthetic:" + _SLUG, value):
        raise ValueError("invalid_synthetic_identifier")
    return value


def _version(value: Any, kind: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"synthetic-" + kind + r"-v[1-9][0-9]{0,3}", value):
        raise ValueError("invalid_synthetic_version")
    return value


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("invalid_timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid_timestamp") from None
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("invalid_timestamp")
    return result


def pseudonymous_learner_id(value: str) -> str:
    """Produce a stable fixture pseudonym; this is not a real-data anonymizer."""
    require_fixture_id(value, "learner")
    digest = hashlib.sha256(("synthetic-hub-v1:" + value).encode()).hexdigest()[:20]
    return "learner:synthetic:p" + digest


@dataclass(frozen=True)
class CompletionEvidence:
    notes_required: int = 2
    notes_completed: int = 0
    notes_completed_later: int = 0
    bank_required: int = 2
    bank_completed: int = 0
    bank_completed_later: int = 0
    draft_required: int = 2
    drafts_completed: int = 0
    first_and_revision_pairs: int = 0
    optional_completed: int = 0
    # None preserves legacy fixtures: unpaired first answers were not observed.
    first_answers_completed: int | None = None
    first_answers_completed_later: int = 0


@dataclass(frozen=True)
class FeedbackObservation:
    submission: SubmissionRevision
    class_stable_id: str
    source_reference: str
    rubric_version: str
    completion: CompletionEvidence = CompletionEvidence()
    work_status: str = "submitted"
    conceptual_signal: str = "none"
    contains_sensitive_content: bool = False
    synthetic_marker: str = "fixture-only"
    evidence_status: str = "complete"


@dataclass(frozen=True)
class FeedbackPipelineRequest:
    class_stable_id: str
    as_of: datetime
    prompt_version: str = "synthetic-feedback-v1"
    rubric_version: str = "synthetic-rubric-v1"
    retention_days: int = 180


@dataclass(frozen=True)
class FeedbackRecord:
    assignment: AssignmentRecord
    submission: SubmissionRevision
    source_revision_id: str
    learner_pseudonym: str
    source_reference: str
    work_status: str
    late: bool
    conceptual_signal: str
    completion: CompletionEvidence
    grade_proposal: GradeProposal
    feedback_draft: str
    prompt_version: str
    evidence: Tuple[EvidenceRef, ...]


@dataclass(frozen=True)
class FeedbackPipelineResult:
    run_id: str
    student_work_plan: StudentWorkPlan
    records: Tuple[FeedbackRecord, ...]
    preview: WorkflowPreview
    discovery_since: str
    retention_days: int
    as_of: datetime
    history: Tuple[FeedbackObservation, ...] = ()
    external_writes: int = 0
    live_model_calls: int = 0

    @property
    def grade_proposals(self) -> Tuple[GradeProposal, ...]:
        return tuple(item.grade_proposal for item in self.records)


def _validate_request(request: FeedbackPipelineRequest) -> None:
    require_fixture_id(request.class_stable_id, "class")
    if not isinstance(request.as_of, datetime) or request.as_of.tzinfo is None or request.as_of.utcoffset() is None:
        raise ValueError("invalid_timestamp")
    _version(request.prompt_version, "feedback")
    _version(request.rubric_version, "rubric")
    if type(request.retention_days) is not int or not 14 <= request.retention_days <= 3650:
        raise ValueError("invalid_retention_days")


def _validate_assignment(item: AssignmentRecord) -> None:
    if not isinstance(item, AssignmentRecord):
        raise ValueError("invalid_assignment")
    require_fixture_id(item.stable_id, "assignment")
    require_fixture_id(item.class_stable_id, "class")
    if not isinstance(item.title, str) or not re.fullmatch(r"Synthetic [A-Za-z0-9 _.-]{1,60}", item.title):
        raise ValueError("invalid_assignment")
    if item.intended_due_date is not None:
        date.fromisoformat(item.intended_due_date)
    for value, kind in ((item.deck_file_id, "deck"), (item.form_file_id, "form")):
        if value is not None:
            require_fixture_id(value, kind)
    if item.external_ids:
        raise ValueError("external_identifiers_not_allowed")


def _validate_observation(item: FeedbackObservation) -> None:
    if not isinstance(item, FeedbackObservation) or item.synthetic_marker != "fixture-only":
        raise ValueError("invalid_synthetic_fixture")
    if item.contains_sensitive_content is not False:
        raise ValueError("sensitive_content_declared")
    if not isinstance(item.submission, SubmissionRevision):
        raise ValueError("invalid_synthetic_fixture")
    require_fixture_id(item.class_stable_id, "class")
    for value, kind in ((item.submission.stable_id, "submission"), (item.submission.assignment_stable_id, "assignment"), (item.submission.learner_stable_id, "learner")):
        require_fixture_id(value, kind)
    if not isinstance(item.submission.revision_hash, str) or not re.fullmatch(r"synthetic-revision:" + _SLUG, item.submission.revision_hash):
        raise ValueError("invalid_synthetic_fixture")
    _timestamp(item.submission.observed_at)
    _version(item.rubric_version, "rubric")
    if not isinstance(item.source_reference, str) or not re.fullmatch(r"synthetic://[a-z0-9/_-]{1,100}", item.source_reference):
        raise ValueError("invalid_synthetic_fixture")
    if item.work_status not in ("submitted", "missing", "absent") or item.conceptual_signal not in SIGNALS:
        raise ValueError("invalid_synthetic_fixture")
    if item.evidence_status not in ("complete", "partial", "unreadable"):
        raise ValueError("invalid_completion_evidence")
    if not isinstance(item.completion, CompletionEvidence):
        raise ValueError("invalid_completion_evidence")
    counts = asdict(item.completion)
    first_answers = counts.pop("first_answers_completed")
    if any(type(value) is not int or not 0 <= value <= 1000 for value in counts.values()):
        raise ValueError("invalid_completion_evidence")
    evidence = item.completion
    if first_answers is None:
        if evidence.first_answers_completed_later:
            raise ValueError("invalid_completion_evidence")
    elif (type(first_answers) is not int or not
          evidence.first_and_revision_pairs <= first_answers <= evidence.draft_required or
          evidence.first_answers_completed_later > first_answers):
        raise ValueError("invalid_completion_evidence")
    if not (evidence.notes_completed_later <= evidence.notes_completed <= evidence.notes_required and evidence.bank_completed_later <= evidence.bank_completed <= evidence.bank_required and evidence.first_and_revision_pairs <= evidence.drafts_completed <= evidence.draft_required):
        raise ValueError("invalid_completion_evidence")
    if item.work_status != "submitted" and (item.conceptual_signal != "none" or first_answers or any(counts[key] for key in counts if not key.endswith("required"))):
        raise ValueError("missing_work_has_no_conceptual_evidence")


def _observation_payload(item: FeedbackObservation) -> Mapping[str, Any]:
    return asdict(item)


def _from_payload(payload: Mapping[str, Any]) -> FeedbackObservation:
    values = dict(payload)
    values["submission"] = SubmissionRevision(**values["submission"])
    values["completion"] = CompletionEvidence(**values["completion"])
    result = FeedbackObservation(**values)
    _validate_observation(result)
    return result


class _PreparedReader(LocalSyntheticAdapter):
    def __init__(self, observations: Sequence[FeedbackObservation], *, review_hold: bool = False):
        self.observations = observations
        self.review_hold = review_hold

    def changed_submissions(self, class_stable_id: str, since: str) -> AdapterResult:
        if self.review_hold:
            # The shared student-work planner has an independent retained ledger.
            # An empty filtered reader would still let it re-plan old evidence.
            # Its existing blocked-reader path prevents that without erasing any
            # history or introducing a second backlog policy here.
            return AdapterResult("blocked", reason="feedback_evidence_review_required")
        return AdapterResult("ok", {"revisions": [
            {**asdict(item.submission), "class_stable_id": item.class_stable_id,
             "work_status": item.work_status, "source_reference": item.source_reference,
             "conceptual_difficulty": None if item.conceptual_signal == "none" else item.conceptual_signal}
            for item in self.observations
        ]})


def _grade_value(item: FeedbackObservation) -> str | None:
    if item.work_status != "submitted":
        return None
    evidence = item.completion
    return canonical_json({
        "completion": {"notes": [evidence.notes_completed, evidence.notes_required], "bank": [evidence.bank_completed, evidence.bank_required], "later_notes_credited": evidence.notes_completed_later, "later_bank_credited": evidence.bank_completed_later,
                       "first_answers": None if evidence.first_answers_completed is None else [evidence.first_answers_completed, evidence.draft_required],
                       "later_first_answers_credited": evidence.first_answers_completed_later},
        "revision": {"draft": [evidence.drafts_completed, evidence.draft_required], "paired_evidence_count": evidence.first_and_revision_pairs, "growth_claim": "not_inferred"},
        "optional": "unscored", "teacher_review_required": True,
    })


def _draft(item: FeedbackObservation) -> str:
    if item.work_status != "submitted":
        return "Review attendance or submission availability. Missing evidence does not show a conceptual difficulty."
    messages = {
        "none": "Review the completed content and revision evidence separately.",
        "evidence_boundary": "What is the strongest reason your conclusion could be wrong, incomplete, or too certain? Revise using the evidence boundary you identify.",
        "cause_correlation": "Which evidence distinguishes a causal explanation from a correlation? Revise the explanation in your own words.",
        "vocabulary_connection": "How do two Concept Bank terms connect to your explanation? Revise the connection in your own words.",
    }
    return messages[item.conceptual_signal] + " Meaningful later-completed first answers, NOTES and Concept Bank fields receive completion credit separately from revised answers; earlier thinking or growth is not invented."


def _review_hold(item: FeedbackObservation, history: Sequence[FeedbackObservation]) -> str | None:
    """Withhold a new proposal, rather than turn uncertain reads into lost credit.

    Counts are cumulative snapshots, never increments to add to an older grade.
    A reduction may be legitimate, but cannot be adjudicated from counts alone.
    """
    if item.evidence_status != "complete":
        return "incomplete_completion_evidence"
    for prior in history:
        if (prior.submission.stable_id != item.submission.stable_id or
            prior.rubric_version != item.rubric_version or
            prior.evidence_status != "complete" or prior.work_status != "submitted" or
            _timestamp(prior.submission.observed_at) >= _timestamp(item.submission.observed_at)):
            continue
        if item.work_status != "submitted":
            return "submission_availability_regressed"
        before, after = prior.completion, item.completion
        if any(getattr(before, field) != getattr(after, field) for field in
               ("notes_required", "bank_required", "draft_required")):
            return "completion_structure_changed"
        if any(getattr(after, field) < getattr(before, field) for field in
               ("notes_completed", "bank_completed", "drafts_completed", "first_and_revision_pairs")):
            return "completion_evidence_regressed"
        if before.first_answers_completed is not None and (
            after.first_answers_completed is None or
            after.first_answers_completed < before.first_answers_completed
        ):
            return "completion_evidence_regressed"
    return None


def plan_feedback_pipeline(
    connection: sqlite3.Connection,
    assignments: Sequence[AssignmentRecord],
    observations: Sequence[FeedbackObservation],
    request: FeedbackPipelineRequest,
) -> FeedbackPipelineResult:
    """Crawl 14 synthetic days and queue drafts using shared revision/action IDs.

    Replays return the same current records, but no newly proposed changes.
    Invalid records become code-only exceptions without copying their values.
    Local partial runs are resumable by replaying the same inputs.
    """
    _validate_request(request)
    issues = []
    assignment_map = {}
    conflicted_assignments = set()
    for item in assignments:
        try:
            _validate_assignment(item)
        except (ValueError, TypeError):
            issues.append(WorkflowIssue("invalid_assignment", "high"))
            continue
        if item.class_stable_id != request.class_stable_id:
            issues.append(WorkflowIssue("wrong_course_assignment", "high", item.stable_id))
            continue
        if item.stable_id in assignment_map and assignment_map[item.stable_id] != item:
            conflicted_assignments.add(item.stable_id)
            issues.append(WorkflowIssue("conflicting_assignment", "high", item.stable_id))
        assignment_map[item.stable_id] = item
    for stable_id in conflicted_assignments:
        assignment_map.pop(stable_id, None)

    since = request.as_of - timedelta(days=14)
    valid = []
    for item in observations:
        try:
            _validate_observation(item)
        except (ValueError, TypeError):
            issues.append(WorkflowIssue("invalid_submission_metadata", "high"))
            continue
        key = item.submission.assignment_stable_id
        code = None
        if item.class_stable_id != request.class_stable_id:
            code = "wrong_course_submission"
        elif key not in assignment_map:
            code = "assignment_not_reconciled"
        elif item.rubric_version != request.rubric_version:
            code = "rubric_mismatch"
        if code:
            issues.append(WorkflowIssue(code, "high", key))
            continue
        if since <= _timestamp(item.submission.observed_at) <= request.as_of:
            valid.append(item)

    stored = {}
    stored_history = []
    unreadable_ids = set()
    for row in connection.execute("SELECT id, stable_object_id, payload_json FROM source_revisions WHERE source_type = ?", (EVIDENCE_SOURCE,)):
        try:
            item = _from_payload(json.loads(row["payload_json"]))
        except (ValueError, TypeError, KeyError):
            issues.append(WorkflowIssue("stored_metadata_unreadable", "high"))
            unreadable_ids.add(row["stable_object_id"])
            continue
        if item.class_stable_id == request.class_stable_id and _timestamp(item.submission.observed_at) <= request.as_of:
            stored_history.append(item)
            sid = item.submission.stable_id
            if sid not in stored or _timestamp(item.submission.observed_at) > _timestamp(stored[sid].submission.observed_at):
                stored[sid] = item

    newest = dict(stored)
    rejected = set()
    for item in sorted(valid, key=lambda value: (value.submission.observed_at, value.submission.revision_hash)):
        sid = item.submission.stable_id
        prior = newest.get(sid)
        if prior:
            identity = lambda value: (value.submission.assignment_stable_id, value.submission.learner_stable_id)
            if identity(prior) != identity(item):
                rejected.add(sid)
                issues.append(WorkflowIssue("submission_identity_conflict", "high", item.submission.assignment_stable_id))
                continue
            old_time, new_time = _timestamp(prior.submission.observed_at), _timestamp(item.submission.observed_at)
            if new_time < old_time:
                continue
            if new_time == old_time and prior != item:
                rejected.add(sid)
                issues.append(WorkflowIssue("ambiguous_submission_revision", "high", item.submission.assignment_stable_id))
                continue
        newest[sid] = item
    logical_submissions = {}
    for sid, item in newest.items():
        key = (item.submission.assignment_stable_id, item.submission.learner_stable_id)
        logical_submissions.setdefault(key, []).append(sid)
    for (assignment_key, _), ids in logical_submissions.items():
        if len(ids) > 1:
            rejected.update(ids)
            issues.append(WorkflowIssue("duplicate_assignment_submission", "high", assignment_key))
    current = []
    for sid in sorted(newest):
        item = newest[sid]
        if sid in rejected or item.submission.assignment_stable_id not in assignment_map or item.rubric_version != request.rubric_version:
            continue
        hold = "stored_metadata_unreadable" if sid in unreadable_ids else _review_hold(item, (*stored_history, *valid))
        if hold:
            issues.append(WorkflowIssue(hold, "high", item.submission.assignment_stable_id))
        else:
            current.append(item)
    current = tuple(current)
    base = plan_student_work(connection, _PreparedReader(current, review_hold=any(issue.severity == "high" for issue in issues)), StudentWorkWorkflowRequest(request.class_stable_id, request.as_of, request.prompt_version, request.rubric_version))
    run = create_or_get_run(connection, "feedback_pipeline", {"class": request.class_stable_id, "as_of": request.as_of.isoformat(), "prompt": request.prompt_version, "rubric": request.rubric_version, "retention_days": request.retention_days, "evidence": [asdict(item) for item in current], "assignments": [asdict(assignment_map[key]) for key in sorted(assignment_map)], "issues": [asdict(issue) for issue in issues]})
    assignment_sources = {key: record_source_revision(connection, ASSIGNMENT_SOURCE, key, asdict(item), "synthetic://assignments/" + key.split(":")[-1], run.id) for key, item in assignment_map.items()}
    # Retain every valid received revision as evidence, while only the newest
    # observation is eligible for a proposal. Never reactivate a stale replay.
    for item in sorted(valid, key=lambda value: _timestamp(value.submission.observed_at)):
        sid = item.submission.stable_id
        if sid in rejected or sid in stored and _timestamp(item.submission.observed_at) <= _timestamp(stored[sid].submission.observed_at):
            continue
        record_source_revision(connection, EVIDENCE_SOURCE, sid, _observation_payload(item), item.source_reference, run.id)
    records, changes = [], []
    for item in current:
        submission = item.submission
        source = record_source_revision(connection, EVIDENCE_SOURCE, submission.stable_id, _observation_payload(item), item.source_reference, run.id)
        assignment = assignment_map[submission.assignment_stable_id]
        assignment_source = assignment_sources[assignment.stable_id]
        value = _grade_value(item)
        proposal_id = deterministic_id("grade_proposal", {"source": source.id, "assignment": assignment_source.id, "prompt": request.prompt_version, "rubric": request.rubric_version})
        grade = GradeProposal(proposal_id, source.id, value, request.rubric_version)
        evidence = (EvidenceRef(EVIDENCE_SOURCE, submission.stable_id, source.id, item.source_reference), EvidenceRef(ASSIGNMENT_SOURCE, assignment.stable_id, assignment_source.id, "synthetic://assignments/" + assignment.stable_id.split(":")[-1]))
        draft = _draft(item)
        proposed = {"grade_proposal_id": grade.stable_id, "grade_proposal": value, "feedback_draft": draft, "prompt_version": request.prompt_version, "rubric_version": request.rubric_version, "delivered": False}
        action = propose_action(connection, run_id=run.id, action_type="feedback_draft_review", target_system="local_teacher_review", target_stable_id=submission.stable_id, source_stable_id=submission.stable_id, source_revision_id=source.id, expected_state={"teacher_decision": "not_recorded"}, proposed_state=proposed, evidence={"source_reference": item.source_reference, "assignment_revision": assignment_source.id}, required_permission="teacher.approval", source_dependencies={"assignment": assignment_source.id})
        if not connection.execute("SELECT 1 FROM review_items WHERE action_id = ? AND kind = 'feedback_draft_review'", (action.id,)).fetchone():
            create_review_item(connection, run_id=run.id, action_id=action.id, kind="feedback_draft_review", severity="normal", payload={"grade_proposal_id": grade.stable_id, "assignment_key": assignment.stable_id, "work_status": item.work_status, "teacher_review_required": True})
        if action.created:
            changes.append(PreviewChange(action.id, assignment.stable_id, "local_teacher_review", submission.stable_id, "feedback_and_grade_proposal", None, proposed, evidence, 1.0, True))
        late = bool(assignment.intended_due_date and _timestamp(submission.observed_at).astimezone(request.as_of.tzinfo).date() > date.fromisoformat(assignment.intended_due_date) and item.work_status == "submitted")
        records.append(FeedbackRecord(assignment, submission, source.id, pseudonymous_learner_id(submission.learner_stable_id), item.source_reference, item.work_status, late, item.conceptual_signal, item.completion, grade, draft, request.prompt_version, evidence))
        if late:
            issues.append(WorkflowIssue("late_submission", "normal", assignment.stable_id))
    # Exception queue stores only fixed codes and validated assignment keys.
    unique_issues = tuple(sorted({WorkflowIssue(issue.code, "warning" if issue.severity == "normal" else "error", issue.assignment_key, issue.retryable) for issue in issues}, key=lambda issue: (issue.code, issue.assignment_key or "")))
    for issue in unique_issues:
        create_review_item(connection, run_id=run.id, kind="feedback_pipeline_exception", severity=issue.severity, payload=asdict(issue))
    finish_run(connection, run.id, details={"record_count": len(records), "new_changes": len(changes), "issue_count": len(unique_issues)})
    history = []
    for row in connection.execute("SELECT payload_json FROM source_revisions WHERE source_type = ?", (EVIDENCE_SOURCE,)):
        try:
            item = _from_payload(json.loads(row["payload_json"]))
        except (ValueError, TypeError, KeyError):
            continue
        if item.class_stable_id == request.class_stable_id and item.submission.assignment_stable_id in assignment_map and _timestamp(item.submission.observed_at) <= request.as_of:
            history.append(item)
    return FeedbackPipelineResult(run.id, base, tuple(records), WorkflowPreview("feedback_pipeline", tuple(changes), unique_issues), since.isoformat(), request.retention_days, request.as_of, tuple(sorted(history, key=lambda item: (_timestamp(item.submission.observed_at), item.submission.stable_id))))


def load_feedback_fixture(payload: Mapping[str, Any]) -> tuple[tuple[AssignmentRecord, ...], tuple[FeedbackObservation, ...], FeedbackPipelineRequest]:
    """Load declared fixture structure; the pipeline validates all field values."""
    try:
        request_values = dict(payload["request"])
        request_values["as_of"] = _timestamp(request_values["as_of"])
        assignments = tuple(AssignmentRecord(**item) for item in payload["assignments"])
        observations = tuple(FeedbackObservation(**{**item, "submission": SubmissionRevision(**item["submission"]), "completion": CompletionEvidence(**item.get("completion", {}))}) for item in payload["observations"])
        return assignments, observations, FeedbackPipelineRequest(**request_values)
    except (KeyError, TypeError, ValueError):
        raise ValueError("invalid_feedback_fixture_structure") from None
