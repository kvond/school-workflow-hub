from __future__ import annotations

from dataclasses import replace
import unittest

from school_workflow_hub.workflows.agenda_assignment import (
    AgendaAssignmentInput,
    AgendaLessonRevision,
    AssignmentMasterRevision,
    ObservedInfiniteCampusRevision,
    plan_agenda_assignment_updates,
    run_synthetic_agenda_assignment_example,
    synthetic_agenda_assignment_example,
)


class AgendaAssignmentWorkstreamContractTests(unittest.TestCase):
    def test_synthetic_example_proposes_local_ic_date_update_only(self) -> None:
        result = run_synthetic_agenda_assignment_example()

        self.assertEqual(result.issues, ())
        self.assertEqual(len(result.plans), 1)
        plan = result.plans[0]
        self.assertEqual(plan.assignment_stable_id, "assignment:synthetic-biology-a4:energy-intro")
        self.assertEqual(plan.deck_file_id, "synthetic-deck-energy-intro")
        self.assertEqual(plan.form_file_id, "synthetic-form-energy-intro")
        self.assertEqual(plan.intended_agenda_due_date, "2026-09-16")
        self.assertEqual(plan.observed_ic_due_date, "2026-09-15")
        self.assertEqual(plan.proposed_ic_due_date, "2026-09-16")
        self.assertEqual(plan.lifecycle_state, "plan_only")
        self.assertFalse(plan.external_write_performed)
        self.assertEqual(plan.required_permission, "external.update")

    def test_matching_explicit_dates_produce_no_plan(self) -> None:
        fixture = synthetic_agenda_assignment_example()
        observed = replace(
            fixture.observed_infinite_campus[0], observed_due_date="2026-09-16"
        )
        result = plan_agenda_assignment_updates(
            AgendaAssignmentInput(
                agenda_lessons=fixture.agenda_lessons,
                assignment_master=fixture.assignment_master,
                observed_infinite_campus=(observed,),
            )
        )

        self.assertEqual(result.plans, ())
        self.assertEqual(result.issues, ())

    def test_file_routing_conflict_blocks_proposal(self) -> None:
        fixture = synthetic_agenda_assignment_example()
        bad_master = replace(fixture.assignment_master[0], deck_file_id="wrong-deck-file-id")
        result = plan_agenda_assignment_updates(
            AgendaAssignmentInput(
                agenda_lessons=fixture.agenda_lessons,
                assignment_master=(bad_master,),
                observed_infinite_campus=fixture.observed_infinite_campus,
            )
        )

        self.assertEqual(result.plans, ())
        self.assertEqual([issue.code for issue in result.issues], ["agenda_assignment_routing_conflict"])

    def test_ic_target_conflict_blocks_proposal(self) -> None:
        fixture = synthetic_agenda_assignment_example()
        bad_observation = replace(
            fixture.observed_infinite_campus[0],
            infinite_campus_assignment_id="wrong-ic-target",
        )
        result = plan_agenda_assignment_updates(
            AgendaAssignmentInput(
                agenda_lessons=fixture.agenda_lessons,
                assignment_master=fixture.assignment_master,
                observed_infinite_campus=(bad_observation,),
            )
        )

        self.assertEqual(result.plans, ())
        self.assertEqual([issue.code for issue in result.issues], ["infinite_campus_target_conflict"])

    def test_ambiguous_and_conflicting_mappings_never_choose_a_target(self) -> None:
        fixture = synthetic_agenda_assignment_example()
        duplicate_agenda = replace(
            fixture.agenda_lessons[0],
            agenda_lesson_stable_id="agenda:synthetic-biology-a4:week-01:lesson-02",
            intended_due_date="2026-09-18",
            agenda_revision_id="agenda-rev-synthetic-002",
        )
        bad_observation = replace(
            fixture.observed_infinite_campus[0],
            infinite_campus_assignment_id="unexpected-ic-target",
            observed_revision_id="observed-ic-rev-synthetic-002",
        )
        result = plan_agenda_assignment_updates(
            AgendaAssignmentInput(
                agenda_lessons=fixture.agenda_lessons + (duplicate_agenda,),
                assignment_master=fixture.assignment_master,
                observed_infinite_campus=fixture.observed_infinite_campus + (bad_observation,),
            )
        )

        self.assertEqual(result.plans, ())
        self.assertEqual(
            {issue.code for issue in result.issues},
            {
                "ambiguous_agenda_assignment_mapping",
                "ambiguous_observed_infinite_campus_mapping",
            },
        )

    def test_unmatched_or_invalid_observed_state_is_review_work_not_a_plan(self) -> None:
        fixture = synthetic_agenda_assignment_example()
        invalid_observed = replace(fixture.observed_infinite_campus[0], observed_due_date="next Friday")
        result = plan_agenda_assignment_updates(
            AgendaAssignmentInput(
                agenda_lessons=fixture.agenda_lessons,
                assignment_master=fixture.assignment_master,
                observed_infinite_campus=(invalid_observed,),
            )
        )

        self.assertEqual(result.plans, ())
        self.assertEqual([issue.code for issue in result.issues], ["invalid_observed_ic_due_date"])

    def test_missing_assignment_master_mapping_is_not_inferred(self) -> None:
        fixture = synthetic_agenda_assignment_example()
        result = plan_agenda_assignment_updates(
            AgendaAssignmentInput(
                agenda_lessons=fixture.agenda_lessons,
                assignment_master=(),
                observed_infinite_campus=fixture.observed_infinite_campus,
            )
        )

        self.assertEqual(result.plans, ())
        self.assertEqual(
            [issue.code for issue in result.issues], ["missing_assignment_master_mapping"]
        )


if __name__ == "__main__":
    unittest.main()
