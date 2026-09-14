from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import unittest

from school_workflow_hub.core.models import AssignmentRecord
from school_workflow_hub.core.workflow import validate_preview
from school_workflow_hub.workflows.agenda_interconnection import (
    AssignmentFolderObservation, InfiniteCampusObservation, SchoologyObservation,
)
from school_workflow_hub.workflows.curriculum_pipeline import (
    AgendaSlot, AssignmentObservation, CalendarDay, CalendarRevision,
    CurriculumPipelineInput, compare_next_year_baseline, plan_curriculum_pipeline,
)


def synthetic_pipeline_input(calendar_name="normal"):
    fixtures = json.loads((Path(__file__).parent / "fixtures/curriculum_calendars.json").read_text())
    calendar = CalendarRevision("synthetic-calendar", "synthetic-calendar-" + calendar_name,
                                tuple(CalendarDay(**day) for day in fixtures[calendar_name]))
    record = AssignmentRecord("synthetic-assignment-1", "synthetic-forensics-a1", "Synthetic evidence", None,
                              "synthetic-deck-current", "synthetic-form-current",
                              {"assignment_folder": "synthetic-folder-item", "schoology": "synthetic-schoology-item",
                               "infinite_campus": "synthetic-ic-item"})
    return CurriculumPipelineInput(
        calendar, (AgendaSlot("synthetic-lesson-1", record, "synthetic-agenda-v1", "A", 1, 2),),
        (AssignmentObservation(replace(record, title="Synthetic old title", intended_due_date="2026-09-14",
                                       deck_file_id="synthetic-deck-old"), "synthetic-master-v1"),),
        (AssignmentFolderObservation(record.class_stable_id, record.stable_id, "synthetic-folder-item",
                                     "2026-09-14", "Synthetic old folder message", "synthetic-deck-old",
                                     "synthetic-form-current", "synthetic-folder-v1"),),
        (SchoologyObservation(record.class_stable_id, record.stable_id, "synthetic-schoology-item",
                              "2026-09-14", "Synthetic old Schoology message", "synthetic-schoology-v1"),),
        (InfiniteCampusObservation(record.class_stable_id, record.stable_id, "synthetic-ic-item",
                                   "2026-09-14", "synthetic-ic-v1"),),
    )


class CurriculumPipelineTests(unittest.TestCase):
    def test_calendar_normal_reversed_changed_and_holiday(self):
        for case, expected in (("normal", "2026-09-16"), ("reversed", "2026-09-17"),
                               ("changed", "2026-09-15"), ("holiday", "2026-09-17")):
            with self.subTest(case=case):
                preview = plan_curriculum_pipeline(synthetic_pipeline_input(case))
                validate_preview(preview)
                ic = [change for change in preview.changes if change.target_system == "infinite_campus"]
                self.assertEqual([change.proposed_value for change in ic], [expected])
                self.assertEqual(preview.artifacts[0]["teaching_dates"][-1], expected)
                self.assertEqual(preview.external_writes, 0)
                self.assertEqual(preview.live_model_calls, 0)

    def test_changed_calendar_has_reviewable_sequence_notice(self):
        request = synthetic_pipeline_input("changed")
        preview = plan_curriculum_pipeline(replace(request, previous_calendar=synthetic_pipeline_input().calendar))
        self.assertIn("calendar_sequence_changed", [issue.code for issue in preview.issues])
        self.assertTrue(all(any(ref.source_type == "calendar" for ref in change.evidence) for change in preview.changes))

    def test_moved_renamed_and_stale_materials_preserve_stable_identity(self):
        request = synthetic_pipeline_input()
        slot = replace(request.agenda[0], meeting_index=2, sessions=1,
                       assignment=replace(request.agenda[0].assignment, title="Synthetic renamed lesson"))
        preview = plan_curriculum_pipeline(replace(request, agenda=(slot,)))
        self.assertTrue(any(change.field == "title" and change.proposed_value == "Synthetic renamed lesson"
                            for change in preview.changes))
        self.assertTrue(any(change.field == "deck_file_id" and change.old_value == "synthetic-deck-old"
                            for change in preview.changes))
        self.assertEqual({change.assignment_key for change in preview.changes}, {slot.assignment.stable_id})
        self.assertEqual(preview.artifacts[0]["teaching_dates"], ["2026-09-16"])

    def test_every_change_has_review_evidence_and_is_reversible(self):
        preview = plan_curriculum_pipeline(synthetic_pipeline_input())
        self.assertEqual(len({change.stable_id for change in preview.changes}), len(preview.changes))
        for change in preview.changes:
            self.assertNotEqual(change.old_value, change.proposed_value)
            self.assertTrue(change.evidence)
            self.assertTrue(change.reversible)
            self.assertEqual(change.approval_status, "pending")
            self.assertEqual(change.status, "proposed")
            self.assertEqual(change.confidence, 1.0)

    def test_identical_and_input_reordered_runs_have_identical_output(self):
        request = synthetic_pipeline_input()
        first = plan_curriculum_pipeline(request)
        self.assertEqual(first, plan_curriculum_pipeline(request))
        self.assertEqual(first, plan_curriculum_pipeline(replace(request,
            calendar=replace(request.calendar, days=tuple(reversed(request.calendar.days))))))

    def test_already_correct_is_noop(self):
        request = synthetic_pipeline_input()
        record = replace(request.agenda[0].assignment, intended_due_date="2026-09-16")
        folder_message = "Complete Synthetic evidence. Materials: deck synthetic-deck-current; form synthetic-form-current. Due 2026-09-16."
        schoology_message = "Complete Synthetic evidence using deck synthetic-deck-current; submit form synthetic-form-current. Due 2026-09-16."
        correct = replace(request, assignments=(AssignmentObservation(record, "synthetic-master-v1"),),
            folders=(replace(request.folders[0], observed_due_date="2026-09-16", observed_message=folder_message,
                             deck_file_id=record.deck_file_id),),
            schoology=(replace(request.schoology[0], observed_due_date="2026-09-16", observed_message=schoology_message),),
            infinite_campus=(replace(request.infinite_campus[0], observed_due_date="2026-09-16"),))
        preview = plan_curriculum_pipeline(correct)
        self.assertEqual(preview.changes, ())
        self.assertEqual(preview.issues, ())

    def test_missing_duplicate_and_wrong_course_mappings_block_assignment(self):
        request = synthetic_pipeline_input()
        cases = (
            (replace(request, assignments=()), "missing_assignment_mapping"),
            (replace(request, assignments=request.assignments * 2), "duplicate_assignment_mapping"),
            (replace(request, agenda=request.agenda * 2), "duplicate_agenda_assignment"),
            (replace(request, assignments=(replace(request.assignments[0], assignment=replace(
                request.assignments[0].assignment, class_stable_id="synthetic-anatomy-b3")),)), "wrong_course_assignment_mapping"),
        )
        for bad, code in cases:
            with self.subTest(code=code):
                preview = plan_curriculum_pipeline(bad)
                self.assertFalse(preview.changes)
                self.assertIn(code, [issue.code for issue in preview.issues])

    def test_missing_material_and_unsafe_target_remap_block(self):
        request = synthetic_pipeline_input()
        for record, code in (
            (replace(request.agenda[0].assignment, deck_file_id=None), "missing_required_material_mapping"),
            (replace(request.agenda[0].assignment, form_file_id=None), "missing_required_material_mapping"),
            (replace(request.agenda[0].assignment, external_ids={"schoology": "synthetic-new-target"}), "unsafe_target_remapping"),
        ):
            result = plan_curriculum_pipeline(replace(request, agenda=(replace(request.agenda[0], assignment=record),)))
            self.assertFalse(result.changes)
            self.assertIn(code, [issue.code for issue in result.issues])

    def test_missing_or_duplicate_target_observation_blocks_only_that_target(self):
        request = synthetic_pipeline_input()
        for records, code in (((), "missing_schoology_mapping"), (request.schoology * 2, "ambiguous_schoology_mapping")):
            result = plan_curriculum_pipeline(replace(request, schoology=records))
            self.assertIn(code, [issue.code for issue in result.issues])
            self.assertFalse(any(change.target_system == "schoology" for change in result.changes))
            self.assertTrue(any(change.target_system == "infinite_campus" for change in result.changes))

    def test_calendar_ambiguity_exhaustion_and_malformed_date_fail_closed(self):
        request = synthetic_pipeline_input()
        bad_cases = (
            (replace(request, calendar=replace(request.calendar, days=request.calendar.days * 2)), "duplicate_calendar_date"),
            (replace(request, calendar=replace(request.calendar, days=(CalendarDay("not-a-date", "A"),))), "invalid_calendar_day"),
            (replace(request, agenda=(replace(request.agenda[0], meeting_index=8),)), "missing_calendar_meeting"),
            (replace(request, agenda=(replace(request.agenda[0], meeting_index=True),)), "invalid_agenda_calendar_slot"),
        )
        for bad, code in bad_cases:
            result = plan_curriculum_pipeline(bad)
            self.assertFalse(result.changes)
            self.assertIn(code, [issue.code for issue in result.issues])

    def test_synthetic_and_privacy_boundaries(self):
        request = synthetic_pipeline_input()
        with self.assertRaises(ValueError):
            plan_curriculum_pipeline(replace(request, synthetic_marker="live"))
        with self.assertRaises(ValueError):
            plan_curriculum_pipeline(replace(request, agenda=(replace(request.agenda[0],
                assignment=replace(request.agenda[0].assignment, deck_file_id="not-fixture-material")),)))
        with self.assertRaises(ValueError):
            plan_curriculum_pipeline(replace(request, agenda=(replace(request.agenda[0],
                assignment=replace(request.agenda[0].assignment, title="fixture.person@example.invalid")),)))

    def test_next_year_baseline_is_preview_and_missing_is_not_deletion(self):
        record = synthetic_pipeline_input().agenda[0].assignment
        result = compare_next_year_baseline((record,), (replace(record, title="Synthetic future lesson"),),
                                             "synthetic-baseline-v1", "synthetic-current-v2")
        self.assertEqual(len(result.changes), 1)
        self.assertEqual(result.changes[0].old_value, record.title)
        missing = compare_next_year_baseline((record,), (), "synthetic-baseline-v1", "synthetic-current-v2")
        self.assertFalse(missing.changes)
        self.assertEqual(missing.issues[0].code, "baseline_missing_or_duplicate_assignment")


if __name__ == "__main__":
    unittest.main()
