from __future__ import annotations

import json
from pathlib import Path
import unittest

from school_workflow_hub.workflows.feedback_research_proof import (
    REQUIRED_PROMPT_VERSION,
    REQUIRED_RUBRIC_VERSION,
    SyntheticFeedbackResearchInput,
    fixture_inputs,
    result_as_dict,
    run_synthetic_feedback_research_proof,
)


FIXTURES = Path(__file__).parent / "fixtures"


class FeedbackResearchProofTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = json.loads(
            (FIXTURES / "synthetic_feedback_research_proof.json").read_text()
        )
        self.expected = json.loads(
            (FIXTURES / "synthetic_feedback_research_proof_expected.json").read_text()
        )

    def test_happy_fixture_stops_at_teacher_review_with_full_provenance(self) -> None:
        happy, _ = fixture_inputs(self.fixture)
        result = result_as_dict(run_synthetic_feedback_research_proof(happy))
        expected = self.expected["happy"]
        self.assertEqual(result["status"], expected["status"])
        self.assertEqual(result["reason"], expected["reason"])
        self.assertEqual(result["teacher_review_required"], expected["teacher_review_required"])
        self.assertEqual(result["external_writes"], expected["external_writes"])
        self.assertEqual(result["live_model_calls"], expected["live_model_calls"])
        self.assertEqual(result["draft_feedback_summary"]["artifact_kind"], expected["artifact_kind"])
        self.assertEqual(result["draft_feedback_summary"]["delivery_state"], expected["delivery_state"])
        self.assertEqual(result["provenance"], {
            "submission_stable_id": "submission:synthetic:feedback-proof-001",
            "source_reference": "synthetic://feedback-research/proof-001",
            "source_revision": "synthetic-revision:proof-001:v1",
            "synthetic_marker": "fixture-only",
            "prompt_version": REQUIRED_PROMPT_VERSION,
            "rubric_version": REQUIRED_RUBRIC_VERSION,
            "model_identifier": "fake/disabled",
        })

    def test_sensitive_fixture_is_denied_without_artifact_or_calls(self) -> None:
        _, denial = fixture_inputs(self.fixture)
        result = result_as_dict(run_synthetic_feedback_research_proof(denial))
        expected = self.expected["denial"]
        for key in ("status", "reason", "teacher_review_required", "external_writes", "live_model_calls", "draft_feedback_summary"):
            self.assertEqual(result[key], expected[key])
        self.assertEqual(result["provenance"]["prompt_version"], REQUIRED_PROMPT_VERSION)
        self.assertEqual(result["provenance"]["rubric_version"], REQUIRED_RUBRIC_VERSION)

    def test_non_synthetic_source_and_unknown_provenance_fail_closed(self) -> None:
        item = SyntheticFeedbackResearchInput(
            submission_stable_id="submission:synthetic:blocked",
            source_reference="external://not-allowed",
            source_revision="synthetic-revision:blocked:v1",
            synthetic_marker="fixture-only",
            evidence_fingerprint="synthetic-evidence:blocked-v1",
            prompt_version="unknown-prompt",
            rubric_version=REQUIRED_RUBRIC_VERSION,
        )
        result = run_synthetic_feedback_research_proof(item)
        self.assertEqual(result.status, "denied")
        self.assertEqual(result.reason, "invalid_synthetic_fixture")
        self.assertIsNone(result.draft_feedback_summary)
        self.assertEqual(result.external_writes, 0)
        self.assertEqual(result.live_model_calls, 0)
        self.assertEqual(result.provenance, {"model_identifier": "fake/disabled", "input": "withheld"})

        disguised_content = SyntheticFeedbackResearchInput(
            submission_stable_id="submission:synthetic:blocked-two",
            source_reference="synthetic://feedback-research/blocked-two",
            source_revision="synthetic-revision:blocked-two:v1",
            synthetic_marker="fixture-only",
            evidence_fingerprint="student@example.org first response",
            prompt_version=REQUIRED_PROMPT_VERSION,
            rubric_version=REQUIRED_RUBRIC_VERSION,
            contains_sensitive_content=False,
        )
        denied = run_synthetic_feedback_research_proof(disguised_content)
        self.assertEqual(denied.status, "denied")
        self.assertEqual(denied.reason, "invalid_synthetic_fixture")
        self.assertEqual(denied.provenance["input"], "withheld")


if __name__ == "__main__":
    unittest.main()
