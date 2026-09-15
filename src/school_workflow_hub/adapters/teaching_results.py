"""Authenticated, read-only boundary for private teaching workflow results.

The hosting application injects a private result provider. No disk fallback,
fixture fallback, credentials, model execution or write operation is present.
The default deployment has no provider and therefore returns unavailable.
"""
from datetime import date, datetime, timezone
import json
import os
import re

from .hosted_agenda import authorize, ConnectionError

SCHEMA = "hub-teaching-results/v1"
CLASS_KEYS = {"biology", "forensics", "anatomy"}
CAPABILITIES = {key: False for key in (
    "run_enabled", "save_enabled", "post_ic_grade", "send_parent_email", "print_feedback")}


def _timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("timezone_required")
    return parsed


def validate_scope(day, class_key, agenda_checked_at):
    try:
        if date.fromisoformat(day).isoformat() != day or class_key not in CLASS_KEYS:
            raise ValueError()
        if _timestamp(agenda_checked_at) > datetime.now(timezone.utc):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ConnectionError("invalid_teaching_scope") from None


def validate_result(data, *, day, class_key, agenda_checked_at, allow_synthetic=False):
    """Reject stale scope, unsupported actions and malformed provider output.

    Providers are trusted host components, not browser-submitted JSON. They must
    independently bind the result's assignment to the supplied Agenda snapshot;
    never copy request scope into unrelated data to make this validation pass.
    """
    if (not isinstance(data, dict) or data.get("schema_version") != SCHEMA
            or data.get("date") != day or data.get("class_key") != class_key
            or data.get("agenda_checked_at") != agenda_checked_at
            or data.get("provenance") not in ({"source_read", "synthetic"} if allow_synthetic else {"source_read"})
            or not isinstance(data.get("capabilities"), dict)
            or set(data["capabilities"]) != set(CAPABILITIES)
            or any(v is not False for v in data["capabilities"].values())
            or type(data.get("external_writes")) is not int or data["external_writes"] != 0
            or not isinstance(data.get("class_stable_id"), str) or not data["class_stable_id"]
            or not isinstance(data.get("assignment_key"), str) or not data["assignment_key"]
            or not isinstance(data.get("source_revision"), str) or not data["source_revision"]):
        raise ConnectionError("teaching_result_unverified")
    try:
        checked = _timestamp(data["checked_at"])
        if checked > datetime.now(timezone.utc) or checked < _timestamp(agenda_checked_at):
            raise ValueError()
        if (not isinstance(data["completion"]["rows"], list)
                or len(data["completion"]["rows"]) > 500
                or not isinstance(data["feedback"]["reports"], list)
                or len(data["feedback"]["reports"]) > 500
                or not isinstance(data["feedback"]["compiled_records"], list)
                or not isinstance(data["summary"], dict)):
            raise ValueError()
        # Also rejects non-JSON payloads and non-finite numeric scores.
        if len(json.dumps(data, allow_nan=False).encode("utf-8")) > 2_000_000:
            raise ValueError()
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ConnectionError("teaching_result_unverified") from None
    return data


def read_teaching_results(assertion, *, day, class_key, agenda_checked_at,
                          provider=None, config=None):
    validate_scope(day, class_key, agenda_checked_at)
    config = os.environ if config is None else config
    audience = config.get("HUB_IAP_AUDIENCE", "")
    allowed = {v.strip().lower() for v in config.get("HUB_ALLOWED_EMAILS", "").split(",") if v.strip()}
    if (not re.fullmatch(r"/projects/[0-9]+/locations/[a-z0-9-]+/services/[a-z0-9-]+", audience)
            or not allowed or any("@" not in value for value in allowed)):
        raise ConnectionError("connection_not_configured")
    # Same signed IAP check as existing Agenda endpoints; do not trust email headers.
    authorize(assertion, audience, allowed)
    if provider is None:
        raise ConnectionError("teaching_results_not_connected")
    try:
        data = provider.read_results(day=day, class_key=class_key,
                                     agenda_checked_at=agenda_checked_at)
    except Exception:
        raise ConnectionError("teaching_results_unavailable") from None
    return validate_result(data, day=day, class_key=class_key, agenda_checked_at=agenda_checked_at)
