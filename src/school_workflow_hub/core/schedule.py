"""Deterministic, fake-clock schedule and cutoff evaluation."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from .models import ScheduleDecision


def school_time(day: date, local_time: str, timezone_name: str) -> datetime:
    try:
        parsed = time.fromisoformat(local_time)
    except ValueError as error:
        raise ValueError(f"invalid local time: {local_time}") from error
    return datetime.combine(day, parsed, tzinfo=ZoneInfo(timezone_name))


def evaluate_cutoff(
    *,
    job_time: datetime,
    dependency_cutoff: datetime,
    dependency_required: bool,
) -> ScheduleDecision:
    if job_time.tzinfo is None or dependency_cutoff.tzinfo is None:
        raise ValueError("job and cutoff times must be timezone-aware")
    local_time = job_time.isoformat()
    if dependency_required and dependency_cutoff >= job_time:
        return ScheduleDecision(
            "blocked",
            "required dependency arrives at or after the job; use an addendum or later refresh",
            local_time,
        )
    return ScheduleDecision("ready", "declared cutoff precedes job", local_time)


def preview_job_schedule(config, job_key: str, *, as_of: datetime,
                         no_school_dates=(), completed_jobs=()) -> dict:
    """Inspect a disabled job definition without registering or invoking it."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must include a timezone")
    local = as_of.astimezone(ZoneInfo(config.timezone))
    job = config.schedules.get(job_key)
    if not isinstance(job, dict):
        raise ValueError("unknown job definition")
    if job.get("enabled", False) is not False:
        raise ValueError("live job activation is disabled")
    planned = school_time(local.date(), job["local_time"], config.timezone)
    reason = "due_preview"
    if local.date().isoformat() in set(no_school_dates) or local.weekday() not in job.get("weekdays", range(5)):
        reason = "no_school_skip"
    elif local < planned:
        reason = "not_due"
    elif job_key in completed_jobs:
        reason = "already_completed"
    elif any(dep not in completed_jobs for dep in job.get("dependencies", ())):
        reason = "dependency_incomplete"
    elif job.get("interval_weeks", 1) > 1:
        anchor = date.fromisoformat(job["anchor_date"])
        day_delta = (local.date() - anchor).days
        if day_delta < 0 or (day_delta // 7) % job["interval_weeks"]:
            reason = "outside_interval"
    return {"job": job_key, "planned_for": planned.isoformat(), "reason": reason,
            "enabled": False, "scheduled": False, "executed": False,
            "due_preview": reason == "due_preview"}
