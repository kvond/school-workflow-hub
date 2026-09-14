"""All-or-nothing 14-day collection using shared SubmissionRevision records.

Provider supplies metadata pages. No answer text, grades, storage or writes.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional, Tuple
from school_workflow_hub.core.models import SubmissionRevision
from school_workflow_hub.core.runs import deterministic_id


@dataclass(frozen=True)
class SubmissionPage:
    class_stable_id: str = field(repr=False)
    snapshot_revision: str = field(repr=False)
    items: Tuple[SubmissionRevision, ...] = field(repr=False)
    next_cursor: Optional[str] = field(default=None, repr=False)
    complete: bool = True


@dataclass(frozen=True)
class SubmissionCollection:
    records: Tuple[SubmissionRevision, ...] = field(repr=False)
    window_start: str
    window_end: str
    complete: bool
    issue_codes: Tuple[str, ...]
    pages_read: int
    receipt_id: Optional[str] = None
    external_writes: int = 0


def _identity(value):
    return (isinstance(value, str) and 0 < len(value) <= 256 and value != "UNVERIFIED"
            and not any(c.isspace() or ord(c) < 32 for c in value))


def collect_submission_window(fetch_page: Callable, *, class_stable_id: str,
                              assignment_ids: frozenset, as_of: datetime,
                              max_pages: int = 50, max_records: int = 5000):
    """fetch_page(cursor, since, until) -> complete page of one class snapshot.

    Failures return no records: partial lists cannot imply missing work or
    advance checkpoints. Identical complete collections yield identical receipts.
    """
    if (not _identity(class_stable_id) or not isinstance(assignment_ids, frozenset)
            or not assignment_ids or any(not _identity(v) for v in assignment_ids)
            or not isinstance(as_of, datetime) or as_of.utcoffset() is None
            or type(max_pages) is not int or not 1 <= max_pages <= 100
            or type(max_records) is not int or not 1 <= max_records <= 10000):
        raise ValueError("invalid_submission_collection_request")
    since, until = as_of - timedelta(days=14), as_of
    start, end = since.isoformat(), until.isoformat()
    cursor, snapshot, pages = None, None, 0
    cursors, unique, owners = set(), {}, {}
    delivered = 0

    def failed(code):
        return SubmissionCollection((), start, end, False, (code,), pages)

    while True:
        if pages >= max_pages:
            return failed("submission_page_limit")
        try:
            page = fetch_page(cursor, since, until)
        except Exception:
            return failed("submission_source_unavailable")
        pages += 1
        if (not isinstance(page, SubmissionPage) or page.complete is not True
                or page.class_stable_id != class_stable_id
                or not _identity(page.snapshot_revision) or not isinstance(page.items, tuple)):
            return failed("submission_page_incomplete_or_wrong_scope")
        if snapshot is not None and snapshot != page.snapshot_revision:
            return failed("submission_snapshot_changed")
        snapshot = page.snapshot_revision
        delivered += len(page.items)
        if delivered > max_records:
            return failed("submission_record_limit")
        for record in page.items:
            if (not isinstance(record, SubmissionRevision)
                    or any(not _identity(v) for v in (record.stable_id, record.assignment_stable_id,
                                                       record.learner_stable_id, record.revision_hash))
                    or record.assignment_stable_id not in assignment_ids):
                return failed("submission_identity_or_assignment_mismatch")
            try:
                observed = datetime.fromisoformat(record.observed_at.replace("Z", "+00:00"))
                if observed.utcoffset() is None or not since <= observed <= until:
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                return failed("submission_outside_window_or_invalid_time")
            owner = (record.assignment_stable_id, record.learner_stable_id)
            if record.stable_id in owners and owners[record.stable_id] != owner:
                return failed("submission_identity_conflict")
            owners[record.stable_id] = owner
            key = (record.stable_id, record.revision_hash)
            if key in unique and unique[key] != record:
                return failed("submission_revision_conflict")
            unique[key] = record
        if page.next_cursor is None:
            records = tuple(unique[key] for key in sorted(unique))
            receipt = deterministic_id("submission-collection", {
                "class_id": class_stable_id, "assignments": sorted(assignment_ids),
                "since": start, "until": end, "snapshot": snapshot,
                "records": [vars(record) for record in records]})
            return SubmissionCollection(records, start, end, True, (), pages, receipt)
        if not _identity(page.next_cursor) or page.next_cursor in cursors:
            return failed("submission_cursor_invalid_or_repeated")
        cursors.add(page.next_cursor)
        cursor = page.next_cursor
