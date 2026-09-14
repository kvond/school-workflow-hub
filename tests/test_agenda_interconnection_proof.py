from __future__ import annotations

from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import unittest

from school_workflow_hub.core.actions import (
    attempt_external_execution,
    get_action,
    validate_action,
)
from school_workflow_hub.core.database import initialize_database
from school_workflow_hub.core.permissions import PermissionDenied, PermissionPolicy
from school_workflow_hub.core.sources import record_source_revision
from school_workflow_hub.workflows.agenda_interconnection import (
    load_synthetic_agenda_interconnection_fixture,
    render_agenda_interconnection_proof,
    run_synthetic_agenda_interconnection,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class AgendaInterconnectionProofTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.connection = initialize_database(Path(self.tempdir.name) / "proof.sqlite3")
        self.addCleanup(self.connection.close)

    def run_fixture(self, fixture_name: str):
        fixture = load_synthetic_agenda_interconnection_fixture(FIXTURES / fixture_name)
        return fixture, run_synthetic_agenda_interconnection(self.connection, fixture)

    def assert_golden(self, fixture_name: str, expected_name: str):
        _, result = self.run_fixture(fixture_name)
        expected = json.loads((FIXTURES / expected_name).read_text(encoding="utf-8"))
        self.assertEqual(json.loads(render_agenda_interconnection_proof(result)), expected)
        return result

    def test_happy_fixture_is_exactly_revision_bound_and_proposes_three_local_changes(self) -> None:
        result = self.assert_golden(
            "agenda_interconnection_happy.json",
            "agenda_interconnection_happy_expected.json",
        )

        self.assertEqual(result.outcome, "proposed")
        self.assertEqual(result.issues, ())
        self.assertEqual(
            [proposal.target_system for proposal in result.proposals],
            ["assignment_folder", "schoology", "infinite_campus"],
        )
        self.assertEqual(
            [proposal.state for proposal in result.proposals], ["proposed", "proposed", "proposed"]
        )
        self.assertTrue(
            all(proposal.source_revision_id == result.normalized_change.agenda_revision_id for proposal in result.proposals)
        )
        self.assertTrue(
            all(("assignment_route", result.normalized_change.routing_revision_id) in proposal.source_dependencies
                for proposal in result.proposals)
        )
        self.assertTrue(all(not proposal.external_write_performed for proposal in result.proposals))
        run = self.connection.execute(
            "SELECT status, external_writes, live_model_calls FROM runs WHERE id = ?", (result.run_id,)
        ).fetchone()
        self.assertEqual(dict(run), {"status": "completed", "external_writes": 0, "live_model_calls": 0})

    def test_conflict_fixture_is_denied_without_any_proposed_action(self) -> None:
        result = self.assert_golden(
            "agenda_interconnection_conflict.json",
            "agenda_interconnection_conflict_expected.json",
        )

        self.assertEqual(result.outcome, "denied")
        self.assertEqual(result.proposals, ())
        self.assertEqual([issue.code for issue in result.issues], ["schoology_target_conflict"])
        count = self.connection.execute("SELECT COUNT(*) AS total FROM proposed_actions").fetchone()["total"]
        self.assertEqual(count, 0)

    def test_happy_fixture_actions_are_hard_denied_for_external_execution(self) -> None:
        _, result = self.run_fixture("agenda_interconnection_happy.json")
        action_id = result.proposals[0].action_id
        validate_action(self.connection, action_id)

        with self.assertRaises(PermissionDenied):
            attempt_external_execution(
                self.connection,
                action_id,
                PermissionPolicy.from_allowed(["external.update"]),
            )

        action = get_action(self.connection, action_id)
        self.assertEqual(action["state"], "proposed")
        self.assertEqual(action["review_status"], "validated")

    def test_assignment_route_revision_invalidates_every_dependent_proposal(self) -> None:
        fixture, result = self.run_fixture("agenda_interconnection_happy.json")
        revised_route = replace(
            fixture.assignment_route,
            revision_label="assignment-route-rev-synthetic-2026-09-13-c",
        )
        record_source_revision(
            self.connection,
            "assignment_route",
            revised_route.assignment_stable_id,
            asdict(revised_route),
            "synthetic://assignment-route/{}".format(revised_route.assignment_stable_id),
            result.run_id,
        )

        self.assertEqual(
            [get_action(self.connection, proposal.action_id)["state"] for proposal in result.proposals],
            ["superseded", "superseded", "superseded"],
        )

    def test_non_synthetic_input_is_refused_before_any_local_ledger_record(self) -> None:
        fixture = load_synthetic_agenda_interconnection_fixture(
            FIXTURES / "agenda_interconnection_happy.json"
        )
        with self.assertRaises(ValueError):
            run_synthetic_agenda_interconnection(
                self.connection,
                replace(fixture, synthetic_marker="not-a-fixture"),
            )

        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) AS total FROM runs").fetchone()["total"], 0
        )
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) AS total FROM source_revisions").fetchone()["total"],
            0,
        )


if __name__ == "__main__":
    unittest.main()
