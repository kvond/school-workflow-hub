from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import unittest

from school_workflow_hub.workflows.turnin_routing import (
    AssignmentTurnInMaster,
    DownstreamSystem,
    LocalTurnInRoutingPlanner,
    ObservedDownstreamTurnInRoute,
    TurnInFolderManifest,
    TurnInRoutingInput,
    plan_synthetic_turnin_routing,
)


FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> TurnInRoutingInput:
    payload = json.loads((FIXTURES / name).read_text())
    masters = tuple(AssignmentTurnInMaster(**record) for record in payload["assignment_master"])
    manifests = tuple(TurnInFolderManifest(**record) for record in payload["folder_manifests"])
    routes = tuple(
        ObservedDownstreamTurnInRoute(
            system=DownstreamSystem(record["system"]),
            class_stable_id=record["class_stable_id"],
            assignment_stable_id=record["assignment_stable_id"],
            downstream_assignment_target_stable_id=record["downstream_assignment_target_stable_id"],
            turn_in_folder_stable_id=record["turn_in_folder_stable_id"],
            form_file_stable_id=record["form_file_stable_id"],
            slide_copy_source_file_stable_id=record["slide_copy_source_file_stable_id"],
            observation_revision_stable_id=record["observation_revision_stable_id"],
        )
        for record in payload["observed_downstream_routes"]
    )
    return TurnInRoutingInput(
        assignment_master=masters,
        folder_manifests=manifests,
        observed_downstream_routes=routes,
    )


class TurnInRoutingProofTests(unittest.TestCase):
    def test_happy_fixture_matches_checked_in_reversible_dry_run_golden_output(self) -> None:
        result = plan_synthetic_turnin_routing(_load_fixture("turnin_routing_happy.json"))
        expected = json.loads((FIXTURES / "turnin_routing_happy_expected.json").read_text())

        self.assertEqual(result.canonical_dict(), expected)

    def test_happy_fixture_preserves_stable_ids_and_exposes_forward_and_rollback_diffs(self) -> None:
        result = plan_synthetic_turnin_routing(_load_fixture("turnin_routing_happy.json"))
        plans = {plan.system: plan for plan in result.plans}

        self.assertEqual(set(plans), {DownstreamSystem.SCHOOLOGY, DownstreamSystem.GRADEBOOK})
        schoology = plans[DownstreamSystem.SCHOOLOGY]
        gradebook = plans[DownstreamSystem.GRADEBOOK]
        self.assertEqual(
            schoology.assignment_stable_id, "assignment:synthetic-forensics:evidence-logic"
        )
        self.assertEqual(
            schoology.downstream_assignment_target_stable_id,
            "schoology-assignment:synthetic:evidence-logic",
        )
        self.assertEqual([diff.field_name for diff in schoology.diffs], ["form_file_stable_id"])
        self.assertEqual(
            [diff.field_name for diff in gradebook.diffs],
            ["turn_in_folder_stable_id", "slide_copy_source_file_stable_id"],
        )
        for plan in result.plans:
            self.assertEqual(len(plan.diffs), len(plan.rollback_diffs))
            for forward, rollback in zip(plan.diffs, plan.rollback_diffs):
                self.assertEqual(forward.field_name, rollback.field_name)
                self.assertEqual(forward.observed_stable_id, rollback.expected_stable_id)
                self.assertEqual(forward.expected_stable_id, rollback.observed_stable_id)
            self.assertTrue(plan.reversible)
            self.assertEqual(plan.lifecycle_state, "dry_run_only")
            self.assertFalse(plan.external_write_performed)

    def test_missing_and_ambiguous_downstream_targets_are_denied_without_a_plan(self) -> None:
        result = plan_synthetic_turnin_routing(_load_fixture("turnin_routing_denial.json"))

        self.assertEqual(result.plans, ())
        self.assertEqual(
            {(issue.code, issue.system) for issue in result.issues},
            {
                ("ambiguous_downstream_target", DownstreamSystem.SCHOOLOGY),
                ("missing_downstream_target", DownstreamSystem.GRADEBOOK),
            },
        )
        self.assertEqual(result.external_writes, 0)

    def test_folder_form_or_slide_manifest_conflict_blocks_every_downstream_plan(self) -> None:
        fixture = _load_fixture("turnin_routing_happy.json")
        bad_manifest = replace(
            fixture.folder_manifests[0], form_file_stable_id="google-form:synthetic:conflicting"
        )
        result = plan_synthetic_turnin_routing(
            TurnInRoutingInput(
                assignment_master=fixture.assignment_master,
                folder_manifests=(bad_manifest,),
                observed_downstream_routes=fixture.observed_downstream_routes,
            )
        )

        self.assertEqual(result.plans, ())
        self.assertEqual([issue.code for issue in result.issues], ["folder_form_slide_manifest_conflict"])

    def test_mismatched_target_is_not_retargeted_or_updated(self) -> None:
        fixture = _load_fixture("turnin_routing_happy.json")
        bad_schoology = replace(
            fixture.observed_downstream_routes[0],
            downstream_assignment_target_stable_id="schoology-assignment:synthetic:wrong-target",
        )
        result = plan_synthetic_turnin_routing(
            TurnInRoutingInput(
                assignment_master=fixture.assignment_master,
                folder_manifests=fixture.folder_manifests,
                observed_downstream_routes=(bad_schoology,) + fixture.observed_downstream_routes[1:],
            )
        )

        self.assertEqual([plan.system for plan in result.plans], [DownstreamSystem.GRADEBOOK])
        self.assertEqual(
            [(issue.code, issue.system) for issue in result.issues],
            [("downstream_target_conflict", DownstreamSystem.SCHOOLOGY)],
        )

    def test_output_is_idempotent_when_the_input_record_order_changes(self) -> None:
        fixture = _load_fixture("turnin_routing_happy.json")
        first = plan_synthetic_turnin_routing(fixture)
        second = plan_synthetic_turnin_routing(
            TurnInRoutingInput(
                assignment_master=tuple(reversed(fixture.assignment_master)),
                folder_manifests=tuple(reversed(fixture.folder_manifests)),
                observed_downstream_routes=tuple(reversed(fixture.observed_downstream_routes)),
            )
        )

        self.assertEqual(first.canonical_dict(), second.canonical_dict())

    def test_planner_has_no_executor_or_external_side_effect_surface(self) -> None:
        result = LocalTurnInRoutingPlanner().plan(_load_fixture("turnin_routing_happy.json"))

        self.assertFalse(hasattr(LocalTurnInRoutingPlanner(), "execute"))
        self.assertEqual(result.browser_calls, 0)
        self.assertEqual(result.model_calls, 0)
        self.assertEqual(result.external_calls, 0)
        self.assertEqual(result.external_writes, 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
