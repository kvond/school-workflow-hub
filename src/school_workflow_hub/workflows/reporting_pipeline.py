"""Morning, Friday and biweekly previews from the feedback pipeline records."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
import re
from typing import Tuple

from school_workflow_hub.core.runs import deterministic_id
from school_workflow_hub.core.workflow import WorkflowIssue, WorkflowPreview, validate_preview
from school_workflow_hub.workflows.feedback_pipeline import FeedbackPipelineResult, pseudonymous_learner_id, require_fixture_id


@dataclass(frozen=True)
class FolderPlan:
    assignment_key: str
    learner_pseudonym: str
    folder_key: str
    source_revision_ids: Tuple[str, ...]
    operation: str = "preview_only"


@dataclass(frozen=True)
class LearnerReport:
    learner_pseudonym: str
    assignment_keys: Tuple[str, ...]
    submitted_count: int
    missing_count: int
    absent_count: int
    late_count: int
    notes_completed: int
    bank_completed: int
    drafts_completed: int
    paired_revision_evidence: int
    source_revision_ids: Tuple[str, ...]
    conceptual_signals: Tuple[str, ...]
    conversation_prompts: Tuple[str, ...]
    notable_connections: Tuple[str, ...]
    evidence_status: str


@dataclass(frozen=True)
class EvidenceChange:
    learner_pseudonym: str
    assignment_key: str
    prior_revision: str
    current_revision: str
    prior_observed_at: str
    current_observed_at: str
    notes_change: int
    bank_change: int
    draft_change: int
    prior_signal: str
    current_signal: str
    source_references: Tuple[str, ...]
    interpretation: str = "evidence_counts_changed_learning_growth_not_inferred"


@dataclass(frozen=True)
class ReportArtifact:
    stable_id: str
    kind: str
    window_start: str
    window_end: str
    learner_reports: Tuple[LearnerReport, ...]
    common_signal_counts: Tuple[Tuple[str, int], ...] = ()
    evidence_changes: Tuple[EvidenceChange, ...] = ()
    change_evidence_status: str = "not_requested"
    delivery_state: str = "not_delivered"


@dataclass(frozen=True)
class ReportingPipelineResult:
    morning_summary: ReportArtifact
    folder_plans: Tuple[FolderPlan, ...]
    friday_report: ReportArtifact
    biweekly_report: ReportArtifact
    exception_codes: Tuple[str, ...]
    preview: WorkflowPreview
    friday_meeting_date: str
    friday_meeting_class_ids: Tuple[str, ...]
    friday_meeting_status: str
    external_writes: int = 0
    live_model_calls: int = 0


def _reports(records) -> Tuple[LearnerReport, ...]:
    groups = {}
    # Pipeline records are latest revisions, but protect against duplicate
    # invocation records passed by a caller before counting work.
    unique = {(item.assignment.stable_id, item.learner_pseudonym): item for item in sorted(records, key=lambda item: (item.submission.observed_at, item.submission.revision_hash))}
    for item in unique.values():
        groups.setdefault(item.learner_pseudonym, []).append(item)
    return tuple(LearnerReport(
        learner, tuple(sorted(item.assignment.stable_id for item in items)),
        sum(item.work_status == "submitted" for item in items),
        sum(item.work_status == "missing" for item in items),
        sum(item.work_status == "absent" for item in items),
        sum(item.late for item in items),
        sum(item.completion.notes_completed for item in items),
        sum(item.completion.bank_completed for item in items),
        sum(item.completion.drafts_completed for item in items),
        sum(item.completion.first_and_revision_pairs for item in items),
        tuple(sorted(item.source_revision_id for item in items)),
        tuple(sorted({item.conceptual_signal for item in items if item.conceptual_signal != "none"})),
        tuple(sorted({item.feedback_draft for item in items if item.work_status == "submitted" and item.conceptual_signal != "none"})),
        tuple("Review whether {} recurs across these distinct assignments; the repeated label is a discussion lead.".format(signal) for signal in sorted({item.conceptual_signal for item in items if item.conceptual_signal != "none" and sum(other.conceptual_signal == item.conceptual_signal for other in items) >= 2})),
        "conceptual_evidence_supplied" if any(item.conceptual_signal != "none" for item in items) else "conceptual_evidence_unavailable",
    ) for learner, items in sorted(groups.items()))


def plan_reporting_pipeline(result: FeedbackPipelineResult, *, friday_class_ids: Tuple[str, ...] | None = None) -> ReportingPipelineResult:
    """Preview all reports now; this function schedules and delivers nothing.

    Morning summary includes the current crawl and durable backlog. Friday and
    biweekly reports use respectively 7 and 14 calendar days ending at as_of.
    Date filtering concerns observed revisions, not inferred learning growth.
    """
    def artifact(kind, days):
        start = result.as_of - timedelta(days=days)
        records = result.records if kind == "morning_summary" else tuple(item for item in result.records if start <= _observed(item) <= result.as_of)
        payload = {"kind": kind, "start": start.isoformat(), "end": result.as_of.isoformat(), "reports": [asdict(item) for item in _reports(records)]}
        signals = tuple((signal, len({item.learner_pseudonym for item in records if item.conceptual_signal == signal})) for signal in sorted({item.conceptual_signal for item in records if item.conceptual_signal != "none"}))
        changes = _evidence_changes(result, start) if kind == "biweekly_report" else ()
        status = "comparison_available" if changes else "prior_revision_unavailable" if kind == "biweekly_report" else "not_requested"
        payload.update({"signals": signals, "changes": [asdict(item) for item in changes], "change_evidence_status": status})
        return ReportArtifact(deterministic_id("report", payload), kind, start.isoformat(), result.as_of.isoformat(), _reports(records), signals, changes, status)

    morning, friday, biweekly = artifact("morning_summary", 14), artifact("friday_report", 7), artifact("biweekly_report", 14)
    folders = tuple(FolderPlan(item.assignment.stable_id, item.learner_pseudonym, "classes/{}/learners/{}/assignments/{}".format(item.assignment.class_stable_id, item.learner_pseudonym, item.assignment.stable_id), (item.source_revision_id,)) for item in result.records)
    issues = list(result.preview.issues)
    meeting_status = "unverified" if friday_class_ids is None else "configured"
    if friday_class_ids is None:
        issues.append(WorkflowIssue("friday_class_meetings_unverified", "warning"))
    for class_id in friday_class_ids or ():
        require_fixture_id(class_id, "class")
    meeting_ids = tuple(sorted(set(friday_class_ids or ())))
    friday_date = (result.as_of.date() + timedelta(days=(4 - result.as_of.weekday()) % 7)).isoformat()
    artifacts = tuple(asdict(item) for item in (morning, friday, biweekly)) + tuple(asdict(item) for item in folders) + ({"kind": "friday_meetings", "date": friday_date, "class_ids": meeting_ids, "status": meeting_status},)
    return ReportingPipelineResult(morning, folders, friday, biweekly, tuple(sorted({issue.code for issue in issues})), WorkflowPreview("reporting_pipeline", issues=tuple(issues), artifacts=artifacts), friday_date, meeting_ids, meeting_status)


def _evidence_changes(result, start):
    groups = {}
    for item in result.history:
        if _observed(item) <= result.as_of:
            groups.setdefault(item.submission.stable_id, []).append(item)
    changes = []
    for items in groups.values():
        ordered = sorted(items, key=_observed)
        if len(ordered) < 2:
            continue
        prior, current = ordered[-2:]
        if _observed(current) < start or current.work_status != "submitted":
            continue
        changes.append(EvidenceChange(pseudonymous_learner_id(current.submission.learner_stable_id), current.submission.assignment_stable_id, prior.submission.revision_hash, current.submission.revision_hash, prior.submission.observed_at, current.submission.observed_at, current.completion.notes_completed - prior.completion.notes_completed, current.completion.bank_completed - prior.completion.bank_completed, current.completion.drafts_completed - prior.completion.drafts_completed, prior.conceptual_signal, current.conceptual_signal, tuple(sorted({prior.source_reference, current.source_reference}))))
    return tuple(sorted(changes, key=lambda item: (item.learner_pseudonym, item.assignment_key)))


def _observed(item):
    from datetime import datetime
    return datetime.fromisoformat(item.submission.observed_at.replace("Z", "+00:00"))


_DISCUSSION_QUESTIONS = {
    "evidence_boundary": "What is the strongest reason your conclusion could be wrong, incomplete, or too certain?",
    "cause_correlation": "Which evidence distinguishes a causal explanation from a correlation?",
    "vocabulary_connection": "How do two Concept Bank terms connect to your explanation?",
}
_REPORT_ISSUE_CODES = frozenset({
    "invalid_assignment", "wrong_course_assignment", "conflicting_assignment",
    "invalid_submission_metadata", "wrong_course_submission", "assignment_not_reconciled",
    "rubric_mismatch", "stored_metadata_unreadable", "submission_identity_conflict",
    "ambiguous_submission_revision", "duplicate_assignment_submission", "late_submission",
    "friday_class_meetings_unverified",
})


def render_report_packet(preview: WorkflowPreview) -> str:
    """Render printer-ready Markdown without printing, sending or writing files.

    Only bounded synthetic metadata is interpolated. Questions and interpretive
    text are reconstructed from enumerated signals; arbitrary supplied prose is
    not copied into the packet. Invalid input errors never include raw values.
    """
    try:
        return _render_report_packet(preview)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("invalid_synthetic_report_packet") from None


def _render_report_packet(preview: WorkflowPreview) -> str:
    validate_preview(preview)
    if preview.workflow != "reporting_pipeline" or preview.changes:
        raise ValueError("not_a_report_preview")

    def bounded(value, pattern):
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise ValueError("invalid_report_metadata")
        return value

    def pseudo(value):
        return bounded(value, r"learner:synthetic:p[0-9a-f]{20}")

    def revision(value):
        return bounded(value, r"src_[0-9a-f]{24}")

    def observed(value):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("invalid_report_metadata")
        return parsed.isoformat()

    def count(value, signed=False):
        if type(value) is not int or not (-1000000 if signed else 0) <= value <= 1000000:
            raise ValueError("invalid_report_metadata")
        return value

    def signals(values):
        if any(value not in _DISCUSSION_QUESTIONS for value in values):
            raise ValueError("invalid_report_metadata")
        return sorted(set(values))

    titles = {"morning_summary": "Morning summary", "friday_report": "Friday report", "biweekly_report": "Biweekly report"}
    reports, folders, meetings = {}, [], []
    for artifact in preview.artifacts:
        kind = artifact.get("kind")
        if kind in titles:
            if kind in reports:
                raise ValueError("duplicate_report")
            reports[kind] = artifact
        elif kind == "friday_meetings":
            meetings.append(artifact)
        elif artifact.get("operation") == "preview_only" and "folder_key" in artifact:
            folders.append(artifact)
        else:
            raise ValueError("unknown_report_artifact")
    if set(reports) != set(titles) or len(meetings) != 1:
        raise ValueError("incomplete_report_packet")

    lines = ["# Synthetic report packet", "", "Teacher review preview. Delivery is disabled. Nothing has been sent, printed, copied, or entered into a gradebook.", "", "Observations below are synthetic evidence counts. Discussion prompts are draft interpretations, not diagnoses or findings of learning growth."]
    for kind, title in titles.items():
        artifact = reports[kind]
        report_id = bounded(artifact["stable_id"], r"report_[0-9a-f]{24}")
        if artifact["delivery_state"] != "not_delivered":
            raise ValueError("delivery_must_be_disabled")
        start, end = observed(artifact["window_start"]), observed(artifact["window_end"])
        lines.extend(["", "## " + title, "", "Window: {} through {}. Report: `{}`. Delivery: disabled.".format(start, end, report_id)])
        if kind == "morning_summary":
            lines.extend(["", "Includes retained unfinished work from before the discovery window when present."])
        common = artifact["common_signal_counts"]
        lines.extend(["", "Common evidence signals (distinct pseudonymous learners):", ""])
        for signal, total in sorted(common):
            signals((signal,))
            lines.append("- `{}`: {}.".format(signal, count(total)))
        if not common:
            lines.append("- No conceptual signal evidence supplied.")
        for learner in sorted(artifact["learner_reports"], key=lambda item: item["learner_pseudonym"]):
            learner_id = pseudo(learner["learner_pseudonym"])
            assignments = sorted(require_fixture_id(key, "assignment") for key in learner["assignment_keys"])
            sources = sorted(revision(value) for value in learner["source_revision_ids"])
            signal_values = signals(learner["conceptual_signals"])
            lines.extend(["", "### " + learner_id, "", "Observation: {} submitted; {} missing; {} absent; {} late.".format(*(count(learner[key]) for key in ("submitted_count", "missing_count", "absent_count", "late_count"))), "", "Completion: NOTES {}; Concept Bank {}. Revision: DRAFT {}; paired first/revision evidence {}.".format(*(count(learner[key]) for key in ("notes_completed", "bank_completed", "drafts_completed", "paired_revision_evidence"))), "", "Assignments: " + ", ".join("`{}`".format(key) for key in assignments) + ".", "", "Exact source revisions: " + ", ".join("`{}`".format(value) for value in sources) + ".", "", "Draft interpretation and conversation prompts:"])
            lines.append("")
            for signal in signal_values:
                lines.append("- {} Revise in your own words.".format(_DISCUSSION_QUESTIONS[signal]))
            if signal_values and len(assignments) >= 2:
                lines.append("- Look for a connection across the listed assignments. A repeated signal is a discussion lead; it does not establish a persistent misconception.")
            if not signal_values:
                lines.append("- Conceptual evidence is unavailable. Check completion or attendance before drawing an inference.")
        changes = artifact["evidence_changes"]
        if kind == "biweekly_report":
            lines.extend(["", "### Saved revision comparisons", ""])
            if not changes:
                lines.append("Prior revision evidence is unavailable; no change-over-time claim can be made.")
            for change in sorted(changes, key=lambda item: (item["learner_pseudonym"], item["assignment_key"])):
                learner_id = pseudo(change["learner_pseudonym"])
                assignment = require_fixture_id(change["assignment_key"], "assignment")
                prior = bounded(change["prior_revision"], r"synthetic-revision:[a-z0-9][a-z0-9_-]{0,63}")
                current = bounded(change["current_revision"], r"synthetic-revision:[a-z0-9][a-z0-9_-]{0,63}")
                before, after = observed(change["prior_observed_at"]), observed(change["current_observed_at"])
                refs = sorted(bounded(value, r"synthetic://[a-z0-9/_-]{1,100}") for value in change["source_references"])
                lines.extend(["", "- `{}` / `{}`: `{}` ({}) → `{}` ({}).".format(learner_id, assignment, prior, before, current, after), "  Observed count change: NOTES {:+d}; Concept Bank {:+d}; DRAFT {:+d}.".format(*(count(change[key], True) for key in ("notes_change", "bank_change", "draft_change"))), "  Evidence: " + ", ".join("`{}`".format(value) for value in refs) + ".", "  Draft interpretation: counts changed; learning growth is not inferred."])
    meeting = meetings[0]
    meeting_date = date.fromisoformat(meeting["date"]).isoformat()
    if meeting["status"] not in {"configured", "unverified"}:
        raise ValueError("invalid_meeting_status")
    class_ids = sorted(require_fixture_id(value, "class") for value in meeting["class_ids"])
    lines.extend(["", "## Friday meeting check", "", "Date: {}. Mapping: {}.".format(meeting_date, meeting["status"]), "", "Classes: " + (", ".join("`{}`".format(value) for value in class_ids) if class_ids else "No verified class mapping supplied") + ".", "", "## Folder routing preview", ""])
    for folder in sorted(folders, key=lambda item: (item["assignment_key"], item["learner_pseudonym"])):
        assignment = require_fixture_id(folder["assignment_key"], "assignment")
        learner = pseudo(folder["learner_pseudonym"])
        sources = sorted(revision(value) for value in folder["source_revision_ids"])
        lines.append("- `{}` / `{}`. Evidence: {}. Folder creation is disabled.".format(assignment, learner, ", ".join("`{}`".format(value) for value in sources)))
    if not folders:
        lines.append("No folder candidates.")
    lines.extend(["", "## Exceptions", ""])
    for issue in sorted(preview.issues, key=lambda item: (item.code, item.assignment_key or "")):
        code = issue.code if issue.code in _REPORT_ISSUE_CODES else "unrecognized_issue_code"
        key = require_fixture_id(issue.assignment_key, "assignment") if issue.assignment_key else "unassigned"
        lines.append("- {}: `{}` / `{}`. Teacher review required.".format(issue.severity, code, key))
    if not preview.issues:
        lines.append("No exceptions recorded in this synthetic packet.")
    lines.extend(["", "Later meaningful NOTES and Concept Bank completion earns completion credit. DRAFT remains separate; optional work is unscored. No real grade or student text is included.", ""])
    return "\n".join(lines)
