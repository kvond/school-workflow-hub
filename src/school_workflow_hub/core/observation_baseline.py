"""Atomic opaque observations in the EXISTING source ledger, no new database.

Caller owns the approved database and supplies only fingerprint_snapshot output.
No network, source contents, keys, credentials, scheduler or live write adapter.
"""
from datetime import datetime, timezone
import json
import re

from .database import transaction
from .sources import record_source_revision
from .runs import content_hash
from school_workflow_hub.adapters.google_sheets import compare_fingerprints

SOURCE_TYPE = "opaque_sheets_observation"
FIELDS = {"schema_version", "scope", "fingerprint", "tab_count", "range_count",
          "source_reads", "external_writes", "link_access_verified", "student_access_verified"}


def _stream(value):
    if not isinstance(value, str) or not re.fullmatch(r"agenda-[a-z0-9_-]{1,40}", value):
        raise ValueError("invalid_observation_stream")


def _time(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise ValueError("invalid_observation_time") from None


def _validate(observation):
    compare_fingerprints(None, observation)
    if (set(observation) != FIELDS or type(observation["tab_count"]) is not int
            or not 0 <= observation["tab_count"] <= 1000
            or type(observation["range_count"]) is not int or not 0 <= observation["range_count"] <= 4
            or type(observation["source_reads"]) is not int or observation["source_reads"] != 1
            or type(observation["external_writes"]) is not int or observation["external_writes"] != 0
            or observation["link_access_verified"] is not False
            or observation["student_access_verified"] is not False):
        raise ValueError("invalid_observation")


def read_baseline(connection, stream_key):
    _stream(stream_key)
    rows = connection.execute(
        "SELECT id, observed_at, payload_json FROM source_revisions "
        "WHERE source_type=? AND stable_object_id=? AND active=1",
        (SOURCE_TYPE, stream_key)).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError("invalid_stored_observation")
    row = rows[0]
    try:
        payload = json.loads(row["payload_json"])
        _validate(payload)
        _time(row["observed_at"])
    except (ValueError, TypeError, KeyError):
        raise ValueError("invalid_stored_observation") from None
    return {"revision_id": row["id"], "observed_at": row["observed_at"], "observation": payload,
            "checkpoint_token": content_hash({"revision": row["id"], "observed_at": row["observed_at"]})}


def checkpoint_observation(connection, stream_key, observation, *, observed_at,
                           expected_checkpoint, now):
    """Compare-and-set one complete read; failed/partial reads never call this.

    expected_checkpoint is the last checkpoint token, or None for first use.
    A missing baseline is established without claiming that something changed.
    Scope/key/range changes require separate review and never replace baseline.
    """
    _stream(stream_key)
    _validate(observation)
    captured, current_time = _time(observed_at), _time(now)
    if captured > current_time or (current_time - captured).total_seconds() > 86400:
        raise ValueError("observation_not_fresh")
    with transaction(connection):
        previous = read_baseline(connection, stream_key)
        token = previous["checkpoint_token"] if previous else None
        if expected_checkpoint != token:
            return {"status": "baseline_conflict", "saved": False, "change_proven": False}
        compared = compare_fingerprints(previous["observation"] if previous else None, observation)
        if compared["status"] == "scope_changed":
            return dict(compared, saved=False)
        if previous:
            last_time = _time(previous["observed_at"])
            if captured < last_time:
                return {"status": "stale_observation", "saved": False, "change_proven": False}
            if captured == last_time and compared["status"] == "changed":
                return {"status": "conflicting_observation_time", "saved": False, "change_proven": False}
        record = record_source_revision(connection, SOURCE_TYPE, stream_key, observation,
                                        "opaque-observation://" + stream_key)
        connection.execute("UPDATE source_revisions SET observed_at=? WHERE id=?",
                           (captured.isoformat(), record.id))
        return {"status": "baseline_created" if previous is None else compared["status"],
                "saved": True, "change_proven": compared["change_proven"],
                "revision_id": record.id,
                "checkpoint_token": content_hash({"revision": record.id, "observed_at": captured.isoformat()})}
