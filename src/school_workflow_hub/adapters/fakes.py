"""Wholly synthetic adapters for deterministic offline tests and demos."""

from __future__ import annotations

from dataclasses import asdict
from typing import Dict, Iterable, List

from school_workflow_hub.adapters.contracts import LocalSyntheticAdapter
from school_workflow_hub.core.models import AdapterResult, AssignmentRecord, SubmissionRevision


class FakeAgendaAdapter(LocalSyntheticAdapter):
    def __init__(self, weeks: Dict[tuple[str, str], Dict[str, str]]):
        self._weeks = weeks

    def read_week(self, class_stable_id: str, week_key: str) -> AdapterResult:
        item = self._weeks.get((class_stable_id, week_key))
        if item is None:
            return AdapterResult("not_found", reason="synthetic week missing")
        return AdapterResult("ok", data=dict(item))


class FakeAssignmentMasterAdapter(LocalSyntheticAdapter):
    def __init__(self, assignments: Iterable[AssignmentRecord]):
        self._assignments = list(assignments)

    def assignments_for(self, class_stable_id: str) -> AdapterResult:
        records = [asdict(item) for item in self._assignments if item.class_stable_id == class_stable_id]
        return AdapterResult("ok", data={"assignments": records})


class FakeStudentWorkAdapter(LocalSyntheticAdapter):
    def __init__(self, revisions: Iterable[SubmissionRevision]):
        self._revisions = list(revisions)

    def changed_submissions(self, class_stable_id: str, since: str) -> AdapterResult:
        # A real adapter would filter by stable assignment/class mappings; the fake
        # returns declared synthetic revisions only and never contacts a platform.
        return AdapterResult("ok", data={"revisions": [asdict(item) for item in self._revisions], "since": since})


class FakeAIProvider:
    """Records no live calls; it exists only to make the disabled boundary explicit."""

    identifier = "fake/disabled"

    def feedback(self, prompt_version: str, submission_reference: str) -> AdapterResult:
        return AdapterResult("disabled", reason="live models are disabled for this foundation")
