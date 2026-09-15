"""Due assignment -> complete folder scan -> revision-bound completion proposals.

Host injects authenticated read adapters and an assessor of actual writing.
No web endpoint, scheduler, model, grade write, email or second data store.
All returned learner-level payloads belong in approved private runtime storage.
"""
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from school_workflow_hub.core.models import AssignmentRecord, EnrollmentRecord, SubmissionRevision
from school_workflow_hub.core.runs import deterministic_id
from school_workflow_hub.workflows.completion_policy import (
    POLICY_VERSION, ReadinessAssessment, assess_completion, check_readiness,
)

VERSION = "due-completion/v1"


@dataclass(frozen=True)
class DueAssignment:
    assignment: AssignmentRecord
    folder_id: str
    mapping_revision: str
    ic_due_date: str
    stage: str
    readiness: ReadinessAssessment
    # Explicit IC targets for content_notes/revision; never infer from '(F)'.
    grade_targets: dict = field(default_factory=dict)
    timezone_name: str = "America/New_York"


@dataclass(frozen=True)
class FolderSubmission:
    revision: SubmissionRevision
    submitted_at: str  # Verified turn-in event, not Drive modifiedTime.
    evidence_reference: str  # Private resolver to the exact document revision.


@dataclass(frozen=True)
class FolderScan:
    folder_id: str
    assignment_id: str
    snapshot_revision: str
    observed_at: str
    submissions: tuple
    complete_history: bool  # All pages since assignment release, not 14-day delta.
    roster: tuple  # Current IC assigned students, not everyone on a GB sheet.
    roster_complete: bool
    # Approved individual due dates, e.g. extensions/new enrollment accommodations.
    effective_due_dates: dict = field(default_factory=dict)
    unresolved_items: tuple = ()


@dataclass(frozen=True)
class WorkAssessment:
    submission_id: str
    revision_hash: str
    readiness: ReadinessAssessment
    responses: tuple


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("timezone_required")
    return result


def boundaries(value, zone_name):
    """Date-only due dates and grace cutoffs include the whole local day."""
    zone = ZoneInfo(zone_name)
    if len(value) == 10:
        day = date.fromisoformat(value)
        return (datetime.combine(day, time.max, zone),
                datetime.combine(day + timedelta(days=14), time.max, zone))
    due = timestamp(value).astimezone(zone)
    return due, due + timedelta(days=14)


def _hold(scope, issue):
    return {"assignment_id": scope.assignment.stable_id, "status": "needs_you",
            "issues": [issue], "rows": [], "external_writes": 0}


def process_assignment(scope, *, as_of, scan_folder, assess_work):
    """Read one due assignment; never interpret access failure as missing work.

    scan_folder(scope, as_of) must authenticate, read every folder page and the
    current IC assigned roster, resolve stable student/assignment identities,
    detect ambiguous files and attest complete cumulative history. assess_work
    reads actual revision-bound writing using the shared Prompt 1. These are
    integration boundaries, not permission to substitute synthetic providers.
    """
    if not isinstance(scope, DueAssignment) or not isinstance(scope.assignment, AssignmentRecord):
        raise ValueError("invalid_assignment_scope")
    now = timestamp(as_of)
    if scope.stage not in {"initial", "final"}:
        return _hold(scope, "assessment_stage_unverified")
    agenda_due = scope.assignment.intended_due_date
    if not agenda_due or not scope.ic_due_date:
        return _hold(scope, "due_date_unverified")
    try:
        due, _ = boundaries(agenda_due, scope.timezone_name)
        ic_due, _ = boundaries(scope.ic_due_date, scope.timezone_name)
    except (ValueError, TypeError, KeyError):
        return _hold(scope, "due_date_invalid")
    if due != ic_due:
        return _hold(scope, "agenda_ic_due_mismatch")
    if now < due:
        return {"assignment_id": scope.assignment.stable_id, "status": "not_due",
                "rows": [], "external_writes": 0}
    if not scope.folder_id or not scope.mapping_revision:
        return _hold(scope, "folder_mapping_unverified")
    if check_readiness(scope.readiness)["status"] != "READY":
        return _hold(scope, "lesson_not_ready")
    try:
        scan = scan_folder(scope, as_of)
    except Exception:
        return _hold(scope, "submission_source_unavailable")
    if (not isinstance(scan, FolderScan) or scan.folder_id != scope.folder_id
            or scan.assignment_id != scope.assignment.stable_id
            or not scan.snapshot_revision or scan.complete_history is not True
            or scan.roster_complete is not True or scan.unresolved_items):
        return _hold(scope, "incomplete_or_ambiguous_scan")
    try:
        observed = timestamp(scan.observed_at)
        if observed > now or now - observed > timedelta(minutes=15):
            return _hold(scope, "stale_or_future_scan")
        members = {}
        for member in scan.roster:
            if (not isinstance(member, EnrollmentRecord)
                    or member.class_stable_id != scope.assignment.class_stable_id
                    or not member.learner_stable_id or member.learner_stable_id in members):
                return _hold(scope, "roster_identity_mismatch")
            members[member.learner_stable_id] = member
        if set(scan.effective_due_dates) - set(members):
            return _hold(scope, "extension_scope_mismatch")
        if len(scan.submissions) > 5000 or len(members) > 500:
            return _hold(scope, "scan_limit")
        grouped, owners = {}, {}
        for fact in scan.submissions:
            if not isinstance(fact, FolderSubmission) or not isinstance(fact.revision, SubmissionRevision):
                return _hold(scope, "submission_record_invalid")
            rev = fact.revision
            if (rev.assignment_stable_id != scope.assignment.stable_id
                    or rev.learner_stable_id not in members or not rev.stable_id
                    or not rev.revision_hash or not fact.evidence_reference):
                return _hold(scope, "submission_identity_mismatch")
            if rev.stable_id in owners and owners[rev.stable_id] != rev.learner_stable_id:
                return _hold(scope, "submission_owner_conflict")
            owners[rev.stable_id] = rev.learner_stable_id
            if not timestamp(fact.submitted_at) <= timestamp(rev.observed_at) <= observed:
                return _hold(scope, "submission_time_invalid")
            grouped.setdefault(rev.learner_stable_id, []).append(fact)
        rows = []
        for learner in sorted(members):
            learner_due, learner_cutoff = boundaries(
                scan.effective_due_dates.get(learner, agenda_due), scope.timezone_name)
            row = {"learner_id": learner, "status": "needs_you", "scores": None,
                   "issues": [], "cutoff_at": learner_cutoff.isoformat()}
            facts = grouped.get(learner, [])
            if observed < learner_due:
                row["status"] = "not_due"
            elif not facts:
                row["status"] = "missing_unscored"
                if observed > learner_cutoff:
                    row.update(status="zero_proposed", scores={},
                               parent_email="needs_contact_and_review")
                    categories = ("NOTES", "BANK") if scope.stage == "final" else ("NOTES",)
                    if any(scope.readiness.required_ids[c] for c in categories):
                        row["scores"]["content_notes"] = 0
                    if scope.stage == "final" and scope.readiness.required_ids["DRAFT"]:
                        row["scores"]["revision"] = 0
                    if not row["scores"]:
                        row.update(status="not_applicable", scores=None, parent_email="not_needed")
            elif len({f.revision.stable_id for f in facts}) != 1:
                row["issues"] = ["multiple_documents_need_review"]
            else:
                latest_time = max(timestamp(f.revision.observed_at) for f in facts)
                latest = [f for f in facts if timestamp(f.revision.observed_at) == latest_time]
                if any(f != latest[0] for f in latest):
                    row["issues"] = ["ambiguous_latest_revision"]
                elif min(timestamp(f.submitted_at) for f in facts) > learner_cutoff:
                    row["issues"] = ["submitted_after_cutoff_needs_review"]
                else:
                    fact = latest[0]
                    row["evidence_reference"] = fact.evidence_reference
                    row["submission_revision"] = fact.revision.revision_hash
                    row["submission_id"] = fact.revision.stable_id
                    try:
                        assessment = assess_work(scope, fact)
                        if (not isinstance(assessment, WorkAssessment)
                                or assessment.submission_id != fact.revision.stable_id
                                or assessment.revision_hash != fact.revision.revision_hash
                                or assessment.readiness != scope.readiness):
                            raise ValueError("assessment_revision_mismatch")
                        result = assess_completion(assessment.readiness, assessment.responses,
                                                   stage=scope.stage)
                        row.update(status=result["status"], completion=result,
                                   issues=result["issues"], scores=None if result["scores"] is None else {
                                       k: v["value"] for k, v in result["scores"].items()
                                       if v["value"] is not None})
                    except Exception:
                        row["issues"] = ["work_unreadable_or_assessment_unverified"]
            row["ic_targets"] = {k: scope.grade_targets.get(k) for k in (row["scores"] or {})}
            if any(not v for v in row["ic_targets"].values()):
                row["issues"].append("ic_score_target_unverified")
            row["post_enabled"] = False
            rows.append(row)
    except (ValueError, TypeError, AttributeError, KeyError):
        return _hold(scope, "invalid_scan_or_deadline")
    receipt = deterministic_id("completion-check", {
        "version": VERSION, "policy": POLICY_VERSION, "scope": asdict(scope),
        "scan": asdict(scan), "rows": rows})
    return {"assignment_id": scope.assignment.stable_id, "status": "review_ready",
            "receipt_id": receipt, "checked_at": scan.observed_at, "rows": rows,
            "external_writes": 0, "run_enabled": False}


def check_due_assignments(assignments, *, as_of, scan_folder, assess_work):
    """Host passes all unfinished assignments, including overdue beyond 14 days.

    Do not filter submissions by modified date: early turn-ins still count.
    Complete source inventory and shared execution claim are host preconditions.
    """
    if not isinstance(assignments, (tuple, list)) or len(assignments) > 500:
        raise ValueError("assignment_limit")
    keys = [s.assignment.stable_id for s in assignments]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate_assignment")
    return {"schema_version": VERSION, "checked_at": as_of, "external_writes": 0,
            "assignments": [process_assignment(s, as_of=as_of, scan_folder=scan_folder,
                                               assess_work=assess_work) for s in assignments]}
