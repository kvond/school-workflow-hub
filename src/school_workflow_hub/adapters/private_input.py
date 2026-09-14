"""Read one explicitly supplied private snapshot; emit only an opaque preview.

No API, browser, credentials, source writes, model, scheduler or executor.
The existing comparator receives a pseudonymous projection, never raw IDs/URLs.
This is NOT a student-data intake or proof of present platform/student access.
"""

from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat

from school_workflow_hub.core.models import AssignmentRecord
from school_workflow_hub.core.runs import deterministic_id
from school_workflow_hub.core.workflow import assert_safe_preview, validate_preview
from school_workflow_hub.workflows.schoology_gradebook import (
    UNVERIFIED, DesiredPlatformAssignment, ObservedPlatformAssignment,
    canonicalize_url, reconcile_platform_assignments,
)

SCHEMA = "private-assignment-input/v1"
MAX_BYTES = 262144
MAX_AGE = timedelta(hours=24)
_SYNC_PARTS = {"dropbox", "onedrive", "google drive", "googledrive", "icloud drive", "mobile documents", "cloudstorage"}
_FIELDS = {"schoology": ("due_date", "deck_reference"), "infinite_campus": ("due_date",)}


class PrivateInputError(ValueError):
    """Messages are fixed codes, never source content or filesystem paths."""


def _fail(code):
    raise PrivateInputError(code)


def _read_private_file(root, source):
    """Open every path component without following links; no writes or scans.

    The caller must first approve the root's location and backup policy. Known
    sync paths and Git ancestors are rejected, but this is not an OS sync audit.
    Only one direct child of an existing, owner-only root is accepted.
    """
    root, source = Path(root), Path(source)
    if not root.is_absolute() or not source.is_absolute() or source.parent != root:
        _fail("private_input_requires_absolute_direct_child")
    if any(p in {".", ".."} for p in root.parts + source.parts) or root == Path("/"):
        _fail("unsafe_private_location")
    if any(any(marker in part.lower() for marker in _SYNC_PARTS) for part in root.parts):
        _fail("sync_location_rejected")
    # lstat the supplied path hierarchy only; never enumerate file contents.
    for parent in (root,) + tuple(root.parents):
        if (parent / ".git").exists():
            _fail("repository_location_rejected")
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in root.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        owner = os.fstat(directory)
        if owner.st_uid != os.getuid() or stat.S_IMODE(owner.st_mode) & 0o077:
            _fail("private_root_requires_owner_only_permissions")
        fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                    or stat.S_IMODE(before.st_mode) & 0o077 or before.st_nlink != 1):
                _fail("private_input_requires_single_owner_only_regular_file")
            if before.st_size > MAX_BYTES:
                _fail("private_input_too_large")
            parts, count = [], 0
            while True:
                chunk = os.read(fd, min(65536, MAX_BYTES + 1 - count))
                if not chunk:
                    break
                parts.append(chunk)
                count += len(chunk)
                if count > MAX_BYTES:
                    _fail("private_input_too_large")
            after = os.fstat(fd)
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                _fail("private_input_changed_during_read")
            return b"".join(parts)
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def _object(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        _fail("unsupported_private_input_fields")


def _identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", value):
        _fail("invalid_private_identity")
    return value


def _date(value):
    if value is None or value == UNVERIFIED:
        return value
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        _fail("invalid_assignment_date")
    try:
        date.fromisoformat(value)
    except ValueError:
        _fail("invalid_assignment_date")
    return value


def _timestamp(value, now):
    try:
        if not isinstance(value, str):
            _fail("invalid_observation_timestamp")
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            _fail("observation_timezone_required")
        age = now - timestamp
        if age < -timedelta(minutes=5) or age > MAX_AGE:
            _fail("stale_or_future_snapshot")
    except (ValueError, TypeError):
        _fail("invalid_or_stale_observation_timestamp")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_private_input_key")
        result[key] = value
    return result


def _project(raw, now):
    packet = json.loads(raw, object_pairs_hook=_pairs)
    _object(packet, {"schema_version", "privacy_salt", "course", "week", "phase", "agenda", "observations"})
    if packet["schema_version"] != SCHEMA:
        _fail("unsupported_private_input_schema")
    if packet["course"] not in ("biology", "forensics", "anatomy"):
        _fail("unsupported_course")
    if not isinstance(packet["week"], str) or not re.fullmatch(r"W(?:0[1-9]|[1-3][0-9]|40)b?", packet["week"]):
        _fail("invalid_week")
    if packet["phase"] not in ("notes", "draft", "lesson", "exam"):
        _fail("explicit_assignment_phase_required")
    salt = packet["privacy_salt"]
    if not isinstance(salt, str) or not re.fullmatch(r"[0-9a-f]{64}", salt):
        _fail("private_random_salt_required")
    key = bytes.fromhex(salt)

    def digest(kind, value):
        return hmac.new(key, (kind + "\0" + value).encode(), hashlib.sha256).hexdigest()

    def alias(kind, value):
        _identity(value)
        return UNVERIFIED if value == UNVERIFIED else "private:" + digest(kind, value)

    def link(value):
        if value == UNVERIFIED:
            return value
        # Preserve exact copy/edit, query and fragment semantics before masking.
        normalized = canonicalize_url(value)
        if normalized.startswith("#"):
            _fail("absolute_lesson_link_required")
        return "https://example.invalid/private-link/" + digest("url", normalized)

    agenda = packet["agenda"]
    _object(agenda, {"class_id", "assignment_id", "revision", "observed_at", "due_date", "deck_url", "schoology_id", "ic_id"})
    _timestamp(agenda["observed_at"], now)
    class_key = alias("class", agenda["class_id"])
    assignment_key = alias("assignment", agenda["assignment_id"])
    targets = {"schoology": alias("schoology", agenda["schoology_id"]), "infinite_campus": alias("infinite_campus", agenda["ic_id"])}
    due = _date(agenda["due_date"])
    desired = DesiredPlatformAssignment(
        AssignmentRecord(assignment_key, class_key, "Saved assignment", None if due == UNVERIFIED else due,
                         UNVERIFIED, external_ids=targets),
        packet["week"], UNVERIFIED, link(agenda["deck_url"]), UNVERIFIED, UNVERIFIED,
        targets["infinite_campus"], alias("agenda_revision", agenda["revision"]),
    )
    observations = packet["observations"]
    if not isinstance(observations, list) or len(observations) > 2:
        _fail("one_assignment_at_most_two_platform_observations")
    rows = []
    for observation in observations:
        _object(observation, {"system", "class_id", "assignment_id", "target_id", "revision", "observed_at", "due_date"}, {"deck_url"})
        system = observation["system"]
        if system not in _FIELDS:
            _fail("unsupported_platform")
        if system == "infinite_campus" and "deck_url" in observation:
            _fail("ic_scope_is_due_date_only")
        _timestamp(observation["observed_at"], now)
        fields = {"due_date": _date(observation["due_date"])}
        if system == "schoology":
            fields["deck_reference"] = link(observation.get("deck_url", UNVERIFIED))
        rows.append(ObservedPlatformAssignment(
            alias("assignment", observation["assignment_id"]), alias("class", observation["class_id"]),
            system, alias(system, observation["target_id"]), alias("platform_revision", observation["revision"]), fields,
        ))
    preview = reconcile_platform_assignments((desired,), tuple(rows), fields_by_system=_FIELDS, require_observed_fields=True)
    receipt = digest("packet", raw.decode("utf-8"))
    changes = []
    for change in preview.changes:
        # Do not mislabel a redacted real snapshot as a synthetic observation.
        evidence = tuple(replace(ref, source_type="saved_" + ref.source_type,
                                 reference="local://private-input/" + receipt + "/" + ref.stable_id)
                         for ref in change.evidence)
        projected = replace(change, evidence=evidence)
        changes.append(replace(projected, stable_id=deterministic_id("private-change", asdict(projected))))
    preview = replace(preview, workflow="private_assignment_snapshot_review", changes=tuple(changes))
    validate_preview(preview)
    result = {
        "schema_version": "private-assignment-review/v1",
        "course": packet["course"], "week": packet["week"], "phase": packet["phase"],
        "receipt": receipt, "preview": asdict(preview),
        "source_status": "saved_snapshot_not_live", "private_resolution_required": True,
        "reachability": UNVERIFIED, "student_access": UNVERIFIED, "publication": UNVERIFIED,
        "network_calls": 0, "source_writes": 0,
    }
    assert_safe_preview(result)
    return result


def preview_private_assignment(private_root, input_path, *, now=None):
    """Return sanitized diffs for one saved assignment; never persist raw data.

    ``now`` is a test clock only. The CLI always uses the current UTC clock.
    Errors deliberately suppress parser/OS exception context and private paths.
    """
    try:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            _fail("review_timezone_required")
        return _project(_read_private_file(private_root, input_path), now)
    except PrivateInputError as error:
        code = str(error)
    except (ValueError, TypeError, KeyError, OSError, RecursionError, OverflowError):
        code = "private_input_rejected"
    # Raise outside the handler so the original exception context cannot leak.
    raise PrivateInputError(code)
