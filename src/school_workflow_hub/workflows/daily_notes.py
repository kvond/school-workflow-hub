"""Route supplied dated/tagged Todoist notes; no source or journal mutation."""
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Mapping, Tuple
from zoneinfo import ZoneInfo
from school_workflow_hub.core.runs import content_hash, deterministic_id
from school_workflow_hub.core.workflow import WorkflowIssue
from school_workflow_hub.workflows.field_notes import FieldNoteIntake


@dataclass(frozen=True)
class DailyNotesResult:
    notes: Tuple[FieldNoteIntake, ...] = field(repr=False)
    issues: Tuple[WorkflowIssue, ...]
    already_seen_count: int
    deferred_count: int
    closed_count: int
    external_writes: int = 0


def plan_daily_notes(tasks: tuple, *, local_day: date, timezone_name: str,
                     class_labels: Mapping[str, str], meeting_class_ids: frozenset,
                     seen_versions: frozenset = frozenset(), no_school: bool = False):
    """Dates/meetings are supplied, never inferred by alternating A/B days.

    Task fields: id, content, description, labels, due {date or datetime},
    checked/is_completed. Conflicting copies of one task are held. Caller owns
    private source intake and storing accepted note versions after append/readback.
    """
    if (not isinstance(tasks, tuple) or len(tasks) > 5000 or type(local_day) is not date
            or not isinstance(class_labels, dict) or not class_labels
            or not isinstance(meeting_class_ids, frozenset)
            or not isinstance(seen_versions, frozenset) or type(no_school) is not bool
            or any(not isinstance(k, str) or not k.strip() or not isinstance(v, str)
                   or not v or any(c.isspace() for c in v) for k, v in class_labels.items())
            or not meeting_class_ids.issubset(set(class_labels.values()))):
        raise ValueError("invalid_daily_note_request")
    zone = ZoneInfo(timezone_name)
    issues, grouped, notes = [], {}, []
    seen_count = deferred = closed = 0
    for task in tasks:
        if (not isinstance(task, dict) or not isinstance(task.get("id"), str)
                or not task["id"] or len(task["id"]) > 256 or any(c.isspace() for c in task["id"])):
            issues.append(WorkflowIssue("invalid_class_note_source", "error"))
            continue
        grouped.setdefault(task["id"], []).append(task)
    for task_id, copies in sorted(grouped.items()):
        if any(copy != copies[0] for copy in copies[1:]):
            issues.append(WorkflowIssue("conflicting_class_note_revision", "error"))
            continue
        task = copies[0]
        try:
            if any(type(task.get(k, False)) is not bool for k in ("checked", "is_completed")):
                raise ValueError()
            if task.get("checked", False) or task.get("is_completed", False):
                closed += 1
                continue
            labels = task.get("labels", [])
            if not isinstance(labels, list) or any(not isinstance(x, str) for x in labels):
                raise ValueError()
            classes = {class_labels[label] for label in labels if label in class_labels}
            if len(classes) != 1:
                issues.append(WorkflowIssue("missing_or_ambiguous_class_note_tag", "warning"))
                continue
            due = task.get("due")
            if not isinstance(due, dict):
                issues.append(WorkflowIssue("class_note_date_missing", "warning"))
                continue
            if due.get("datetime"):
                moment = datetime.fromisoformat(due["datetime"].replace("Z", "+00:00"))
                if moment.utcoffset() is None:
                    raise ValueError()
                on_date = moment.astimezone(zone).date()
            else:
                on_date = date.fromisoformat(due["date"])
            class_id = next(iter(classes))
            if no_school or on_date != local_day or class_id not in meeting_class_ids:
                deferred += 1
                continue
            title, body = task.get("content"), task.get("description", "")
            if (not isinstance(title, str) or not title.strip() or len(title) > 2000
                    or not isinstance(body, str) or len(body) > 10000):
                raise ValueError()
            observation = title.strip() + (("\n\n" + body.strip()) if body.strip() else "")
            version = deterministic_id("class-note", {"source": task_id, "date": on_date.isoformat(),
                                        "class": class_id, "content_hash": content_hash(observation)})
            if version in seen_versions:
                seen_count += 1
                continue
            notes.append(FieldNoteIntake(version, on_date, class_id, observation,
                                         todoist_task_ids=(task_id,)))
        except (ValueError, TypeError, KeyError, AttributeError):
            issues.append(WorkflowIssue("invalid_class_note_source", "error"))
    return DailyNotesResult(tuple(notes), tuple(issues), seen_count, deferred, closed)
