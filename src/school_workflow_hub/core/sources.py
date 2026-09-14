"""Versioned source observations and stale-proposal invalidation."""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

from .database import transaction, utc_now
from .models import SourceRevision
from .runs import canonical_json, content_hash, deterministic_id


def record_source_revision(
    connection: sqlite3.Connection,
    source_type: str,
    stable_object_id: str,
    payload: Any,
    source_reference: str,
    run_id: Optional[str] = None,
) -> SourceRevision:
    """Record a source hash and supersede plans built from an older revision."""
    digest = content_hash(payload)
    revision_id = deterministic_id(
        "src", {"type": source_type, "stable_id": stable_object_id, "content_hash": digest}
    )
    with transaction(connection):
        existing = connection.execute(
            "SELECT id, active FROM source_revisions "
            "WHERE source_type = ? AND stable_object_id = ? AND content_hash = ?",
            (source_type, stable_object_id, digest),
        ).fetchone()
        if existing and existing["active"]:
            return SourceRevision(existing["id"], source_type, stable_object_id, digest, False)

        connection.execute(
            "UPDATE source_revisions SET active = 0 WHERE source_type = ? AND stable_object_id = ?",
            (source_type, stable_object_id),
        )
        if existing:
            connection.execute(
                "UPDATE source_revisions SET active = 1, observed_at = ?, run_id = ? WHERE id = ?",
                (utc_now(), run_id, existing["id"]),
            )
            revision_id = existing["id"]
        else:
            connection.execute(
                "INSERT INTO source_revisions "
                "(id, run_id, source_type, stable_object_id, content_hash, source_reference, observed_at, payload_json, active) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (
                    revision_id,
                    run_id,
                    source_type,
                    stable_object_id,
                    digest,
                    source_reference,
                    utc_now(),
                    canonical_json(payload),
                ),
            )
        connection.execute(
            "UPDATE proposed_actions SET state = 'superseded', review_status = 'needs_review', "
            "verification_json = ? "
            "WHERE id IN ("
            "  SELECT dependency.action_id "
            "  FROM action_source_dependencies AS dependency "
            "  JOIN source_revisions AS source ON source.id = dependency.source_revision_id "
            "  WHERE source.source_type = ? AND source.stable_object_id = ? AND source.id <> ?"
            ") AND state = 'proposed'",
            (
                canonical_json({"reason": "source_revision_changed", "new_source_revision_id": revision_id}),
                source_type,
                stable_object_id,
                revision_id,
            ),
        )
    return SourceRevision(revision_id, source_type, stable_object_id, digest, True)
