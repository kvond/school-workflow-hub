"""Offline desired-state comparison and occurrence-level platform link audit.

Inputs are declared manifests and synthetic/approved saved observations, never
live platform reads. URL comparison does not establish reachability or student
access. Every result is a preview; this module contains no network or executor.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import ipaddress
import json
from pathlib import Path
import re
from typing import Any, Mapping, Optional, Tuple
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from school_workflow_hub.core.models import AssignmentRecord
from school_workflow_hub.core.runs import deterministic_id
from school_workflow_hub.core.workflow import EvidenceRef, PreviewChange, WorkflowIssue, WorkflowPreview, assert_safe_preview, validate_preview


UNVERIFIED = "UNVERIFIED"
SCHEMA_VERSION = "platform-manifest/v1"
LINK_SURFACES = frozenset({
    "assignment", "schoology_page", "ic_assignment", "slide_text", "slide_image",
    "slide_shape", "speaker_notes", "activity", "video", "qr", "internal_slide",
})
_ACTION_WORDS = frozenset({"delete", "remove", "submit", "logout", "unsubscribe", "purchase", "checkout"})
_SECRET_KEYS = frozenset({"token", "access_token", "refresh_token", "password", "secret", "key", "auth", "authorization", "signature", "sig", "code"})


class UnsafeLink(ValueError):
    """A link cannot enter this passive comparison; its raw value is not logged."""


def canonicalize_url(value: str) -> str:
    """Normalize passive URLs conservatively, preserving copy/edit and slide IDs.

    Tracking query parameters are ignored. Arbitrary query values, meaningful
    fragments and path case remain significant. Redirects are never followed.
    """
    if not isinstance(value, str) or not value or value != value.strip():
        raise UnsafeLink("invalid_url")
    if any(ord(char) < 32 for char in value) or "\\" in value:
        raise UnsafeLink("unsafe_url_characters")
    if re.fullmatch(r"#slide=id\.[A-Za-z0-9_-]+", value):
        return value
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError as error:
        raise UnsafeLink("invalid_url") from error
    if parsed.scheme.lower() != "https" or not host or parsed.username or parsed.password:
        raise UnsafeLink("unsafe_url_scheme_or_authority")
    if "%" in host or not host.isascii():
        raise UnsafeLink("unsupported_hostname")
    if host == "answerableteaching.com" or host.endswith(".answerableteaching.com"):
        raise UnsafeLink("prohibited_destination")
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal")) or "." not in host:
        raise UnsafeLink("private_destination")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        raise UnsafeLink("ip_destination")
    decoded_path = unquote(parsed.path).lower()
    if set(filter(None, re.split(r"[/._-]", decoded_path))) & _ACTION_WORDS:
        raise UnsafeLink("action_destination")
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    normalized = []
    for key, val in pairs:
        lower_key = key.lower()
        if lower_key in _SECRET_KEYS or any(part in lower_key for part in ("token", "password", "secret")):
            raise UnsafeLink("sensitive_url_parameter")
        if lower_key in {"action", "do", "operation"} and val.lower() in _ACTION_WORDS:
            raise UnsafeLink("action_destination")
        # A nested destination could hide a prohibited/action link. It is not
        # expanded or silently treated as safe by this offline comparator.
        if lower_key in {"url", "redirect", "redirect_uri", "redirect_url", "next", "continue"}:
            raise UnsafeLink("redirect_destination_unverified")
        if not lower_key.startswith("utm_") and lower_key not in {"fbclid", "gclid"}:
            normalized.append((key, val))
    authority = host if port in (None, 443) else f"{host}:{port}"
    return urlunsplit(("https", authority, parsed.path or "/", urlencode(sorted(normalized)), parsed.fragment))


def _identity(value: str, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or any(c.isspace() for c in value):
        raise ValueError(f"{field} must be a stable key or UNVERIFIED")
    return value


@dataclass(frozen=True)
class DesiredPlatformAssignment:
    assignment: AssignmentRecord
    week_key: str
    marking_period: str
    deck_reference: str
    schoology_destination_id: str
    expected_publication: str
    ic_linkage_id: str
    revision: str

    def __post_init__(self) -> None:
        for name in ("week_key", "marking_period", "schoology_destination_id", "ic_linkage_id", "revision"):
            _identity(getattr(self, name), name)
        _identity(self.assignment.stable_id, "assignment_key")
        _identity(self.assignment.class_stable_id, "class_stable_id")
        if self.expected_publication not in {"draft", "published", "unpublished", UNVERIFIED}:
            raise ValueError("unsupported publication state")
        if self.deck_reference != UNVERIFIED:
            canonicalize_url(self.deck_reference)


@dataclass(frozen=True)
class DesiredCourseManifest:
    course_key: str
    class_stable_id: str
    revision: str
    course_structure: Tuple[str, ...]
    assignments: Tuple[DesiredPlatformAssignment, ...]


def load_manifest(path: Path) -> DesiredCourseManifest:
    """Load a human-editable template; unknown live IDs remain explicit."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    assert_safe_preview(raw)
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported platform manifest schema")
    for field in ("course_key", "class_stable_id", "revision"):
        _identity(raw[field], field)
    assignments = []
    for item in raw["assignments"]:
        for field in ("assignment_key", "deck_file_id", "schoology_assignment_id", "ic_linkage_id"):
            _identity(item[field], field)
        assignments.append(DesiredPlatformAssignment(
            assignment=AssignmentRecord(
                stable_id=item["assignment_key"], class_stable_id=raw["class_stable_id"],
                title=item["title"], intended_due_date=item.get("intended_due_date"),
                deck_file_id=item["deck_file_id"],
                external_ids={"schoology": item["schoology_assignment_id"], "infinite_campus": item["ic_linkage_id"]},
            ),
            week_key=item["week_key"], marking_period=item["marking_period"],
            deck_reference=item["deck_reference"], schoology_destination_id=item["schoology_destination_id"],
            expected_publication=item["expected_publication"], ic_linkage_id=item["ic_linkage_id"],
            revision=raw["revision"],
        ))
    return DesiredCourseManifest(raw["course_key"], raw["class_stable_id"], raw["revision"], tuple(raw["course_structure"]), tuple(assignments))


@dataclass(frozen=True)
class ObservedPlatformAssignment:
    assignment_key: str
    class_stable_id: str
    system: str
    target_id: str
    revision: str
    fields: Mapping[str, Any]
    login_status: str = "available"

    def __post_init__(self) -> None:
        if self.system not in {"schoology", "infinite_campus"}:
            raise ValueError("unsupported platform")


def reconcile_platform_assignments(
    desired: Tuple[DesiredPlatformAssignment, ...], current: Tuple[ObservedPlatformAssignment, ...],
    *, fields_by_system: Optional[Mapping[str, Tuple[str, ...]]] = None,
    require_observed_fields: bool = False,
) -> WorkflowPreview:
    """Return reversible field diffs only for unique, verified identities."""
    if fields_by_system is not None:
        allowed = {
            "schoology": {"title", "due_date", "deck_reference", "week_key", "marking_period", "destination_id", "publication"},
            "infinite_campus": {"title", "due_date", "deck_reference"},
        }
        if set(fields_by_system) != set(allowed) or any(
            not isinstance(fields, tuple) or not fields or len(set(fields)) != len(fields)
            or not set(fields).issubset(allowed[system])
            for system, fields in fields_by_system.items()
        ):
            raise ValueError("invalid platform field scope")
    assert_safe_preview({"desired": [asdict(row) for row in desired], "current": [asdict(row) for row in current]})
    changes = []
    issues = []
    desired_keys = [(d.assignment.class_stable_id, d.assignment.stable_id) for d in desired]
    for entry in sorted(desired, key=lambda d: (d.assignment.class_stable_id, d.assignment.stable_id)):
        assignment = entry.assignment
        key = (assignment.class_stable_id, assignment.stable_id)
        if desired_keys.count(key) != 1:
            issues.append(WorkflowIssue("duplicate_desired_assignment", "error", assignment.stable_id))
            continue
        if UNVERIFIED in key:
            issues.append(WorkflowIssue("unverified_assignment_identity", "error", assignment.stable_id))
            continue
        for system in ("schoology", "infinite_campus"):
            target_id = assignment.external_ids.get(system, UNVERIFIED)
            rows = [r for r in current if (r.class_stable_id, r.assignment_key, r.system) == (*key, system)]
            if target_id == UNVERIFIED:
                issues.append(WorkflowIssue(f"unverified_{system}_target", "error", assignment.stable_id))
                continue
            if system == "infinite_campus" and target_id != entry.ic_linkage_id:
                issues.append(WorkflowIssue("conflicting_infinite_campus_linkage", "error", assignment.stable_id))
                continue
            if sum(d.assignment.external_ids.get(system) == target_id for d in desired) > 1:
                issues.append(WorkflowIssue(f"duplicate_{system}_target_binding", "error", assignment.stable_id))
                continue
            if len(rows) != 1:
                issues.append(WorkflowIssue(f"{'missing' if not rows else 'duplicate'}_{system}_target", "error", assignment.stable_id))
                continue
            observed = rows[0]
            if observed.login_status != "available":
                issues.append(WorkflowIssue(f"{system}_login_unavailable", "error", assignment.stable_id, True))
                continue
            if observed.target_id != target_id:
                issues.append(WorkflowIssue(f"incorrect_{system}_target", "error", assignment.stable_id))
                continue
            if UNVERIFIED in {entry.revision, observed.revision}:
                issues.append(WorkflowIssue("unverified_source_revision", "error", assignment.stable_id))
                continue
            expected = {"title": assignment.title, "due_date": assignment.intended_due_date, "deck_reference": entry.deck_reference}
            if system == "schoology":
                expected.update(week_key=entry.week_key, marking_period=entry.marking_period, destination_id=entry.schoology_destination_id, publication=entry.expected_publication)
            evidence = (
                EvidenceRef("desired_manifest", assignment.stable_id, entry.revision, f"synthetic://manifest/{assignment.stable_id}"),
                EvidenceRef("platform_snapshot", target_id, observed.revision, f"synthetic://snapshot/{target_id}"),
            )
            for field, proposed in sorted(expected.items()):
                if fields_by_system is not None and field not in fields_by_system[system]:
                    continue
                if proposed in (None, UNVERIFIED):
                    issues.append(WorkflowIssue(f"unverified_{system}_{field}", "warning", assignment.stable_id))
                    continue
                old = observed.fields.get(field)
                if require_observed_fields and (field not in observed.fields or old == UNVERIFIED):
                    issues.append(WorkflowIssue(f"unverified_observed_{system}_{field}", "warning", assignment.stable_id))
                    continue
                if field == "deck_reference" and old:
                    try:
                        if canonicalize_url(old) == canonicalize_url(proposed):
                            continue
                    except UnsafeLink:
                        issues.append(WorkflowIssue("unsafe_current_deck_link", "error", assignment.stable_id))
                        continue
                if old != proposed:
                    identity = {"key": key, "system": system, "target": target_id, "field": field, "old": old, "new": proposed, "evidence": [asdict(e) for e in evidence]}
                    changes.append(PreviewChange(
                        stable_id=deterministic_id("platform-change", identity), assignment_key=assignment.stable_id,
                        target_system=system, target_id=target_id, field=field, old_value=old, proposed_value=proposed,
                        evidence=evidence, confidence=1.0, reversible=True,
                    ))
    for row in current:
        if (row.class_stable_id, row.assignment_key) not in desired_keys:
            issues.append(WorkflowIssue("stale_or_wrong_course_observation", "warning", row.assignment_key))
    preview = WorkflowPreview("schoology_gradebook", tuple(sorted(changes, key=lambda c: c.stable_id)), tuple(sorted(set(issues), key=lambda i: (i.assignment_key or "", i.code))))
    validate_preview(preview)
    return preview


@dataclass(frozen=True)
class LinkOccurrence:
    """One inspected location, including notes, images, QR and slide jumps.

    ``location`` is an exact stable slide/element or assignment-field key.
    Repetition at different locations is intentional coverage, not a duplicate.
    """
    occurrence_id: str
    assignment_key: str
    surface: str
    location: str
    destination: str
    revision: str

    def __post_init__(self) -> None:
        if self.surface not in LINK_SURFACES:
            raise ValueError("unsupported link surface")
        for field in ("occurrence_id", "assignment_key", "location", "revision"):
            _identity(getattr(self, field), field)


@dataclass(frozen=True)
class LinkAuditRecord:
    occurrence_id: str
    assignment_key: str
    surface: str
    location: str
    status: str
    expected_destination: Optional[str]
    observed_destination: Optional[str]
    revision: str
    reason: str
    access_status: str = "not_tested_offline"


@dataclass(frozen=True)
class PlatformLinkAudit:
    occurrences: Tuple[LinkAuditRecord, ...]
    preview: WorkflowPreview
    network_calls: int = 0


def audit_link_occurrences(expected: Tuple[LinkOccurrence, ...], observed: Tuple[LinkOccurrence, ...]) -> PlatformLinkAudit:
    """Compare all declared occurrences without fetching any destination."""
    groups = {}
    for row in observed:
        groups.setdefault((row.assignment_key, row.surface, row.location), []).append(row)
    expectations = {}
    for row in expected:
        expectations.setdefault((row.assignment_key, row.surface, row.location), []).append(row)
    records = []
    issues = []
    for key in sorted(set(groups) | set(expectations)):
        candidates = sorted(groups.get(key, []), key=lambda row: row.occurrence_id)
        wants = expectations.get(key, [])
        for row in candidates or wants:
            want = wants[0] if len(wants) == 1 else None
            expected_url = observed_url = None
            unsafe = False
            try:
                expected_url = canonicalize_url(want.destination) if want else None
                observed_url = canonicalize_url(row.destination) if candidates else None
            except UnsafeLink:
                unsafe = True
            if unsafe:
                status, reason = "unsafe", "destination_rejected_without_fetch"
                expected_url = observed_url = None
            elif len(wants) > 1 or len(candidates) > 1:
                status, reason = "duplicate", "ambiguous_occurrence_location"
            elif not candidates:
                status, reason = "missing", "expected_occurrence_absent"
            elif not wants:
                status, reason = "stale", "occurrence_not_in_desired_inventory"
            elif UNVERIFIED in {want.revision, row.revision}:
                status, reason = "unverified", "revision_unverified"
            elif want.revision != row.revision:
                status, reason = "stale", "observation_revision_outdated"
            elif expected_url != observed_url:
                status, reason = "incorrect", "destination_mismatch"
            else:
                status, reason = "correct", "declared_destination_matches"
            records.append(LinkAuditRecord(row.occurrence_id, *key, status, expected_url, observed_url, row.revision, reason))
            if status != "correct":
                issues.append(WorkflowIssue(f"link_{status}", "error" if status in {"unsafe", "duplicate"} else "warning", row.assignment_key))
    ordered = tuple(sorted(records, key=lambda r: (r.assignment_key, r.surface, r.location, r.occurrence_id)))
    assert_safe_preview([asdict(record) for record in ordered])
    preview = WorkflowPreview("platform_link_audit", issues=tuple(sorted(set(issues), key=lambda i: (i.assignment_key or "", i.code))))
    return PlatformLinkAudit(ordered, preview)
