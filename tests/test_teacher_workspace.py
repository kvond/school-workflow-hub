from dataclasses import replace
import copy
import unittest

from school_workflow_hub.core.models import EnrollmentRecord, SubmissionRevision
from school_workflow_hub.workflows.teacher_workspace import (
    CompletionCase, DailyLesson, Deadline, FeedbackDocument, Outcome, SubmissionFact,
    build_submission_feed, build_teacher_workspace, plan_feedback_print, deadline_after_due_date,
)
from tests.test_completion_policy import ready, responses

STAMP = "2026-09-14T20:00:00Z"
CLASS = "class:synthetic:biology"
ASSIGNMENT = "assignment:synthetic:one"
LEARNER = "learner:synthetic:one"
FOLDER = "folder:synthetic:lesson"
PROMPT = "prompt:synthetic:current"
ROSTER = (EnrollmentRecord(CLASS, LEARNER), EnrollmentRecord(CLASS, "learner:synthetic:two"))


def fact(**changes):
    revision = SubmissionRevision("submission:synthetic:one", ASSIGNMENT, LEARNER, "revision:one", STAMP)
    return SubmissionFact(replace(revision, **changes), "2026-09-01T19:00:00Z")


def document(**changes):
    return replace(FeedbackDocument(CLASS, ASSIGNMENT, LEARNER, "revision:one", FOLDER,
                                   "document:synthetic:feedback", PROMPT, STAMP, "receipt:synthetic:file",
                                   "document:revision:one"), **changes)


def feed(**changes):
    args = dict(class_key=CLASS, assignment_keys=(ASSIGNMENT,), roster=ROSTER,
                submissions=(fact(),), roster_complete=True, full_history_complete=True,
                observed_at=STAMP, as_of=STAMP, prompt_version=PROMPT,
                deadlines=(Deadline(ASSIGNMENT, "2026-09-14T19:00:00Z", "policy:approved:fixture"),))
    args.update(changes)
    return build_submission_feed(**args)


def lesson(**changes):
    return replace(DailyLesson("lesson:synthetic:one", CLASS, ASSIGNMENT, "2026-09-14",
                              "Synthetic lesson", "Synthetic notes", "page:synthetic:daily",
                              "revision:daily:one", STAMP,
                              "https://docs.google.com/presentation/d/synthetic_deck/edit#slide=id.p1"), **changes)


class TeacherWorkspaceTests(unittest.TestCase):
    def test_teacher_deadline_rule_uses_due_plus_fourteen_local_dates(self):
        deadline = deadline_after_due_date(ASSIGNMENT, "2026-09-01")
        self.assertEqual(deadline.cutoff_at, "2026-09-15T23:59:59.999999-04:00")
        self.assertEqual(deadline_after_due_date(ASSIGNMENT, "2026-10-25").cutoff_at,
                         "2026-11-08T23:59:59.999999-05:00")

    def test_known_submission_and_missing_overdue_case(self):
        rows = feed()["rows"]
        self.assertEqual(rows[0]["submission_state"], "submitted")
        self.assertEqual(rows[0]["deadline_state"], "submitted_by_deadline")
        self.assertEqual(rows[1]["submission_state"], "not_submitted")
        self.assertEqual(rows[1]["deadline_state"], "past_deadline")
        self.assertEqual(rows[1]["feedback_state"], "missing_work_feedback_needed")

    def test_individual_extension_overrides_class_cutoff_for_parent_routing(self):
        learner = ROSTER[1].learner_stable_id
        extension = deadline_after_due_date(ASSIGNMENT, "2026-09-10", learner_key=learner)
        rows = feed(deadlines=(Deadline(ASSIGNMENT, "2026-09-14T19:00:00Z", "policy:class"),
                               extension), ic_contact_references={learner: "ic:contact:two"})["rows"]
        self.assertEqual(rows[1]["deadline_state"], "within_deadline")
        self.assertEqual(rows[1]["parent_email_route"], "not_needed")
        self.assertEqual(rows[1]["deadline_cutoff_at"], extension.cutoff_at)
        self.assertEqual(rows[1]["deadline_policy_reference"], extension.policy_reference)
        for deadlines in ((extension, extension),
                          (replace(extension, learner_key="learner:unknown"),)):
            with self.assertRaises(ValueError):
                feed(deadlines=deadlines)

    def test_delta_window_or_partial_roster_cannot_prove_missing(self):
        for changes in ({"full_history_complete": False}, {"roster_complete": False}):
            result = feed(**changes)
            self.assertEqual(result["rows"][1]["submission_state"], "unverified")
            self.assertEqual(result["rows"][1]["parent_email_route"], "eligibility_unverified")

    def test_unapproved_deadline_anchor_is_not_invented(self):
        result = feed(deadlines=())
        self.assertTrue(all(r["deadline_state"] == "unverified" for r in result["rows"]))

    def test_cutoff_boundary_and_snapshot_time_not_page_refresh(self):
        boundary = Deadline(ASSIGNMENT, STAMP, "policy:approved:fixture")
        result = feed(deadlines=(boundary,), as_of="2026-09-16T20:00:00Z")
        self.assertEqual(result["rows"][1]["deadline_state"], "within_deadline")

    def test_earlier_on_time_work_not_late_because_revised(self):
        later = replace(fact(revision_hash="revision:two"), submitted_at="2026-09-14T19:30:00Z")
        early = fact(observed_at="2026-09-01T20:00:00Z")
        result = feed(submissions=(early, later))
        self.assertEqual(result["rows"][0]["deadline_state"], "submitted_by_deadline")
        self.assertEqual(result["rows"][0]["submission_revision"], "revision:two")

    def test_incomplete_history_cannot_prove_first_submission_was_late(self):
        late = replace(fact(), submitted_at="2026-09-14T19:30:00Z")
        self.assertEqual(feed(submissions=(late,), full_history_complete=False)["rows"][0]["deadline_state"], "unverified")

    def test_conflicting_and_wrong_scope_submissions_rejected(self):
        for changes in ({"submissions": (fact(), fact(revision_hash="revision:other"))},
                        {"submissions": (fact(learner_stable_id="learner:synthetic:unknown"),)},
                        {"roster": (ROSTER[0], ROSTER[0])}):
            with self.assertRaises(ValueError):
                feed(**changes)

    def test_later_observation_does_not_select_between_distinct_submission_files(self):
        old = fact(observed_at="2026-09-01T20:00:00Z")
        other = fact(stable_id="submission:synthetic:other", revision_hash="revision:other")
        for submissions in ((old, other), (other, old)):
            with self.assertRaisesRegex(ValueError, "multiple_submission_documents_need_review"):
                feed(submissions=submissions)

    def test_conflicting_turnin_times_cannot_manufacture_on_time_evidence(self):
        on_time = fact()
        late = replace(on_time, submitted_at="2026-09-14T19:30:00Z")
        for submissions in ((on_time, late), (late, on_time)):
            with self.assertRaisesRegex(ValueError, "ambiguous_submission_observation"):
                feed(submissions=submissions)

    def test_empty_invalid_resource_mappings_are_not_treated_as_absent(self):
        for changes in ({"turnin_folders": []}, {"ic_contact_references": False}):
            with self.assertRaisesRegex(ValueError, "invalid_private_resource_mapping"):
                feed(**changes)

    def test_parent_route_requires_verified_private_ic_contact_reference(self):
        result = feed(ic_contact_references={"learner:synthetic:two": "ic:contact:synthetic:two"})
        self.assertEqual(result["rows"][1]["parent_email_route"], "ready_for_draft_review")
        self.assertEqual(result["external_writes"], 0)

    def test_feedback_must_match_current_revision_prompt_and_lesson_folder(self):
        args = dict(feedback=(document(),), turnin_folders={ASSIGNMENT: FOLDER})
        row = feed(**args)["rows"][0]
        self.assertEqual(row["feedback_state"], "available")
        self.assertEqual(row["feedback_document_reference"], "document:synthetic:feedback")
        for changes, expected in (({"submission_revision": "revision:old"}, "stale_feedback"),
                                  ({"prompt_version": "prompt:old"}, "stale_feedback"),
                                  ({"folder_reference": "folder:wrong"}, "folder_needs_review")):
            row = feed(feedback=(document(**changes),), turnin_folders={ASSIGNMENT: FOLDER})["rows"][0]
            self.assertEqual(row["feedback_state"], expected)
            self.assertIsNone(row["feedback_document_reference"])

    def test_delivery_readback_must_be_live_and_match_revision(self):
        outcome = Outcome(CLASS, ASSIGNMENT, LEARNER, "revision:one", "ic_grade", "verified", STAMP)
        with self.assertRaises(ValueError):
            feed(outcomes=(outcome,))
        valid = replace(outcome, verification="live_end_to_end", receipt_reference="receipt:synthetic:readback")
        self.assertEqual(feed(outcomes=(valid,))["rows"][0]["delivery"]["ic_grade"]["state"], "verified")
        stale = replace(valid, submission_revision="revision:old")
        self.assertEqual(feed(outcomes=(stale,))["rows"][0]["delivery"]["ic_grade"]["state"], "prior_revision")

    def test_four_pages_keep_source_slides_and_all_classes(self):
        lessons = (lesson(), lesson(lesson_key="lesson:synthetic:two", class_key="class:synthetic:anatomy"))
        result = build_teacher_workspace(lessons=lessons, submission_feeds=(feed(),), generated_at=STAMP)
        self.assertEqual(len(result["pages"]), 4)
        self.assertEqual(len(result["pages"]["teaching_agenda"]["lessons"]), 2)
        self.assertEqual(result["pages"]["teaching_agenda"]["lessons"][1]["open_slides"], lesson().slides_url)
        self.assertEqual(result["pages"]["teaching_agenda"]["comparison_target"], "infinite_campus")
        self.assertFalse(any(a["supported"] for a in result["actions"].values()))

    def test_absent_slides_do_not_generate_sample_resource(self):
        result = build_teacher_workspace(lessons=(lesson(slides_url=None),), submission_feeds=(), generated_at=STAMP)
        self.assertIsNone(result["pages"]["teaching_agenda"]["lessons"][0]["open_slides"])

    def test_unsafe_or_wrong_resource_link_rejected(self):
        for url in ("https://answerableteaching.com/lesson", "javascript:alert(1)",
                    "https://docs.google.com/document/d/synthetic/edit"):
            with self.assertRaises(ValueError):
                build_teacher_workspace(lessons=(lesson(slides_url=url),), submission_feeds=(), generated_at=STAMP)

    def test_summary_counts_cases_without_inventing_instructional_findings(self):
        result = build_teacher_workspace(lessons=(), submission_feeds=(feed(),), generated_at=STAMP)
        summary = result["pages"]["teacher_summary"]
        self.assertEqual(summary["case_counts"]["submission_state"], {"submitted": 1, "not_submitted": 1})
        self.assertEqual(summary["instructional_summary_state"], "not_generated")

    def test_completion_scores_join_only_current_submission_and_summary(self):
        case = CompletionCase(CLASS, ASSIGNMENT, LEARNER, "revision:one", "final", ready(), responses())
        data = feed(completion_cases=(case,))
        self.assertEqual(data["rows"][0]["completion"]["scores"]["content_notes"]["value"], 10)
        result = build_teacher_workspace(lessons=(), submission_feeds=(data,), generated_at=STAMP)
        self.assertEqual(result["pages"]["teacher_summary"]["case_counts"]["completion_state"],
                         {"proposed": 1, "not_assessed": 1})
        old = feed(completion_cases=(replace(case, submission_revision="revision:old"),))["rows"][0]
        self.assertEqual(old["completion_state"], "prior_revision")
        self.assertIsNone(old["completion"])

    def test_research_requires_separate_authorization_and_only_approved_references(self):
        private = ({"student": "do not expose"},)
        result = build_teacher_workspace(lessons=(), submission_feeds=(), generated_at=STAMP, research_receipts=private)
        self.assertEqual(result["pages"]["research_data"]["records"], [])
        with self.assertRaises(ValueError):
            build_teacher_workspace(lessons=(), submission_feeds=(), generated_at=STAMP,
                                    research_receipts=private, research_access=True)
        record = {"record_reference": "research:synthetic:one", "instruction_version": "codebook:v1",
                  "approval_reference": "approval:synthetic:research", "observed_at": STAMP}
        result = build_teacher_workspace(lessons=(), submission_feeds=(), generated_at=STAMP,
                                        research_receipts=(record,), research_access=True)
        self.assertEqual(result["pages"]["research_data"]["records"], [record])
        self.assertFalse(result["pages"]["research_data"]["collection_supported"])

    def test_printing_requires_direction_destination_and_complete_selected_batch(self):
        args = dict(class_key=CLASS, assignment_key=ASSIGNMENT, expected_learners=(LEARNER,),
                    folder_reference=FOLDER, prompt_version=PROMPT)
        blocked = plan_feedback_print((document(),), **args)
        self.assertEqual(blocked["status"], "blocked")
        enabled_args = dict(args, teacher_request_key="request:synthetic:one", printer_reference="printer:synthetic:school",
                            submission_revisions={LEARNER: "revision:one"})
        prepared = plan_feedback_print((document(),), **enabled_args)
        self.assertEqual(prepared["status"], "prepared_not_sent")
        self.assertFalse(prepared["execution_supported"])
        self.assertEqual(prepared["batch_key"], plan_feedback_print((document(),), **enabled_args)["batch_key"])
        self.assertEqual(plan_feedback_print((), **enabled_args)["status"], "blocked")
        self.assertEqual(plan_feedback_print((document(), document()), **enabled_args)["status"], "blocked")

    def test_print_retry_identity_ignores_check_time_but_tracks_content(self):
        args = dict(class_key=CLASS, assignment_key=ASSIGNMENT, expected_learners=(LEARNER,),
                    folder_reference=FOLDER, prompt_version=PROMPT,
                    teacher_request_key="request:synthetic:one", printer_reference="printer:synthetic:school",
                    submission_revisions={LEARNER: "revision:one"})
        before = plan_feedback_print((document(),), **args)
        refreshed = document(observed_at="2026-09-15T20:00:00Z", receipt_reference="receipt:synthetic:new")
        self.assertEqual(before["batch_key"], plan_feedback_print((refreshed,), **args)["batch_key"])
        changed = document(document_revision="document:revision:two")
        self.assertNotEqual(before["batch_key"], plan_feedback_print((changed,), **args)["batch_key"])
        stale = plan_feedback_print((document(),), **dict(args, submission_revisions={LEARNER: "revision:two"}))
        self.assertIn("print_feedback_revision_stale", stale["issue_codes"])

    def test_snapshot_does_not_mutate_or_alias_source_feed(self):
        source = feed()
        before = copy.deepcopy(source)
        result = build_teacher_workspace(lessons=(), submission_feeds=(source,), generated_at=STAMP)
        result["pages"]["submissions_feedback"]["classes"][0]["rows"].clear()
        self.assertEqual(source, before)

    def test_workspace_rejects_partial_feed_claiming_missing_work(self):
        for field in ("roster_complete", "full_history_complete"):
            source = feed()
            source[field] = False
            with self.assertRaisesRegex(ValueError, "missing_work_requires_complete_evidence"):
                build_teacher_workspace(lessons=(), submission_feeds=(source,), generated_at=STAMP)

    def test_workspace_rejects_row_timestamp_relabelled_by_newer_feed(self):
        source = feed()
        source["rows"][0]["last_checked_at"] = "2026-09-01T20:00:00Z"
        with self.assertRaisesRegex(ValueError, "invalid_submission_row"):
            build_teacher_workspace(lessons=(), submission_feeds=(source,), generated_at=STAMP)
