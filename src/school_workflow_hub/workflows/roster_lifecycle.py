"""Review-only lifecycle planning for the weekly roster-sync workflow.

This is intentionally not a scheduler and has no activation, pause, browser,
or roster-reading capability.  It gives the teacher a stable, local plan for
the two already-decided review dates: pause on 2026-11-15 and reconsider the
preserved workflow on 2027-08-15 in America/New_York.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo


ROSTER_TIMEZONE = "America/New_York"
PAUSE_REVIEW_AT = datetime(2026, 11, 15, 9, 0, tzinfo=ZoneInfo(ROSTER_TIMEZONE))
RESTART_REVIEW_AT = datetime(2027, 8, 15, 9, 0, tzinfo=ZoneInfo(ROSTER_TIMEZONE))


@dataclass(frozen=True)
class RosterLifecycleDecision:
    status: str
    review_kind: str
    due_at: str
    workflow_stable_id: str
    activation_performed: bool
    external_writes: int
    instruction: str


def plan_roster_lifecycle(
    *,
    observed_at: datetime,
    workflow_stable_id: str,
) -> RosterLifecycleDecision:
    """Return the next human review without changing any schedule state."""

    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must include a timezone offset")
    if not isinstance(workflow_stable_id, str) or not workflow_stable_id.strip():
        raise ValueError("workflow_stable_id is required")
    if "*" in workflow_stable_id or "?" in workflow_stable_id:
        raise ValueError("workflow_stable_id must be exact")
    local_time = observed_at.astimezone(ZoneInfo(ROSTER_TIMEZONE))
    if local_time < PAUSE_REVIEW_AT:
        return RosterLifecycleDecision(
            status="review_scheduled",
            review_kind="pause_weekly_roster_sync",
            due_at=PAUSE_REVIEW_AT.isoformat(),
            workflow_stable_id=workflow_stable_id,
            activation_performed=False,
            external_writes=0,
            instruction="At the review date, inspect roster stability and decide manually whether to pause.",
        )
    if local_time < RESTART_REVIEW_AT:
        return RosterLifecycleDecision(
            status="review_due",
            review_kind="pause_weekly_roster_sync",
            due_at=PAUSE_REVIEW_AT.isoformat(),
            workflow_stable_id=workflow_stable_id,
            activation_performed=False,
            external_writes=0,
            instruction="Roster pause requires a current human decision; this planner did not change a schedule.",
        )
    return RosterLifecycleDecision(
        status="review_due",
        review_kind="restart_weekly_roster_sync",
        due_at=RESTART_REVIEW_AT.isoformat(),
        workflow_stable_id=workflow_stable_id,
        activation_performed=False,
        external_writes=0,
        instruction="Before the new school year, inspect access and roster rules, then decide manually whether to restart.",
    )
