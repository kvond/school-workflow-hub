"""Resumable local preview runs using the existing step and audit ledger."""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from typing import Callable, Mapping, Tuple

from .actions import create_review_item
from .runs import create_or_get_run, finish_run
from .spine import record_run_step
from .workflow import WorkflowPreview, assert_safe_preview, persist_preview


class RetryablePreviewError(RuntimeError):
    """An injected local transient failure; never contains durable raw data."""


@dataclass(frozen=True)
class PreviewStep:
    key: str
    build: Callable[[], WorkflowPreview]
    dependencies: Tuple[str, ...] = ()


def run_preview_steps(connection: sqlite3.Connection, *, workflow: str,
                      inputs: Mapping, steps: Tuple[PreviewStep, ...],
                      max_attempts: int = 3) -> dict:
    """Run repository-owned preview builders, skipping verified local steps.

    There is no network adapter, scheduler, or external apply operation here.
    Retries occur on a later invocation, never in an unbounded retry loop.
    The caller includes every source/config revision in ``inputs``.
    """
    assert_safe_preview(inputs)
    assert_safe_preview({"workflow": workflow, "steps": [(step.key, step.dependencies) for step in steps]})
    if not isinstance(workflow, str) or not workflow.strip() or any(not isinstance(step.key, str) or not step.key.strip() for step in steps):
        raise ValueError("named workflow and steps are required")
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    keys = [step.key for step in steps]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate pipeline step")
    previous = set()
    for step in steps:
        if not set(step.dependencies).issubset(previous):
            raise ValueError("dependencies must precede their consumer without cycles")
        previous.add(step.key)
    run = create_or_get_run(connection, workflow, {
        "inputs": dict(inputs), "steps": [(s.key, s.dependencies) for s in steps],
        "max_attempts": max_attempts,
    })
    results = {}
    for step in steps:
        if any(results[dep]["status"] != "completed" for dep in step.dependencies):
            results[step.key] = {"status": "blocked", "code": "dependency_incomplete"}
            continue
        rows = connection.execute(
            "SELECT status, detail_json FROM run_steps WHERE run_id = ? AND name = ? ORDER BY step_index",
            (run.id, step.key),
        ).fetchall()
        completed = next((row for row in reversed(rows) if row["status"] == "completed"), None)
        if completed:
            receipt = json.loads(completed["detail_json"])
            # Confirm stored receipt targets still exist. Do not trust a status
            # label alone after a failed or manually altered local database.
            intact = all(connection.execute("SELECT 1 FROM runs WHERE id = ? AND status = 'completed' AND external_writes = 0 AND live_model_calls = 0", (child,)).fetchone()
                         for child in receipt.get("preview_run_ids", []))
            intact = intact and all(connection.execute(
                "SELECT 1 FROM proposed_actions WHERE id = ? AND state = 'proposed' AND NOT EXISTS ("
                "SELECT 1 FROM action_source_dependencies d JOIN source_revisions s ON s.id = d.source_revision_id "
                "WHERE d.action_id = proposed_actions.id AND s.active = 0)", (action,)
            ).fetchone() for action in receipt.get("action_ids", []))
            if not intact:
                results[step.key] = {"status": "blocked", "code": "receipt_readback_failed"}
                continue
            results[step.key] = {"status": "completed", **receipt}
            continue
        attempts = sum(row["status"] == "started" for row in rows)
        if attempts >= max_attempts or any(row["status"] == "denied" for row in rows):
            results[step.key] = {"status": "blocked", "code": "retry_exhausted_or_permanent"}
            continue
        record_run_step(connection, run_id=run.id, name=step.key, status="started",
                        detail={"attempt": attempts + 1})
        try:
            preview = step.build()
            receipt = persist_preview(connection, preview)
            detail = {"preview_run_ids": [receipt["run_id"]],
                      "action_ids": list(receipt["action_ids"]),
                      "change_count": len(receipt["action_ids"]),
                      "issue_count": len(receipt["issue_ids"])}
            record_run_step(connection, run_id=run.id, name=step.key, status="completed", detail=detail)
            results[step.key] = {"status": "completed", **detail}
        except Exception as error:
            retryable = isinstance(error, RetryablePreviewError)
            code = "transient_preview_failure" if retryable else "invalid_preview_input"
            record_run_step(connection, run_id=run.id, name=step.key,
                            status="blocked" if retryable else "denied", detail={"code": code})
            create_review_item(connection, run_id=run.id, kind=code, severity="error",
                               payload={"step": step.key, "retryable": retryable})
            results[step.key] = {"status": "blocked", "code": code}
    status = "completed" if all(row["status"] == "completed" for row in results.values()) else "blocked"
    # Do not rewrite completed run timestamps on identical complete reruns.
    if run.created or run.status != status:
        finish_run(connection, run.id, status=status, details={"steps": len(steps)})
    return {"run_id": run.id, "status": status, "steps": results,
            "external_writes": 0, "live_model_calls": 0}
