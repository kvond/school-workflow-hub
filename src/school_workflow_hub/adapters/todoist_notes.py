"""Read only one explicitly configured Todoist project/section into memory."""
from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Tuple
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from school_workflow_hub.adapters.read_only_http import read_json, SourceReadError, validate_token


@dataclass(frozen=True)
class NoteTaskSnapshot:
    tasks: Tuple[dict, ...] = field(repr=False)
    pages_read: int
    pagination_complete: bool = True
    # Todoist pagination is not a transaction: edits can occur between pages.
    atomic_snapshot: bool = False
    external_writes: int = 0


@dataclass(frozen=True)
class TodoistNoteReader:
    access_token: str = field(repr=False)
    opener: object = field(default=None, repr=False, compare=False)
    sleeper: object = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        validate_token(self.access_token)

    def read_notes(self, project_id, section_id, *, label=None, max_pages=10):
        if any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", v)
               for v in (project_id, section_id)):
            raise SourceReadError("explicit_note_scope_required")
        if type(max_pages) is not int or not 1 <= max_pages <= 20:
            raise SourceReadError("invalid_note_page_limit")
        if label is not None and (not isinstance(label, str) or not label or len(label) > 100
                                  or any(ord(c) < 32 for c in label)):
            raise SourceReadError("invalid_note_label")
        fixed = [("project_id", project_id), ("section_id", section_id), ("limit", "100")]
        if label is not None:
            fixed.append(("label", label))
        cursor, seen, records = None, set(), {}
        for page_index in range(max_pages):
            query = fixed + ([("cursor", cursor)] if cursor is not None else [])
            options = {"opener": self.opener}
            if self.sleeper is not None:
                options["sleeper"] = self.sleeper
            raw = read_json("https://api.todoist.com/api/v1/tasks?" + urlencode(query), self.access_token, **options)
            if (not isinstance(raw, dict) or not isinstance(raw.get("results"), list)
                    or "next_cursor" not in raw or len(raw["results"]) > 100):
                raise SourceReadError("invalid_note_page")
            for task in raw["results"]:
                if (not isinstance(task, dict) or task.get("project_id") != project_id
                        or task.get("section_id") != section_id
                        or task.get("is_deleted", False) is not False
                        or not isinstance(task.get("id"), str)
                        or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task["id"])
                        or (label is not None and (not isinstance(task.get("labels"), list) or label not in task["labels"]))):
                    raise SourceReadError("note_source_scope_mismatch")
                # Keep only routing/content fields; discard user IDs/URLs and
                # other provider metadata. Actual notes remain private in memory.
                projected = {k: task[k] for k in ("id", "content", "description", "labels", "due", "checked", "is_completed", "updated_at") if k in task}
                due = projected.get("due")
                if isinstance(due, dict) and isinstance(due.get("date"), str) and "T" in due["date"]:
                    try:
                        moment = datetime.fromisoformat(due["date"].replace("Z", "+00:00"))
                        if moment.utcoffset() is None and due.get("timezone"):
                            moment = moment.replace(tzinfo=ZoneInfo(due["timezone"]))
                        # A floating time without a declared source timezone
                        # remains naive and is held by the routing validator.
                        projected["due"] = {"datetime": moment.isoformat()}
                    except (ValueError, TypeError, KeyError):
                        raise SourceReadError("invalid_note_due_time") from None
                if task["id"] in records and records[task["id"]] != projected:
                    raise SourceReadError("note_changed_during_read")
                records[task["id"]] = projected
            cursor = raw["next_cursor"]
            if cursor is None:
                return NoteTaskSnapshot(tuple(records[k] for k in sorted(records)), page_index + 1)
            if (not isinstance(cursor, str) or not cursor or len(cursor) > 2048
                    or any(ord(c) < 32 for c in cursor) or cursor in seen):
                raise SourceReadError("invalid_or_repeated_note_cursor")
            seen.add(cursor)
        raise SourceReadError("note_page_limit")
