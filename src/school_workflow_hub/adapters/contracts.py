"""Ports for source reads and future two-stage school-system execution."""

from __future__ import annotations

from typing import Iterable, Protocol

from school_workflow_hub.core.models import (
    AdapterResult,
    AssignmentRecord,
    SubmissionRevision,
)


class AgendaReader(Protocol):
    def read_week(self, class_stable_id: str, week_key: str) -> AdapterResult:
        ...


class AssignmentMasterReader(Protocol):
    def assignments_for(self, class_stable_id: str) -> AdapterResult:
        ...


class StudentWorkReader(Protocol):
    def changed_submissions(self, class_stable_id: str, since: str) -> AdapterResult:
        ...


class LocalSyntheticAdapter:
    """Marker for adapters that can run without contacting an external system.

    The foundation's workflow broker accepts only this marker.  A future live
    reader must use a separately approved external-read implementation rather
    than quietly opting into a synthetic-only path.
    """


def read_synthetic_student_work(
    reader: StudentWorkReader, class_stable_id: str, since: str
) -> AdapterResult:
    """Invoke only an explicitly local fake/not-configured adapter.

    This is an executable gate, not a documentation convention: an arbitrary
    object with a ``changed_submissions`` method is never called.
    """

    if not isinstance(reader, LocalSyntheticAdapter):
        return AdapterResult(
            "not_configured",
            reason="external student-work reads are disabled in the synthetic foundation",
        )
    return reader.changed_submissions(class_stable_id, since)


class BrowserExecutor(Protocol):
    """Future stage-two executor; it must re-read then apply a pre-authorized action."""

    def inspect_target(self, target_stable_id: str) -> AdapterResult:
        ...

    def execute_authorized_action(self, action_id: str) -> AdapterResult:
        ...
