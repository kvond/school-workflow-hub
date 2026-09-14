"""Synthetic-only proof of a guarded feedback research path.

This isolated module deliberately does not use the existing student-work
workflow.  It accepts declared synthetic metadata, rejects sensitive content,
creates a local draft-summary artifact, and stops at teacher review.  It has no
model, network, browser, database, grade, or external-action dependency.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Dict, Mapping, Tuple


SYNTHETIC_SOURCE_PREFIX = "synthetic://"
REQUIRED_PROMPT_VERSION = "synthetic-feedback-v1"
REQUIRED_RUBRIC_VERSION = "synthetic-rubric-v1"
_SYNTHETIC_SUBMISSION_ID = re.compile(r"^submission:synthetic:[a-z0-9_-]+$")
_SYNTHETIC_REFERENCE = re.compile(r"^synthetic://[a-z0-9/_-]+$")
_SYNTHETIC_REVISION = re.compile(r"^synthetic-revision:[a-z0-9:_-]+$")
_SYNTHETIC_FINGERPRINT = re.compile(r"^synthetic-evidence:[a-z0-9:_-]+$")


@dataclass(frozen=True)
class SyntheticFeedbackResearchInput:
    """Non-student fixture input for the feedback research proof."""

    submission_stable_id: str
    source_reference: str
    source_revision: str
    synthetic_marker: str
    evidence_fingerprint: str
    prompt_version: str
    rubric_version: str
    contains_sensitive_content: bool = False


@dataclass(frozen=True)
class FeedbackResearchProofResult:
    """A local-only outcome; never student feedback or a grading decision."""

    status: str
    reason: str
    provenance: Dict[str, str]
    draft_feedback_summary: Dict[str, str] | None
    teacher_review_required: bool
    external_writes: int = 0
    live_model_calls: int = 0


def _require_match(value: str, pattern: re.Pattern[str], field: str) -> None:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError("{0} must be a narrowly formatted synthetic identifier".format(field))


def _provenance(item: SyntheticFeedbackResearchInput) -> Dict[str, str]:
    return {
        "submission_stable_id": item.submission_stable_id,
        "source_reference": item.source_reference,
        "source_revision": item.source_revision,
        "synthetic_marker": item.synthetic_marker,
        "prompt_version": item.prompt_version,
        "rubric_version": item.rubric_version,
        "model_identifier": "fake/disabled",
    }


def _validate_structure(item: SyntheticFeedbackResearchInput) -> None:
    _require_match(item.submission_stable_id, _SYNTHETIC_SUBMISSION_ID, "submission_stable_id")
    _require_match(item.source_reference, _SYNTHETIC_REFERENCE, "source_reference")
    _require_match(item.source_revision, _SYNTHETIC_REVISION, "source_revision")
    _require_match(item.evidence_fingerprint, _SYNTHETIC_FINGERPRINT, "evidence_fingerprint")
    if item.synthetic_marker != "fixture-only":
        raise ValueError("synthetic_marker must be fixture-only")
    if not isinstance(item.contains_sensitive_content, bool):
        raise ValueError("contains_sensitive_content must be boolean")


def run_synthetic_feedback_research_proof(
    item: SyntheticFeedbackResearchInput,
) -> FeedbackResearchProofResult:
    """Produce a review-only synthetic draft or deny unsafe input.

    A caller must explicitly declare a synthetic source and must never supply
    sensitive content. The input accepts only compact synthetic identifiers and
    a synthetic evidence fingerprint—not raw student text or a free-text
    summary—so a false declaration cannot echo content into the artifact. The
    happy path produces a generic teacher-facing summary and cannot execute.
    """

    try:
        _validate_structure(item)
    except ValueError:
        # Do not copy malformed caller values into provenance merely to explain
        # a denial: they could be real identifiers or raw student text.
        return FeedbackResearchProofResult(
            status="denied",
            reason="invalid_synthetic_fixture",
            provenance={"model_identifier": "fake/disabled", "input": "withheld"},
            draft_feedback_summary=None,
            teacher_review_required=True,
        )
    provenance = _provenance(item)
    synthetic_source = item.source_reference.startswith(SYNTHETIC_SOURCE_PREFIX)
    correct_versions = (
        item.prompt_version == REQUIRED_PROMPT_VERSION
        and item.rubric_version == REQUIRED_RUBRIC_VERSION
    )
    if item.contains_sensitive_content:
        return FeedbackResearchProofResult(
            status="denied",
            reason="sensitive_content_declared",
            provenance=provenance,
            draft_feedback_summary=None,
            teacher_review_required=True,
        )
    if not synthetic_source:
        # Kept as a defensive branch even though structural validation already
        # requires a synthetic URI.
        return FeedbackResearchProofResult(
            status="denied",
            reason="non_synthetic_source",
            provenance=provenance,
            draft_feedback_summary=None,
            teacher_review_required=True,
        )
    if not correct_versions:
        return FeedbackResearchProofResult(
            status="denied",
            reason="unapproved_prompt_or_rubric_version",
            provenance=provenance,
            draft_feedback_summary=None,
            teacher_review_required=True,
        )

    return FeedbackResearchProofResult(
        status="teacher_review_required",
        reason="synthetic_draft_only",
        provenance=provenance,
        draft_feedback_summary={
            "artifact_kind": "synthetic_feedback_summary",
            "summary": "Synthetic evidence is ready for teacher review; no model feedback or grade proposal was generated.",
            "evidence_fingerprint": item.evidence_fingerprint,
            "delivery_state": "not_delivered",
        },
        teacher_review_required=True,
    )


def result_as_dict(result: FeedbackResearchProofResult) -> Dict[str, Any]:
    """Stable serializable representation for deterministic fixture checks."""

    return asdict(result)


def fixture_inputs(payload: Mapping[str, Mapping[str, Any]]) -> Tuple[
    SyntheticFeedbackResearchInput, SyntheticFeedbackResearchInput
]:
    """Load the two declared fixture cases without accepting arbitrary input."""

    return (
        SyntheticFeedbackResearchInput(**dict(payload["happy"])),
        SyntheticFeedbackResearchInput(**dict(payload["denial"])),
    )
