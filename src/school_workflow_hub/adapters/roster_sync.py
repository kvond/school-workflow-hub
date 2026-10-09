"""Authenticated status contract for the shared weekly roster-sync workflow.

This module deliberately does not read Infinite Campus or write a workbook.
It exposes the one durable task record those future adapters must use, so
ChatGPT, Claude, and the dashboard see the same state and cannot each invent
their own run history.
"""
from school_workflow_hub.adapters.firestore_state import configured_store
from school_workflow_hub.adapters.hosted_agenda import (
    ConnectionError,
    _identity_configuration,
    authorize,
)
from school_workflow_hub.core.cloud_state import CloudState, StoreUnavailable, key
from school_workflow_hub.adapters.roster_only import configured_targets


ROSTER_SYNC_SCOPE = {
    "workflow": "weekly-roster-sync",
    "class_key": "all-classes",
    "assignment_key": "2026-2027",
}


def _task_id():
    # Match CloudState.create_task without creating a task on a read-only GET.
    return key("task", ROSTER_SYNC_SCOPE)


def read_roster_sync_status(assertion, config):
    """Return a redacted, authenticated status with no source or student data."""
    audience, allowed = _identity_configuration(config)
    authorize(assertion, audience, allowed)
    result = {
        "schema_version": "hub-roster-sync/v1",
        "workflow": "weekly_roster_sync",
        "status": "Needs you",
        "run_enabled": False,
        "source": {"infinite_campus": "not_configured"},
        "destination": {"roster_workbook": "not_configured"},
        "shared_run_claims": "not_configured",
        "last_check_at": None,
        "next_action": "verify_infinite_campus_reader_and_roster_workbook_writer",
        "external_writes": 0,
    }
    # Roster-only sections are explicit scope, never extra grading populations.
    try:
        targets = configured_targets(config)
        result["roster_scope"] = {"instructional_sections": 4,
                                  "roster_only_sections": len(targets),
                                  "study_hall_included": bool(targets)}
    except ConnectionError:
        result["roster_scope"] = {"instructional_sections": 4,
                                  "roster_only_sections": 0,
                                  "study_hall_included": False,
                                  "problem": "roster_only_configuration_invalid"}
    try:
        store = CloudState(configured_store(config))
        task = store.read_task(_task_id())
    except StoreUnavailable:
        return result
    result["shared_run_claims"] = "configured"
    if task is None:
        result["status"] = "Needs you"
        return result
    # The task is internal state, so return only the teacher-facing fields.
    result.update(status=task["status"], next_action=task.get("next_action"),
                  last_check_at=(task.get("last_result") or {}).get("stored_at"))
    if task.get("active_run"):
        result["active_run"] = True
    return result
