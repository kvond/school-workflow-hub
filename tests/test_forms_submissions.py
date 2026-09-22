"""Synthetic tests for assignment filtering in one shared Google Form."""
import unittest
from unittest.mock import patch

from school_workflow_hub.adapters import forms_submissions as forms
from school_workflow_hub.adapters.forms_submissions import (
    historical_assignment_file_ids, response_file_refs,
)
from school_workflow_hub.adapters.hosted_agenda import ConnectionError

FORM = "FORM" + "x" * 24
FILE_A = "a" * 24
FILE_B = "b" * 24


def text_answer(value):
    return {"textAnswers": {"answers": [{"value": value}]}}


def file_answer(file_id, name):
    return {"fileId": file_id, "fileName": name,
            "mimeType": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}


def form_response(assignment, files=()):
    answers = {"assignment": text_answer(assignment)}
    if files:
        answers["files"] = {"fileUploadAnswers": {"answers": [
            file_answer(file_id, name) for file_id, name in files]}}
    return {"responseId": "r" * 24, "createTime": "2026-09-20T14:00:00Z",
            "lastSubmittedTime": "2026-09-20T14:00:00Z", "answers": answers}


class Reader:
    def __init__(self, pages):
        self.pages = list(pages)
        self.index = 0

    def responses(self, form_id, cursor=None):
        page = self.pages[self.index]
        self.index += 1
        return page


class SharedFormTests(unittest.TestCase):
    def test_exact_assignment_filters_shared_form(self):
        reader = Reader([{"responses": [
            form_response("Biology assignment", ((FILE_A, "bio.pptx"),)),
            form_response("Forensics assignment", ((FILE_B, "forensics.pptx"),)),
        ]}])
        refs, evidence = response_file_refs(reader, FORM, "Biology assignment")
        self.assertEqual(set(refs), {FILE_A})
        self.assertEqual(evidence["responses_checked"], 2)
        self.assertEqual(evidence["matching_responses"], 1)

    def test_zero_responses_is_clean_empty_state(self):
        refs, evidence = response_file_refs(
            Reader([{"responses": []}]), FORM, "Biology assignment")
        self.assertEqual(refs, {})
        self.assertEqual(evidence["matching_responses"], 0)

    def test_multiple_uploads_and_missing_upload_are_supported(self):
        reader = Reader([{"responses": [
            form_response("A", ((FILE_A, "one.pptx"), (FILE_B, "two.pptx"))),
            form_response("A"),
        ]}])
        refs, evidence = response_file_refs(reader, FORM, "A")
        self.assertEqual(set(refs), {FILE_A, FILE_B})
        self.assertEqual(evidence["matching_responses"], 2)

    def test_repeated_cursor_fails_closed(self):
        reader = Reader([
            {"responses": [], "nextPageToken": "same"},
            {"responses": [], "nextPageToken": "same"},
        ])
        with self.assertRaisesRegex(ConnectionError, "incomplete"):
            response_file_refs(reader, FORM, "A")


class HistoricalCoverageTests(unittest.TestCase):
    def test_forensics_week_collects_all_recorded_assignment_keys_for_that_week(self):
        records = {
            "assignments": [
                {"assignment_key": "forensics:F03-Observation", "class_key": "forensics",
                 "assignment_title": "Observation and deductive reasoning"},
                {"assignment_key": "forensics:F03ID", "class_key": "forensics",
                 "assignment_title": "Inductive and deductive reasoning"},
                {"assignment_key": "forensics:F04", "class_key": "forensics",
                 "assignment_title": "Eyewitness accounts"},
            ],
            "submissions": [
                {"assignment_key": "forensics:F03-Observation", "class_key": "forensics",
                 "source_file_id": FILE_A},
                {"assignment_key": "forensics:F03ID", "class_key": "forensics",
                 "source_file_id": FILE_B},
                {"assignment_key": "forensics:F04", "class_key": "forensics",
                 "source_file_id": "c" * 24},
            ],
        }
        ids = historical_assignment_file_ids(
            records, "forensics",
            "🔬 Forensics · Wk 03 — Observation Skills & Deductive Reasoning")
        self.assertEqual(ids, [FILE_A, FILE_B])

    def test_missing_recorded_legacy_file_is_gap_not_current_coverage_failure(self):
        class RootReader:
            def metadata(self, file_id):
                return {"id": file_id, "name": "00 ALL TURN IN",
                        "mimeType": forms.FOLDER, "trashed": False}
        current = {"id": FILE_A, "name": "current.pptx",
                   "mimeType": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                   "trashed": False, "modifiedTime": "2026-09-20T14:00:00Z",
                   "version": "2"}
        evidence = {"pages": 1, "responses_checked": 1, "matching_responses": 1}
        ref = {"id": FILE_A, "form_name": "current.pptx",
               "form_mime_type": current["mimeType"],
               "submitted_at": "2026-09-20T14:00:00Z"}
        with patch.object(forms, "service_token", return_value="tok"), \
             patch.object(forms, "FormsReader"), \
             patch.object(forms, "response_file_refs", return_value=({FILE_A: ref}, evidence)), \
             patch.object(forms, "DriveReader", return_value=RootReader()), \
             patch.object(forms, "locate_files_under_root",
                          return_value=({FILE_A: current}, 4)):
            files, result = forms.collect_assignment_submissions(
                {"HUB_TURNIN_FORM_ID": FORM,
                 "HUB_SUBMISSIONS_ROOT_FOLDER": "r" * 24,
                 "HUB_READER_SERVICE_ACCOUNT": "reader@example.com"},
                "Biology assignment", known_file_ids=(FILE_B,))
        self.assertEqual([row["id"] for row in files], [FILE_A])
        self.assertEqual(result["legacy_outside_root_files"], 1)
        self.assertEqual(result["root_verified_files"], 1)

    def test_missing_current_form_file_still_fails_closed(self):
        class RootReader:
            def metadata(self, file_id):
                return {"id": file_id, "name": "00 ALL TURN IN",
                        "mimeType": forms.FOLDER, "trashed": False}
        evidence = {"pages": 1, "responses_checked": 1, "matching_responses": 1}
        ref = {"id": FILE_A, "form_name": "current.pptx",
               "form_mime_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
               "submitted_at": "2026-09-20T14:00:00Z"}
        with patch.object(forms, "service_token", return_value="tok"), \
             patch.object(forms, "FormsReader"), \
             patch.object(forms, "response_file_refs", return_value=({FILE_A: ref}, evidence)), \
             patch.object(forms, "DriveReader", return_value=RootReader()), \
             patch.object(forms, "locate_files_under_root", return_value=({}, 4)):
            with self.assertRaisesRegex(ConnectionError, "coverage_incomplete"):
                forms.collect_assignment_submissions(
                    {"HUB_TURNIN_FORM_ID": FORM,
                     "HUB_SUBMISSIONS_ROOT_FOLDER": "r" * 24,
                     "HUB_READER_SERVICE_ACCOUNT": "reader@example.com"},
                    "Biology assignment")


if __name__ == "__main__":
    unittest.main()
