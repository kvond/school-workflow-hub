"""Synthetic-only field-notes, maintenance, and future-job planning wrapper.

The module returns local plans and metadata only.  It has no journal writer,
Drive, Todoist, GitHub, browser, scheduler, model, or network integration.
That separation makes a future integration prove its permissions and execution
path rather than making an apparently harmless planning call mutate anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import List, Optional, Tuple


def _require_stable_id(value: str, field_name: str) -> None:
    if not value or value != value.strip() or any(character.isspace() for character in value):
        raise ValueError("{} must be a non-empty stable ID without whitespace".format(field_name))


def _require_text(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError("{} is required".format(field_name))


@dataclass(frozen=True)
class FieldNoteIntake:
    """One dated, class-tagged teacher note with thinking fields kept separate."""

    stable_id: str
    observed_on: date
    class_stable_id: str
    observation: str
    interpretation: Optional[str] = None
    reflection: Optional[str] = None
    todoist_task_ids: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_stable_id(self.stable_id, "field note stable_id")
        _require_stable_id(self.class_stable_id, "field note class_stable_id")
        _require_text(self.observation, "observation")
        for value, field_name in (
            (self.interpretation, "interpretation"),
            (self.reflection, "reflection"),
        ):
            if value is not None and not value.strip():
                raise ValueError("{} must be non-empty when supplied".format(field_name))
        if len(set(self.todoist_task_ids)) != len(self.todoist_task_ids):
            raise ValueError("todoist_task_ids must not contain duplicates")
        for task_id in self.todoist_task_ids:
            _require_stable_id(task_id, "todoist task ID")


@dataclass(frozen=True)
class LocalJournalAppendPlan:
    """An append-only local journal *plan*, not a journal write operation."""

    plan_stable_id: str
    field_note_stable_id: str
    observed_on: date
    class_stable_id: str
    observation: str
    interpretation: Optional[str]
    reflection: Optional[str]
    preserved_todoist_task_ids: Tuple[str, ...]
    operation: str = "append_only_local_journal_plan"
    lifecycle_state: str = "plan_only"
    drive_write_performed: bool = False
    todoist_write_performed: bool = False


class LifecycleReminderKind(str, Enum):
    MID_NOVEMBER_REVIEW = "mid_november_review"
    MID_AUGUST_RESTART = "mid_august_restart"
    FRIDAY_MEETING = "friday_meeting"


@dataclass(frozen=True)
class FakeClock:
    """Caller-supplied date used to make lifecycle planning deterministic."""

    local_date: date
    timezone: str = "America/New_York"


@dataclass(frozen=True)
class LifecycleReminderPlan:
    stable_id: str
    kind: LifecycleReminderKind
    planned_for: date
    reason: str
    lifecycle_state: str = "plan_only"
    scheduled_automation_created: bool = False


@dataclass(frozen=True)
class MaintenanceIssue:
    code: str
    detail: str


@dataclass(frozen=True)
class GitHubReportSnapshot:
    """A previously supplied, read-only report snapshot; never a live GitHub read."""

    report_stable_id: str
    repository_stable_id: str
    observed_on: date
    source_revision_id: str
    display_title: str

    def __post_init__(self) -> None:
        _require_stable_id(self.report_stable_id, "github report_stable_id")
        _require_stable_id(self.repository_stable_id, "github repository_stable_id")
        _require_stable_id(self.source_revision_id, "github source_revision_id")
        _require_text(self.display_title, "github display_title")


@dataclass(frozen=True)
class ProposedGitHubArchive:
    """A reviewable archive candidate; deletion is neither planned nor possible."""

    proposal_stable_id: str
    report_stable_id: str
    repository_stable_id: str
    source_revision_id: str
    age_days: int
    action: str = "propose_archive_only"
    lifecycle_state: str = "plan_only"
    deletion_performed: bool = False
    github_write_performed: bool = False


@dataclass(frozen=True)
class FutureJobMetadata:
    """Implementation checklist for a future job, with no scheduling side effect."""

    job_stable_id: str
    host_requirement: str
    account_scope: str
    permission_requirements: Tuple[str, ...]
    persistence_requirement: str
    cost_boundary: str
    offline_behavior: str
    execution_state: str = "not_scheduled"

    def __post_init__(self) -> None:
        _require_stable_id(self.job_stable_id, "future job_stable_id")
        for value, field_name in (
            (self.host_requirement, "host_requirement"),
            (self.account_scope, "account_scope"),
            (self.persistence_requirement, "persistence_requirement"),
            (self.cost_boundary, "cost_boundary"),
            (self.offline_behavior, "offline_behavior"),
        ):
            _require_text(value, field_name)
        if not self.permission_requirements:
            raise ValueError("permission_requirements are required")
        for permission in self.permission_requirements:
            _require_text(permission, "permission requirement")


@dataclass(frozen=True)
class FieldNotesMaintenanceInput:
    field_notes: Tuple[FieldNoteIntake, ...]
    fake_clock: FakeClock
    friday_meeting_confirmed: bool
    github_report_snapshots: Tuple[GitHubReportSnapshot, ...]
    future_jobs: Tuple[FutureJobMetadata, ...]
    stale_after_days: int = 30

    def __post_init__(self) -> None:
        if self.stale_after_days < 1:
            raise ValueError("stale_after_days must be positive")
        note_ids = [note.stable_id for note in self.field_notes]
        if len(set(note_ids)) != len(note_ids):
            raise ValueError("field_notes must not repeat a stable ID")
        report_ids = [snapshot.report_stable_id for snapshot in self.github_report_snapshots]
        if len(set(report_ids)) != len(report_ids):
            raise ValueError("github_report_snapshots must not repeat a report stable ID")
        job_ids = [job.job_stable_id for job in self.future_jobs]
        if len(set(job_ids)) != len(job_ids):
            raise ValueError("future_jobs must not repeat a job stable ID")


@dataclass(frozen=True)
class FieldNotesMaintenancePlanResult:
    journal_append_plans: Tuple[LocalJournalAppendPlan, ...]
    lifecycle_reminder_plans: Tuple[LifecycleReminderPlan, ...]
    maintenance_issues: Tuple[MaintenanceIssue, ...]
    proposed_github_archives: Tuple[ProposedGitHubArchive, ...]
    future_job_metadata: Tuple[FutureJobMetadata, ...]
    external_calls_made: int = 0
    external_writes_performed: int = 0
    scheduled_automation_created: bool = False
    live_model_calls: int = 0


def plan_field_notes_maintenance(
    workflow_input: FieldNotesMaintenanceInput,
) -> FieldNotesMaintenancePlanResult:
    """Build deterministic local plans only from supplied synthetic records."""

    append_plans = tuple(
        _journal_append_plan(note)
        for note in sorted(workflow_input.field_notes, key=lambda item: (item.observed_on, item.stable_id))
    )
    lifecycle_reminders, lifecycle_issues = _plan_lifecycle_reminders(
        workflow_input.fake_clock, workflow_input.friday_meeting_confirmed
    )
    archive_plans = tuple(
        _archive_plan(snapshot, workflow_input.fake_clock.local_date)
        for snapshot in sorted(workflow_input.github_report_snapshots, key=lambda item: item.report_stable_id)
        if _age_days(snapshot.observed_on, workflow_input.fake_clock.local_date)
        >= workflow_input.stale_after_days
    )
    return FieldNotesMaintenancePlanResult(
        journal_append_plans=append_plans,
        lifecycle_reminder_plans=lifecycle_reminders,
        maintenance_issues=lifecycle_issues,
        proposed_github_archives=archive_plans,
        future_job_metadata=workflow_input.future_jobs,
    )


def _journal_append_plan(note: FieldNoteIntake) -> LocalJournalAppendPlan:
    return LocalJournalAppendPlan(
        plan_stable_id="journal-plan:{}:{}".format(note.stable_id, note.observed_on.isoformat()),
        field_note_stable_id=note.stable_id,
        observed_on=note.observed_on,
        class_stable_id=note.class_stable_id,
        observation=note.observation,
        interpretation=note.interpretation,
        reflection=note.reflection,
        preserved_todoist_task_ids=note.todoist_task_ids,
    )


def _plan_lifecycle_reminders(
    clock: FakeClock, friday_meeting_confirmed: bool
) -> Tuple[Tuple[LifecycleReminderPlan, ...], Tuple[MaintenanceIssue, ...]]:
    reminders: List[LifecycleReminderPlan] = []
    issues: List[MaintenanceIssue] = []
    today = clock.local_date

    if (today.month, today.day) == (11, 15):
        reminders.append(
            LifecycleReminderPlan(
                stable_id="lifecycle:mid-november-review:{}".format(today.isoformat()),
                kind=LifecycleReminderKind.MID_NOVEMBER_REVIEW,
                planned_for=today,
                reason="Mid-November review is due on the fake clock.",
            )
        )
    if (today.month, today.day) == (8, 15):
        reminders.append(
            LifecycleReminderPlan(
                stable_id="lifecycle:mid-august-restart:{}".format(today.isoformat()),
                kind=LifecycleReminderKind.MID_AUGUST_RESTART,
                planned_for=today,
                reason="Mid-August restart is due on the fake clock.",
            )
        )
    if friday_meeting_confirmed and today.weekday() == 4:
        reminders.append(
            LifecycleReminderPlan(
                stable_id="lifecycle:friday-meeting:{}".format(today.isoformat()),
                kind=LifecycleReminderKind.FRIDAY_MEETING,
                planned_for=today,
                reason="A Friday meeting is confirmed for this fake-clock date.",
            )
        )
    elif friday_meeting_confirmed:
        issues.append(
            MaintenanceIssue(
                code="friday_meeting_not_on_friday",
                detail="A Friday-meeting reminder was requested for a non-Friday fake-clock date; no reminder was planned.",
            )
        )
    return tuple(reminders), tuple(issues)


def _age_days(observed_on: date, reference_date: date) -> int:
    """A future-dated snapshot is not stale and produces no archive candidate."""

    return max(0, (reference_date - observed_on).days)


def _archive_plan(snapshot: GitHubReportSnapshot, reference_date: date) -> ProposedGitHubArchive:
    return ProposedGitHubArchive(
        proposal_stable_id="github-archive-plan:{}:{}".format(
            snapshot.report_stable_id, snapshot.source_revision_id
        ),
        report_stable_id=snapshot.report_stable_id,
        repository_stable_id=snapshot.repository_stable_id,
        source_revision_id=snapshot.source_revision_id,
        age_days=_age_days(snapshot.observed_on, reference_date),
    )


def synthetic_field_notes_maintenance_example() -> FieldNotesMaintenanceInput:
    """Return a tiny fake dataset; no real note, task, report, or job is read."""

    return FieldNotesMaintenanceInput(
        field_notes=(
            FieldNoteIntake(
                stable_id="field-note:synthetic:001",
                observed_on=date(2026, 11, 15),
                class_stable_id="class:synthetic:biology-a4",
                observation="Several learners paused before comparing two synthetic examples.",
                interpretation="The comparison step may need a clearer transition.",
                reflection="Try a one-minute partner comparison before the whole-class share.",
                todoist_task_ids=("todoist:synthetic:task-001",),
            ),
        ),
        fake_clock=FakeClock(date(2026, 11, 15)),
        friday_meeting_confirmed=False,
        github_report_snapshots=(
            GitHubReportSnapshot(
                report_stable_id="github-report:synthetic:old",
                repository_stable_id="github-repo:synthetic:hub",
                observed_on=date(2026, 9, 1),
                source_revision_id="github-report-source:synthetic:old:v1",
                display_title="Synthetic old report",
            ),
        ),
        future_jobs=(
            FutureJobMetadata(
                job_stable_id="job:synthetic:monthly-maintenance",
                host_requirement="teacher-selected local host",
                account_scope="separately authenticated school account reference",
                permission_requirements=("external.read",),
                persistence_requirement="requires explicit, durable scheduler approval",
                cost_boundary="no paid provider call without a capped, explicit approval",
                offline_behavior="skip external work and create a local review item",
            ),
        ),
    )


def run_synthetic_field_notes_maintenance_example() -> FieldNotesMaintenancePlanResult:
    """Run the fake input through the pure local planner."""

    return plan_field_notes_maintenance(synthetic_field_notes_maintenance_example())
