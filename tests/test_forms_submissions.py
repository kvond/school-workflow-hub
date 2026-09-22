"""Synthetic tests for Form-first submission intake; no live school data."""
import json
import unittest

from school_workflow_hub.adapters.forms_submissions import (
    collect, form_id, parse_turnin_url, source_id,
)
from school_workflow_hub.adapters.hosted_agenda import ConnectionError

PUBLIC = "p" * 24
API = "a" * 24
FILE_A = "f" * 24
FILE_B = "g" * 24
LABEL = "Biology · Wk 04 — Fictional Energy"


class Forms:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = 0

    def responses(self, form, cursor=None):
        self.calls += 1
        return self.pages.pop(0)


class Drive:
    def metadata(self, file_id):
        return {"id": file_id, "name": "Fictional deck " + file_id[:2],
                "mimeType": "application/vnd.google-apps.presentation",
                "trashed": False, "modifiedTime": "2032-01-05T12:00:00Z",
                "version": "7"}


def answer(text=None, file_id=None):
    if text is not None:
        return {"textAnswers": {"answers": [{"value": text}]}}
    return {"fileUploadAnswers": {"answers": [{
        "fileId": file_id, "fileName": "Fictional deck",
        "mimeType": "application/vnd.google-apps.presentation"}]}}


def response(label, file_id):
    return {"responseId": "r" * 24,
            "answers": {"q-label": answer(text=label),
                        "q-file": answer(file_id=file_id)}}


class FormLinkTests(unittest.TestCase):
    def test_public_responder_link_extracts_exact_prefill(self):
        parsed = parse_turnin_url(
            "https://docs.google.com/forms/d/e/" + PUBLIC +
            "/viewform?usp=pp_url&entry.123=" + LABEL.replace(" ", "+"))
        self.assertEqual(parsed["public_id"], PUBLIC)
        self.assertEqual(parsed["assignment_label"], LABEL)
        self.assertIsNone(parsed["form_id"])

    def test_editor_link_uses_fallback_label(self):
        parsed = parse_turnin_url(
            "https://docs.google.com/forms/d/" + API + "/edit", LABEL)
        self.assertEqual(parsed["form_id"], API)
        self.assertEqual(parsed["assignment_label"], LABEL)

    def test_public_id_is_never_treated_as_api_id(self):
        parsed = {"public_id": PUBLIC, "form_id": None, "assignment_label": LABEL}
        with self.assertRaisesRegex(ConnectionError, "mapping"):
            form_id(parsed, {})
        self.assertEqual(form_id(parsed, {"HUB_SUBMISSION_FORM_ID": API}), API)
        self.assertEqual(form_id(
            parsed, {"HUB_FORM_ID_MAP": json.dumps({PUBLIC: API})}), API)

    def test_stream_key_is_assignment_specific(self):
        one = {"public_id": PUBLIC, "form_id": None, "assignment_label": LABEL}
        two = {"public_id": PUBLIC, "form_id": None, "assignment_label": LABEL + " II"}
        self.assertNotEqual(source_id(one), source_id(two))


class ResponseTests(unittest.TestCase):
    def test_exact_assignment_filter_excludes_other_shared_form_responses(self):
        pages = [{"responses": [
            response("Forensics · Wk 04 — Other", FILE_B),
            response(LABEL, FILE_A),
        ]}]
        files, checked = collect(Forms(pages), Drive(), API, LABEL)
        self.assertEqual(checked, 2)
        self.assertEqual([f["id"] for f in files], [FILE_A])

    def test_empty_form_is_clean_empty_state(self):
        files, checked = collect(Forms([{}]), Drive(), API, LABEL)
        self.assertEqual(files, [])
        self.assertEqual(checked, 0)

    def test_pagination_and_duplicate_uploads_are_deduplicated(self):
        pages = [
            {"responses": [response(LABEL, FILE_A)], "nextPageToken": "next"},
            {"responses": [response(LABEL, FILE_A), response(LABEL, FILE_B)]},
        ]
        reader = Forms(pages)
        files, checked = collect(reader, Drive(), API, LABEL)
        self.assertEqual(checked, 3)
        self.assertEqual([f["id"] for f in files], [FILE_A, FILE_B])
        self.assertEqual(reader.calls, 2)

    def test_missing_file_upload_answer_is_not_invented(self):
        pages = [{"responses": [{"answers": {"q-label": answer(text=LABEL)}}]}]
        files, checked = collect(Forms(pages), Drive(), API, LABEL)
        self.assertEqual(files, [])
        self.assertEqual(checked, 1)


if __name__ == "__main__":
    unittest.main()
