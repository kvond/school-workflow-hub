"""Deterministic run ledger and exception recording."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Dict

from .database import transaction, utc_now
from .models import RunResult
from .safe_logging import safe_exception_message, sanitize


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def deterministic_id(prefix: str, value: Any) -> str:
    return f"{prefix}_{content_hash(value)[:24]}"


def create_or_get_run(
    connection: sqlite3.Connection,
    workflow: str,
    inputs: Dict[str, Any],
    mode: str = "dry_run",
) -> RunResult:
    input_digest = content_hash(inputs)
    existing = connection.execute(
        "SELECT id, status FROM runs WHERE workflow = ? AND input_hash = ?",
        (workflow, input_digest),
    ).fetchone()
    if existing:
        return RunResult(existing["id"], False, existing["status"])
    run_id = deterministic_id("run", {"workflow": workflow, "input_hash": input_digest})
    with transaction(connection):
        connection.execute(
            "INSERT INTO runs(id, workflow, input_hash, status, mode, started_at, detail_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, workflow, input_digest, "running", mode, utc_now(), canonical_json({"input_version": 1})),
        )
    return RunResult(run_id, True, "running")


def finish_run(
    connection: sqlite3.Connection,
    run_id: str,
    status: str = "completed",
    external_writes: int = 0,
    live_model_calls: int = 0,
    details: Dict[str, Any] | None = None,
) -> None:
    if external_writes != 0 or live_model_calls != 0:
        raise ValueError("the synthetic foundation cannot record external writes or live model calls")
    with transaction(connection):
        connection.execute(
            "UPDATE runs SET status = ?, finished_at = ?, external_writes = ?, live_model_calls = ?, detail_json = ? "
            "WHERE id = ?",
            (status, utc_now(), external_writes, live_model_calls, canonical_json(sanitize(details or {})), run_id),
        )


def record_exception(connection: sqlite3.Connection, run_id: str, error: BaseException) -> None:
    """Record a concise, sanitized failure without treating it as success."""
    message = safe_exception_message(error)
    with transaction(connection):
        connection.execute(
            "UPDATE runs SET status = 'failed', finished_at = ?, error_type = ?, error_message = ? WHERE id = ?",
            (utc_now(), type(error).__name__, message, run_id),
        )


def get_run(connection: sqlite3.Connection, run_id: str) -> Dict[str, Any]:
    row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise KeyError(f"unknown run: {run_id}")
    return dict(row)
