"""A bounded Form read must prove completion before any submission is used."""
import unittest
from unittest.mock import patch

from hub_v2 import google, morning, work


class FormResponseCompletenessTests(unittest.TestCase):
    def page(self, number, *, more=True):
        value = {"responses": [{"responseId": "fictional-%d" % number}]}
        if more:
            value["nextPageToken"] = "page-%d" % (number + 1)
        return value

    def test_complete_pages_preserve_every_response_and_follow_the_exact_cursor(self):
        pages = [self.page(1), self.page(2), self.page(3, more=False)]
        expected = [page["responses"][0] for page in pages]
        with patch.object(google, "get_json", side_effect=pages) as api:
            self.assertEqual(google.form_responses("read-token", "fictional-form"), expected)
        self.assertEqual(api.call_count, 3)
        for index, call in enumerate(api.call_args_list):
            self.assertEqual(call.args[:2],
                             ("https://forms.googleapis.com/v1/forms/fictional-form/responses", "read-token"))
            expected_params = {"pageSize": 5000}
            if index:
                expected_params["pageToken"] = "page-%d" % (index + 1)
            self.assertEqual(call.args[2], expected_params)
            self.assertNotIn("method", call.kwargs)

    def test_twentieth_page_can_complete_the_listing_without_a_twenty_first_read(self):
        pages = [self.page(number, more=number < 20) for number in range(1, 21)]
        with patch.object(google, "get_json", side_effect=pages) as api:
            result = google.form_responses("read-token", "fictional-form")
        self.assertEqual(result, [page["responses"][0] for page in pages])
        self.assertEqual(api.call_count, 20)

    def test_remaining_page_after_the_limit_raises_instead_of_returning_partial_work(self):
        pages = [self.page(number) for number in range(1, 22)]
        with patch.object(google, "get_json", side_effect=pages) as api:
            with self.assertRaisesRegex(google.GoogleError, "verification limit") as caught:
                google.form_responses("read-token", "fictional-form")
        self.assertEqual(api.call_count, 20)
        self.assertEqual(caught.exception.reason, "incomplete_response_listing")
        self.assertIsNone(caught.exception.status)
        self.assertFalse(google.read_timed_out(caught.exception))

    def test_repeated_cursor_or_cycle_is_refused_before_reading_the_same_page_again(self):
        for cursors in (("next", "next"), ("next", "other", "next")):
            pages = [{"responses": [{"responseId": "fictional"}], "nextPageToken": cursor}
                     for cursor in cursors]
            with self.subTest(cursors=cursors), patch.object(google, "get_json", side_effect=pages) as api:
                with self.assertRaisesRegex(google.GoogleError, "repeated a page") as caught:
                    google.form_responses("read-token", "fictional-form")
                self.assertEqual(api.call_count, len(pages))
                self.assertEqual(caught.exception.reason, "incomplete_response_listing")

    def test_empty_final_page_is_valid_but_does_not_end_a_continuing_listing(self):
        for final in ({}, {"responses": []}, {"responses": [], "nextPageToken": ""}):
            with self.subTest(final=final), patch.object(google, "get_json", side_effect=[
                    {"nextPageToken": "next"}, final]) as api:
                self.assertEqual(google.form_responses("read-token", "fictional-form"), [])
                self.assertEqual(api.call_count, 2)

    def test_unusable_page_or_cursor_cannot_be_accepted_as_complete_coverage(self):
        malformed = (None, [], "private provider body", {"responses": None},
                     {"responses": {}}, {"responses": [None]},
                     {"responses": [], "nextPageToken": 0},
                     {"responses": [], "nextPageToken": False},
                     {"responses": [], "nextPageToken": ["private cursor"]},
                     {"responses": [{}] * 5001})
        for page in malformed:
            with self.subTest(page_type=type(page).__name__), \
                    patch.object(google, "get_json", side_effect=[self.page(1), page]) as api:
                with self.assertRaises(google.GoogleError) as caught:
                    google.form_responses("read-token", "fictional-form")
                self.assertEqual(api.call_count, 2)
                self.assertEqual(caught.exception.reason, "incomplete_response_listing")
                self.assertNotIn("private", str(caught.exception))

    def test_provider_failure_propagates_without_partial_results_or_new_retry(self):
        error = google.GoogleError("Provider unavailable", 503)
        with patch.object(google, "get_json", side_effect=[self.page(1), error]) as api:
            with self.assertRaises(google.GoogleError) as caught:
                google.form_responses("read-token", "fictional-form")
        self.assertIs(caught.exception, error)
        self.assertEqual(api.call_count, 2)

    def test_incomplete_form_read_remains_an_availability_hold_before_scoring(self):
        pages = [self.page(number) for number in range(1, 21)]
        form = {"label": "Fictional assignment", "url": "https://forms.example.test/form",
                "form_id": "fictional-form", "problem": None}
        with patch.object(google, "get_json", side_effect=pages), \
                patch.object(work, "submissions") as submissions, \
                patch.object(google, "drive_file") as metadata:
            read = work.read_form("read-token", form, "reader@example.test")
        self.assertIn("coverage is incomplete", read["problem"])
        self.assertIs(read["read_timeout"], False)
        submissions.assert_not_called()
        metadata.assert_not_called()
        found = {"lessons": [{"forms": [read]}], "problems": []}
        with patch.object(work, "assignment_policy", return_value={"problem": None, "excluded": False}), \
                patch.object(morning.completion, "assignment_readiness") as readiness:
            scored = unittest.mock.Mock()
            from test_hub_morning import single_context_agenda
            agenda = single_context_agenda("Wk 01", "fictional-form", "forensics")
            result = morning.make_list("read-token", {}, agenda, "forensics", "Wk 01",
                                       "fictional-form", lambda *args, **kwargs: found, scored)
        self.assertEqual(result, {"code": "coverage_incomplete"})
        readiness.assert_not_called()
        scored.assert_not_called()


if __name__ == "__main__":
    unittest.main()
