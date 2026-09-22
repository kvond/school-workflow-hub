"""Synthetic tests for assignment filtering in one shared Google Form."""
import unittest

from school_workflow_hub.adapters.forms_submissions import response_file_refs
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


if __name__ == "__main__":
    unittest.main()
