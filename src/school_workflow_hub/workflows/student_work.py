"""Synthetic-only planning for student work, feedback, and grade review.

This workflow reads a class-scoped adapter, stores only revision metadata and
references in the local SQLite ledger, and creates local teacher-review plans.
It never calls a model, changes a grade, creates a folder, or writes to a
school system.  Stable IDs are required throughout; display names are not part
of the contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import sqlite3
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from school_workflow_hub.adapters.contracts import StudentWorkReader, read_synthetic_student_work
from school_workflow_hub.core.actions import create_review_item, propose_action
from school_workflow_hub.core.database import transaction
from school_workflow_hub.core.models import (
    AdapterResult,
    CumulativeSummary,
    FeedbackRunRequest,
    GradeProposal,
    SubmissionRevision,
)
from school_workflow_hub.core.runs import (
    canonical_json,
    create_or_get_run,
    deterministic_id,
    finish_run,
    record_exception,
)
from school_workflow_hub.core.sources import record_source_revision


WORK_STATUSES = frozenset(("submitted", "missing", "absent"))
GRADE_REVIEW_ACTION = "grade_proposal_review"
STUDENT_WORK_SOURCE_TYPE = "student_work_submission"


class StudentWorkWorkflowError(ValueError):
    """Raised when an adapter result cannot safely enter the local ledger."""


@dataclass(frozen=True)
class StudentWorkWorkflowRequest:
    """A local plan request; identifiers name versions, not live services."""

    class_stable_id: str
    as_of: datetime
    feedback_prompt_version: str
    rubric_version: str
    model_identifier: str = "fake/disabled"
    discovery_window_days: int = 14


@dataclass(frozen=True)
class StudentWorkDiscovery:
    """One source revision needing a local teacher-review plan."""

    source_revision_id: str
    submission: SubmissionRevision
    work_status: str
    conceptual_difficulty: Optional[str]
    original_response_reference: Optional[str]
    source_reference: str
    filing_key: str
    retained_from_backlog: bool


@dataclass(frozen=True)
class StudentWorkPlan:
    """A result containing plans only, never feedback text or a grade change."""

    run_id: str
    run_created: bool
    status: str
    discovery_since: str
    discoveries: Tuple[StudentWorkDiscovery, ...]
    feedback_requests: Tuple[FeedbackRunRequest, ...]
    grade_proposals: Tuple[GradeProposal, ...]
    proposed_action_ids: Tuple[str, ...]
    review_item_ids: Tuple[str, ...]
    cumulative_summaries: Tuple[CumulativeSummary, ...]
    external_writes: int = 0
    live_model_calls: int = 0


@dataclass(frozen=True)
class _Observation:
    """Normalized synthetic submission metadata held in a source revision."""

    class_stable_id: str
    submission: SubmissionRevision
    work_status: str
    conceptual_difficulty: Optional[str]
    original_response_reference: Optional[str]
    source_reference: str


def _require_nonempty(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise StudentWorkWorkflowError("{0} is required".format(label))


def _validate_request(request: StudentWorkWorkflowRequest) -> None:
    _require_nonempty(request.class_stable_id, "class_stable_id")
    _require_nonempty(request.feedback_prompt_version, "feedback_prompt_version")
    _require_nonempty(request.rubric_version, "rubric_version")
    _require_nonempty(request.model_identifier, "model_identifier")
    if request.as_of.tzinfo is None or request.as_of.utcoffset() is None:
        raise StudentWorkWorkflowError("as_of must be timezone-aware")
    if request.discovery_window_days != 14:
        raise StudentWorkWorkflowError("this foundation fixes the discovery window at 14 days")


def _parse_aware_timestamp(value: str, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise StudentWorkWorkflowError("{0} must be a nonempty timestamp".format(label))
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise StudentWorkWorkflowError("{0} is not an ISO-8601 timestamp".format(label)) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StudentWorkWorkflowError("{0} must include a timezone offset".format(label))
    return parsed


def _as_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StudentWorkWorkflowError("{0} must be a nonempty string".format(label))
    return value


def _observation_from_mapping(
    payload: Mapping[str, Any],
    *,
    requested_class_id: str,
    default_source_reference: str,
) -> _Observation:
    """Validate a reader item before it is made durable.

    A real adapter may put submission metadata under ``submission_revision``;
    the supplied fake adapter returns the same fields at the top level.  Both
    representations are intentionally accepted, but class mismatches and
    ambiguous statuses fail closed.
    """

    revision_payload = payload.get("submission_revision", payload)
    if not isinstance(revision_payload, Mapping):
        raise StudentWorkWorkflowError("submission_revision must be an object")
    submission = SubmissionRevision(
        stable_id=_as_string(revision_payload.get("stable_id"), "submission stable_id"),
        assignment_stable_id=_as_string(
            revision_payload.get("assignment_stable_id"), "assignment_stable_id"
        ),
        learner_stable_id=_as_string(
            revision_payload.get("learner_stable_id"), "learner_stable_id"
        ),
        revision_hash=_as_string(revision_payload.get("revision_hash"), "revision_hash"),
        observed_at=_as_string(revision_payload.get("observed_at"), "observed_at"),
    )
    _parse_aware_timestamp(submission.observed_at, "observed_at")

    class_stable_id = payload.get("class_stable_id", requested_class_id)
    if class_stable_id != requested_class_id:
        raise StudentWorkWorkflowError("reader returned a submission for a different class")

    work_status = payload.get("work_status", payload.get("submission_status", "submitted"))
    if work_status not in WORK_STATUSES:
        raise StudentWorkWorkflowError(
            "work_status must be one of submitted, missing, or absent"
        )
    conceptual_difficulty = payload.get(
        "conceptual_difficulty", payload.get("conceptual_signal")
    )
    if conceptual_difficulty is not None and not isinstance(conceptual_difficulty, str):
        raise StudentWorkWorkflowError("conceptual_difficulty must be a string when provided")
    if work_status in ("missing", "absent") and conceptual_difficulty:
        raise StudentWorkWorkflowError(
            "missing or absent work cannot be classified as conceptual difficulty"
        )

    original_response_reference = payload.get("original_response_reference")
    if original_response_reference is not None and not isinstance(original_response_reference, str):
        raise StudentWorkWorkflowError("original_response_reference must be a string when provided")
    source_reference = payload.get("source_reference", default_source_reference)
    _require_nonempty(source_reference, "source_reference")
    return _Observation(
        class_stable_id=class_stable_id,
        submission=submission,
        work_status=work_status,
        conceptual_difficulty=conceptual_difficulty or None,
        original_response_reference=original_response_reference,
        source_reference=source_reference,
    )


def _source_payload(observation: _Observation) -> Dict[str, Any]:
    submission = observation.submission
    return {
        "class_stable_id": observation.class_stable_id,
        "submission_revision": {
            "stable_id": submission.stable_id,
            "assignment_stable_id": submission.assignment_stable_id,
            "learner_stable_id": submission.learner_stable_id,
            "revision_hash": submission.revision_hash,
            "observed_at": submission.observed_at,
        },
        "work_status": observation.work_status,
        "conceptual_difficulty": observation.conceptual_difficulty,
        "original_response_reference": observation.original_response_reference,
    }


def _observation_from_source_row(
    row: sqlite3.Row, requested_class_id: str
) -> Optional[_Observation]:
    try:
        payload = json.loads(row["payload_json"])
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise StudentWorkWorkflowError("stored submission source payload is unreadable") from error
    if not isinstance(payload, Mapping):
        raise StudentWorkWorkflowError("stored submission source payload must be an object")
    # A single local ledger may contain multiple classes.  Stored records from a
    # different class are deliberately not read into this class's plan.
    if payload.get("class_stable_id") != requested_class_id:
        return None
    return _observation_from_mapping(
        payload,
        requested_class_id=requested_class_id,
        default_source_reference=row["source_reference"],
    )


def _feedback_contract(request: StudentWorkWorkflowRequest) -> Dict[str, str]:
    return {
        "feedback_prompt_version": request.feedback_prompt_version,
        "rubric_version": request.rubric_version,
        "model_identifier": request.model_identifier,
    }


def _has_matching_plan(
    connection: sqlite3.Connection,
    source_revision_id: str,
    feedback_contract: Mapping[str, str],
) -> bool:
    rows = connection.execute(
        "SELECT proposed_state_json FROM proposed_actions "
        "WHERE source_revision_id = ? AND action_type = ? AND state <> 'superseded'",
        (source_revision_id, GRADE_REVIEW_ACTION),
    ).fetchall()
    for row in rows:
        try:
            proposed_state = json.loads(row["proposed_state_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        if proposed_state.get("feedback_contract") == dict(feedback_contract):
            return True
    return False


def _pending_retained_sources(
    connection: sqlite3.Connection,
    request: StudentWorkWorkflowRequest,
) -> Iterable[Tuple[str, _Observation]]:
    """Yield active, unplanned source revisions even after they age out.

    This is the local recovery path: a crash after discovery leaves a durable
    source revision but no matching proposal, so a later run can re-plan it.
    """

    contract = _feedback_contract(request)
    rows = connection.execute(
        "SELECT id, source_reference, payload_json FROM source_revisions "
        "WHERE source_type = ? AND active = 1 ORDER BY observed_at, id",
        (STUDENT_WORK_SOURCE_TYPE,),
    ).fetchall()
    for row in rows:
        observation = _observation_from_source_row(row, request.class_stable_id)
        if observation is None:
            continue
        if _has_matching_plan(connection, row["id"], contract):
            continue
        yield row["id"], observation


def _review_kind(observation: _Observation) -> str:
    if observation.work_status == "missing":
        return "missing_work"
    if observation.work_status == "absent":
        return "absent_work"
    if observation.conceptual_difficulty:
        return "conceptual_difficulty_review"
    return "feedback_revision_ready"


def _supersede_prior_contract_plans(
    connection: sqlite3.Connection,
    *,
    source_revision_id: str,
    keep_action_id: str,
    feedback_contract: Mapping[str, str],
) -> None:
    """Keep older feedback-contract plans traceable, but out of the active queue."""

    rows = connection.execute(
        "SELECT id, proposed_state_json FROM proposed_actions "
        "WHERE source_revision_id = ? AND action_type = ? AND state = 'proposed'",
        (source_revision_id, GRADE_REVIEW_ACTION),
    ).fetchall()
    stale_ids: List[str] = []
    for row in rows:
        if row["id"] == keep_action_id:
            continue
        try:
            proposed_state = json.loads(row["proposed_state_json"])
        except (TypeError, json.JSONDecodeError):
            proposed_state = {}
        if proposed_state.get("feedback_contract") != dict(feedback_contract):
            stale_ids.append(row["id"])
    if not stale_ids:
        return
    with transaction(connection):
        for action_id in stale_ids:
            connection.execute(
                "UPDATE proposed_actions SET state = 'superseded', review_status = 'needs_review', "
                "verification_json = ? WHERE id = ?",
                (
                    canonical_json(
                        {
                            "reason": "feedback_contract_changed",
                            "replacement_action_id": keep_action_id,
                        }
                    ),
                    action_id,
                ),
            )


def _cumulative_summaries(
    discoveries: Sequence[StudentWorkDiscovery],
    since: datetime,
    as_of: datetime,
) -> Tuple[CumulativeSummary, ...]:
    counts: Dict[str, int] = {}
    statuses: Dict[str, str] = {}
    for discovery in discoveries:
        learner_id = discovery.submission.learner_stable_id
        counts[learner_id] = counts.get(learner_id, 0) + 1
        if discovery.work_status in ("missing", "absent"):
            statuses[learner_id] = "needs_attendance_or_completion_review"
        elif discovery.conceptual_difficulty:
            statuses.setdefault(learner_id, "needs_conceptual_review")
        else:
            statuses.setdefault(learner_id, "feedback_planned")
    return tuple(
        CumulativeSummary(
            learner_stable_id=learner_id,
            window_start=since.isoformat(timespec="seconds"),
            window_end=as_of.isoformat(timespec="seconds"),
            evidence_count=counts[learner_id],
            status=statuses[learner_id],
        )
        for learner_id in sorted(counts)
    )


def _plan_one_discovery(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    source_revision_id: str,
    observation: _Observation,
    retained_from_backlog: bool,
    request: StudentWorkWorkflowRequest,
) -> Tuple[StudentWorkDiscovery, FeedbackRunRequest, GradeProposal, str, str]:
    """Create only local review records for one already-recorded source revision."""

    submission = observation.submission
    contract = _feedback_contract(request)
    filing_key = "classes/{0}/students/{1}".format(
        observation.class_stable_id, submission.learner_stable_id
    )
    grade_proposal = GradeProposal(
        stable_id=deterministic_id(
            "grade_proposal",
            {
                "source_revision_id": source_revision_id,
                "rubric_version": request.rubric_version,
                "feedback_prompt_version": request.feedback_prompt_version,
            },
        ),
        submission_revision_id=source_revision_id,
        proposed_value=None,
        rubric_version=request.rubric_version,
        status="proposed",
    )
    action = propose_action(
        connection,
        run_id=run_id,
        action_type=GRADE_REVIEW_ACTION,
        target_system="local_teacher_review",
        target_stable_id="{0}:{1}:{2}".format(
            observation.class_stable_id,
            submission.assignment_stable_id,
            submission.learner_stable_id,
        ),
        source_stable_id=submission.stable_id,
        source_revision_id=source_revision_id,
        expected_state={"teacher_decision": "not_recorded"},
        proposed_state={
            "grade_proposal_id": grade_proposal.stable_id,
            "proposed_value": None,
            "grade_change": False,
            "feedback_contract": contract,
            "work_status": observation.work_status,
        },
        evidence={
            "submission_revision_hash": submission.revision_hash,
            "submission_observed_at": submission.observed_at,
            "source_reference": observation.source_reference,
            "original_response_reference": observation.original_response_reference,
            "conceptual_difficulty": observation.conceptual_difficulty,
            "filing_key": filing_key,
        },
        required_permission="teacher.approval",
    )
    _supersede_prior_contract_plans(
        connection,
        source_revision_id=source_revision_id,
        keep_action_id=action.id,
        feedback_contract=contract,
    )
    review_item_id = create_review_item(
        connection,
        run_id=run_id,
        action_id=action.id,
        kind=_review_kind(observation),
        severity="normal",
        payload={
            "submission_revision_id": source_revision_id,
            "submission_stable_id": submission.stable_id,
            "work_status": observation.work_status,
            "conceptual_difficulty": observation.conceptual_difficulty,
            "feedback_contract": contract,
            "grade_change": False,
            "requires_teacher_decision": True,
            "retained_from_backlog": retained_from_backlog,
        },
    )
    discovery = StudentWorkDiscovery(
        source_revision_id=source_revision_id,
        submission=submission,
        work_status=observation.work_status,
        conceptual_difficulty=observation.conceptual_difficulty,
        original_response_reference=observation.original_response_reference,
        source_reference=observation.source_reference,
        filing_key=filing_key,
        retained_from_backlog=retained_from_backlog,
    )
    feedback_request = FeedbackRunRequest(
        submission_revision_id=source_revision_id,
        prompt_version=request.feedback_prompt_version,
        rubric_version=request.rubric_version,
        model_identifier=request.model_identifier,
    )
    return discovery, feedback_request, grade_proposal, action.id, review_item_id


def plan_student_work(
    connection: sqlite3.Connection,
    reader: StudentWorkReader,
    request: StudentWorkWorkflowRequest,
) -> StudentWorkPlan:
    """Discover up to 14 days of changes and re-plan any stored backlog.

    The adapter is read-only by contract.  The only writes here are local,
    transactional SQLite records.  ``FeedbackRunRequest`` records are plans;
    this function does not pass them to an AI provider.
    """

    _validate_request(request)
    since = request.as_of - timedelta(days=request.discovery_window_days)
    inputs = {
        "class_stable_id": request.class_stable_id,
        "as_of": request.as_of.isoformat(timespec="seconds"),
        "feedback_contract": _feedback_contract(request),
        "discovery_window_days": request.discovery_window_days,
    }
    run = create_or_get_run(connection, "student_work_plan", inputs)
    try:
        adapter_result: AdapterResult = read_synthetic_student_work(
            reader, request.class_stable_id, since.isoformat(timespec="seconds")
        )
        if adapter_result.status != "ok":
            review_item_id = create_review_item(
                connection,
                run_id=run.id,
                kind="student_work_discovery_unavailable",
                severity="high",
                payload={
                    "class_stable_id": request.class_stable_id,
                    "adapter_status": adapter_result.status,
                    "reason": adapter_result.reason,
                    "since": since.isoformat(timespec="seconds"),
                    "external_writes": 0,
                    "live_model_calls": 0,
                },
            )
            finish_run(
                connection,
                run.id,
                status="blocked",
                details={
                    "adapter_status": adapter_result.status,
                    "review_item_id": review_item_id,
                    "external_writes": 0,
                    "live_model_calls": 0,
                },
            )
            return StudentWorkPlan(
                run_id=run.id,
                run_created=run.created,
                status="blocked",
                discovery_since=since.isoformat(timespec="seconds"),
                discoveries=(),
                feedback_requests=(),
                grade_proposals=(),
                proposed_action_ids=(),
                review_item_ids=(review_item_id,),
                cumulative_summaries=(),
            )

        payload = adapter_result.data or {}
        revisions = payload.get("revisions")
        if not isinstance(revisions, list):
            raise StudentWorkWorkflowError("adapter result must contain a revisions list")

        # A reader may return more than one revision for a submission in one
        # response.  Plan only its newest unambiguous observation; recording an
        # older revision first would briefly make it active and could produce a
        # stale proposed action after the newer revision supersedes it.
        newest_incoming: Dict[str, Tuple[datetime, _Observation]] = {}
        for raw_item in revisions:
            if not isinstance(raw_item, Mapping):
                raise StudentWorkWorkflowError("each revision must be an object")
            observation = _observation_from_mapping(
                raw_item,
                requested_class_id=request.class_stable_id,
                default_source_reference="synthetic://student-work/{0}".format(
                    raw_item.get("stable_id", "unknown")
                ),
            )
            observed_at = _parse_aware_timestamp(observation.submission.observed_at, "observed_at")
            # The adapter is asked for a 14-day window, and the workflow checks
            # it again locally so a buggy reader cannot broaden discovery.
            if observed_at < since or observed_at > request.as_of:
                continue
            current = newest_incoming.get(observation.submission.stable_id)
            if current is not None:
                current_time, current_observation = current
                if observed_at < current_time:
                    continue
                if observed_at == current_time:
                    if _source_payload(current_observation) != _source_payload(observation):
                        raise StudentWorkWorkflowError(
                            "multiple distinct revisions for one submission have the same observed_at"
                        )
                    continue
            newest_incoming[observation.submission.stable_id] = (observed_at, observation)

        recorded_current: Dict[str, _Observation] = {}
        for stable_id in sorted(newest_incoming):
            observation = newest_incoming[stable_id][1]
            source = record_source_revision(
                connection,
                STUDENT_WORK_SOURCE_TYPE,
                observation.submission.stable_id,
                _source_payload(observation),
                observation.source_reference,
                run.id,
            )
            recorded_current[source.id] = observation

        candidates: Dict[str, Tuple[_Observation, bool]] = {}
        for source_revision_id, observation in recorded_current.items():
            candidates[source_revision_id] = (observation, False)
        for source_revision_id, observation in _pending_retained_sources(connection, request):
            candidates.setdefault(source_revision_id, (observation, True))

        discoveries: List[StudentWorkDiscovery] = []
        feedback_requests: List[FeedbackRunRequest] = []
        grade_proposals: List[GradeProposal] = []
        action_ids: List[str] = []
        review_item_ids: List[str] = []
        for source_revision_id in sorted(candidates):
            observation, retained = candidates[source_revision_id]
            if _has_matching_plan(connection, source_revision_id, _feedback_contract(request)):
                continue
            discovery, feedback_request, grade_proposal, action_id, review_item_id = _plan_one_discovery(
                connection,
                run_id=run.id,
                source_revision_id=source_revision_id,
                observation=observation,
                retained_from_backlog=retained,
                request=request,
            )
            discoveries.append(discovery)
            feedback_requests.append(feedback_request)
            grade_proposals.append(grade_proposal)
            action_ids.append(action_id)
            review_item_ids.append(review_item_id)

        summaries = _cumulative_summaries(discoveries, since, request.as_of)
        finish_run(
            connection,
            run.id,
            details={
                "class_stable_id": request.class_stable_id,
                "discovery_since": since.isoformat(timespec="seconds"),
                "new_or_retained_plans": len(discoveries),
                "retained_backlog_plans": sum(
                    1 for discovery in discoveries if discovery.retained_from_backlog
                ),
                "external_writes": 0,
                "live_model_calls": 0,
            },
        )
        return StudentWorkPlan(
            run_id=run.id,
            run_created=run.created,
            status="completed",
            discovery_since=since.isoformat(timespec="seconds"),
            discoveries=tuple(discoveries),
            feedback_requests=tuple(feedback_requests),
            grade_proposals=tuple(grade_proposals),
            proposed_action_ids=tuple(action_ids),
            review_item_ids=tuple(review_item_ids),
            cumulative_summaries=summaries,
        )
    except BaseException as error:
        record_exception(connection, run.id, error)
        raise


def make_synthetic_student_work_example() -> Tuple[StudentWorkReader, StudentWorkWorkflowRequest]:
    """Return a tiny fake reader and a deterministic, offline plan request."""

    # Imported here so normal workflow use depends only on the reader contract.
    from school_workflow_hub.adapters.fakes import FakeStudentWorkAdapter

    fake_reader = FakeStudentWorkAdapter(
        (
            SubmissionRevision(
                stable_id="submission:synthetic-student-001",
                assignment_stable_id="biology-a4:energy-intro",
                learner_stable_id="learner:synthetic-001",
                revision_hash="synthetic-revision-v2",
                observed_at="2026-09-12T15:30:00-04:00",
            ),
        )
    )
    request = StudentWorkWorkflowRequest(
        class_stable_id="biology-a4",
        as_of=datetime.fromisoformat("2026-09-13T08:00:00-04:00"),
        feedback_prompt_version="synthetic-feedback-v1",
        rubric_version="synthetic-rubric-v1",
        model_identifier="fake/disabled",
    )
    return fake_reader, request
