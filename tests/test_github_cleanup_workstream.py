from __future__ import annotations

from dataclasses import replace
from datetime import date
import json
from pathlib import Path
import unittest

from school_workflow_hub.workflows.github_cleanup import (
    CleanupDisposition,
    CleanupPlanStatus,
    CleanupPolicy,
    GitHubCleanupItem,
    GitHubItemKind,
    GitHubItemState,
    LocalGitHubCleanupPlanner,
    PROJECT_ITEM_RESTORE_DOC,
    REPOSITORY_RESTORE_DOC,
    plan_synthetic_github_cleanup,
)


FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture() -> tuple[tuple[GitHubCleanupItem, ...], CleanupPolicy]:
    payload = json.loads((FIXTURES / "github_cleanup_manifest.json").read_text())
    policy_payload = payload["policy"]
    policy = CleanupPolicy(
        policy_stable_id=policy_payload["policy_stable_id"],
        as_of=date.fromisoformat(policy_payload["as_of"]),
        retention_days=policy_payload["retention_days"],
        allowlisted_item_ids=tuple(policy_payload["allowlisted_item_ids"]),
        excluded_item_ids=tuple(policy_payload.get("excluded_item_ids", ())),
        protected_item_ids=tuple(policy_payload.get("protected_item_ids", ())),
        active_item_ids=tuple(policy_payload.get("active_item_ids", ())),
    )
    items = tuple(
        GitHubCleanupItem(
            stable_id=item["stable_id"],
            kind=GitHubItemKind(item["kind"]),
            canonical_location=item["canonical_location"],
            state=GitHubItemState(item["state"]),
            last_activity_on=date.fromisoformat(item["last_activity_on"]),
            is_archived=item.get("is_archived", False),
            is_protected=item.get("is_protected", False),
            is_active=item.get("is_active", False),
            is_restorable=item.get("is_restorable", True),
            estimated_storage_bytes=item.get("estimated_storage_bytes", 0),
        )
        for item in payload["items"]
    )
    return items, policy


class GitHubCleanupWorkstreamTestCase(unittest.TestCase):
    def test_fixture_matches_the_checked_in_golden_plan(self) -> None:
        items, policy = _load_fixture()
        plan = plan_synthetic_github_cleanup(items, policy)
        expected = json.loads((FIXTURES / "github_cleanup_expected.json").read_text())

        self.assertEqual(plan.canonical_dict(), expected)

    def test_plan_is_idempotent_for_reordered_identical_input(self) -> None:
        items, policy = _load_fixture()
        first = plan_synthetic_github_cleanup(items, policy)
        second = plan_synthetic_github_cleanup(tuple(reversed(items)), policy)

        self.assertEqual(first.status, CleanupPlanStatus.PLANNED)
        self.assertEqual(first.canonical_dict(), second.canonical_dict())
        proposed_ids = [
            decision.action_stable_id
            for decision in first.decisions
            if decision.disposition is CleanupDisposition.PROPOSE_ARCHIVE
        ]
        self.assertEqual(len(proposed_ids), len(set(proposed_ids)))

    def test_false_positives_are_never_proposed_for_archive(self) -> None:
        items, policy = _load_fixture()
        plan = LocalGitHubCleanupPlanner().plan(items, policy)
        by_id = {decision.stable_id: decision for decision in plan.decisions}

        self.assertEqual(
            by_id["github-project-item:protected"].disposition, CleanupDisposition.BLOCKED
        )
        self.assertIn("protected", by_id["github-project-item:protected"].reasons)
        self.assertEqual(by_id["github-project-item:active"].disposition, CleanupDisposition.BLOCKED)
        self.assertEqual(by_id["github-project-item:recent"].disposition, CleanupDisposition.KEEP)
        self.assertEqual(
            by_id["github-project-item:excluded"].disposition, CleanupDisposition.BLOCKED
        )
        self.assertEqual(
            by_id["github-project-item:not-allowlisted"].disposition, CleanupDisposition.BLOCKED
        )
        self.assertEqual(by_id["github-project-item:open"].disposition, CleanupDisposition.KEEP)
        self.assertEqual(
            by_id["github-project-item:not-restorable"].disposition, CleanupDisposition.BLOCKED
        )
        self.assertEqual(
            by_id["github-project-item:already-archived"].disposition,
            CleanupDisposition.ALREADY_ARCHIVED,
        )
        self.assertEqual(
            by_id["github-project-item:already-archived"].restore_guide.documentation_url,
            PROJECT_ITEM_RESTORE_DOC,
        )
        self.assertEqual(
            [
                decision.stable_id
                for decision in plan.decisions
                if decision.disposition is CleanupDisposition.PROPOSE_ARCHIVE
            ],
            ["github-project-item:eligible", "github-repository:eligible"],
        )

    def test_archive_proposals_are_restorable_and_report_space_conservatively(self) -> None:
        items, policy = _load_fixture()
        plan = plan_synthetic_github_cleanup(items, policy)
        proposed = {
            decision.stable_id: decision
            for decision in plan.decisions
            if decision.disposition is CleanupDisposition.PROPOSE_ARCHIVE
        }

        project = proposed["github-project-item:eligible"]
        repository = proposed["github-repository:eligible"]
        self.assertEqual(project.restore_guide.documentation_url, PROJECT_ITEM_RESTORE_DOC)
        self.assertEqual(repository.restore_guide.documentation_url, REPOSITORY_RESTORE_DOC)
        self.assertEqual(plan.space_impact.project_view_slots_reduced, 1)
        self.assertEqual(plan.space_impact.reviewable_storage_bytes, 2060)
        self.assertEqual(plan.space_impact.guaranteed_storage_reclaimed_bytes, 0)

    def test_invalid_manifest_fails_closed_without_a_proposal_or_external_call(self) -> None:
        items, policy = _load_fixture()
        invalid_items = items + (replace(items[0], canonical_location="example-org/duplicate"),)

        failed = plan_synthetic_github_cleanup(invalid_items, policy)

        self.assertEqual(failed.status, CleanupPlanStatus.FAILED_VALIDATION)
        self.assertEqual(failed.failure_code, "invalid_manifest_or_policy")
        self.assertEqual(failed.decisions, ())
        self.assertEqual(failed.external_calls, 0)
        self.assertEqual(failed.external_writes, 0)

    def test_future_source_timestamp_fails_closed(self) -> None:
        items, policy = _load_fixture()
        invalid_items = (replace(items[0], last_activity_on=date(2026, 9, 14)),) + items[1:]

        failed = plan_synthetic_github_cleanup(invalid_items, policy)

        self.assertEqual(failed.status, CleanupPlanStatus.FAILED_VALIDATION)
        self.assertFalse(failed.decisions)

    def test_explicit_allowlist_is_required(self) -> None:
        with self.assertRaises(ValueError):
            CleanupPolicy(
                policy_stable_id="github-cleanup-policy:missing-scope",
                as_of=date(2026, 9, 13),
                retention_days=30,
                allowlisted_item_ids=(),
            )

    def test_planner_has_no_executor_and_never_records_an_external_write(self) -> None:
        items, policy = _load_fixture()
        plan = LocalGitHubCleanupPlanner().plan(items, policy)

        self.assertFalse(hasattr(LocalGitHubCleanupPlanner(), "execute"))
        self.assertEqual(plan.external_calls, 0)
        self.assertEqual(plan.external_writes, 0)
        rendered = json.dumps(plan.canonical_dict(), sort_keys=True).lower()
        self.assertNotIn('"operation": "delete"', rendered)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
