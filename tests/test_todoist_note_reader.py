from datetime import date
from http.client import BadStatusLine
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
import unittest

from school_workflow_hub.adapters.read_only_http import read_json, SourceReadError
from school_workflow_hub.adapters.todoist_notes import TodoistNoteReader
from school_workflow_hub.workflows.daily_notes import plan_daily_notes
from tests.test_google_sheets_reader import Opener

PROJECT, SECTION, TOKEN = "synthetic-project", "synthetic-section", "fictional-test-token"


def task(**changes):
    result = {"id": "synthetic-task", "project_id": PROJECT, "section_id": SECTION,
              "content": "Synthetic note", "description": "Observation only", "labels": ["biology"],
              "due": {"date": "2026-09-14"}, "checked": False, "user_id": "private-owner"}
    result.update(changes)
    return result


class TodoistNoteReaderTests(unittest.TestCase):
    def test_explicit_scoped_get_keeps_filters_across_pages(self):
        opener = Opener({"results": [task()], "next_cursor": "opaque/page?cursor"},
                        {"results": [task(), task(id="synthetic-task-2")], "next_cursor": None})
        snapshot = TodoistNoteReader(TOKEN, opener).read_notes(PROJECT, SECTION, label="biology")
        self.assertEqual(len(snapshot.tasks), 2)
        self.assertTrue(snapshot.pagination_complete)
        self.assertFalse(snapshot.atomic_snapshot)
        for request, timeout in opener.calls:
            self.assertEqual(request.get_method(), "GET")
            query = parse_qs(urlsplit(request.full_url).query)
            self.assertEqual(query["project_id"], [PROJECT])
            self.assertEqual(query["section_id"], [SECTION])
            self.assertEqual(query["label"], ["biology"])
            self.assertEqual(query["limit"], ["100"])
            self.assertNotIn(TOKEN, request.full_url)
        self.assertEqual(parse_qs(urlsplit(opener.calls[1][0].full_url).query)["cursor"], ["opaque/page?cursor"])
        self.assertNotIn("private-owner", str(snapshot.tasks))
        self.assertNotIn("Synthetic note", repr(snapshot))
        self.assertNotIn(TOKEN, repr(TodoistNoteReader(TOKEN)))

    def test_unscoped_or_malformed_input_never_calls_network(self):
        for project, section in ((None, SECTION), (PROJECT, None), ("", SECTION), ("https://bad.invalid", SECTION)):
            opener = Opener()
            with self.assertRaises(SourceReadError):
                TodoistNoteReader(TOKEN, opener).read_notes(project, section)
            self.assertFalse(opener.calls)

    def test_wrong_section_project_deleted_or_label_raises_without_partial_snapshot(self):
        for record in (task(project_id="wrong"), task(section_id="wrong"), task(is_deleted=True), task(labels=["other"])):
            opener = Opener({"results": [record], "next_cursor": None})
            with self.assertRaisesRegex(SourceReadError, "^note_source_scope_mismatch$"):
                TodoistNoteReader(TOKEN, opener).read_notes(PROJECT, SECTION, label="biology")

    def test_mid_page_change_is_held(self):
        opener = Opener({"results": [task()], "next_cursor": "next"},
                        {"results": [task(content="Private changed text")], "next_cursor": None})
        with self.assertRaisesRegex(SourceReadError, "^note_changed_during_read$"):
            TodoistNoteReader(TOKEN, opener).read_notes(PROJECT, SECTION)

    def test_page_limit_repeat_or_missing_cursor_is_not_completion(self):
        cases = [([{ "results": [task()], "next_cursor": "next"}], {"max_pages": 1}),
                 ([{"results": [task()], "next_cursor": "next"}, {"results": [], "next_cursor": "next"}], {}),
                 ([{"results": [task()]}], {}),
                 ([{"results": [task()] * 101, "next_cursor": None}], {})]
        for pages, options in cases:
            with self.assertRaises(SourceReadError):
                TodoistNoteReader(TOKEN, Opener(*pages)).read_notes(PROJECT, SECTION, **options)

    def test_auth_and_provider_protocol_errors_are_sanitized(self):
        opener = Opener(HTTPError("private-url", 401, "private-token", {}, None))
        with self.assertRaisesRegex(SourceReadError, "^authentication_required$"):
            TodoistNoteReader(TOKEN, opener).read_notes(PROJECT, SECTION)
        self.assertEqual(len(opener.calls), 1)
        opener = Opener(*(BadStatusLine("private-provider-value") for _ in range(3)))
        with self.assertRaisesRegex(SourceReadError, "^source_unavailable$"):
            TodoistNoteReader(TOKEN, opener, lambda _: None).read_notes(PROJECT, SECTION)

    def test_transport_rejects_other_hosts_and_mutation_paths(self):
        for url in ("https://api.todoist.com/api/v1/tasks/secret/close", "https://example.invalid/api/v1/tasks",
                    "http://api.todoist.com/api/v1/tasks", "https://sheets.googleapis.com/v4/spreadsheets/id:batchUpdate",
                    "https://[malformed", None):
            opener = Opener()
            with self.assertRaises(SourceReadError):
                read_json(url, TOKEN, opener=opener)
            self.assertFalse(opener.calls)

    def test_reader_to_daily_router_without_source_modification(self):
        original = task()
        snapshot = TodoistNoteReader(TOKEN, Opener({"results": [original], "next_cursor": None})).read_notes(PROJECT, SECTION)
        result = plan_daily_notes(snapshot.tasks, local_day=date(2026, 9, 14), timezone_name="America/New_York",
            class_labels={"biology": "class:synthetic:biology"}, meeting_class_ids=frozenset({"class:synthetic:biology"}))
        self.assertEqual(len(result.notes), 1)
        self.assertFalse(original["checked"])
        self.assertEqual(result.external_writes, 0)

    def test_native_datetime_is_normalized_without_guessing_floating_timezone(self):
        for due, expected in (({"date": "2026-09-14T07:00:00", "timezone": "America/New_York"}, "2026-09-14T07:00:00-04:00"),
                              ({"date": "2026-09-14T11:00:00Z"}, "2026-09-14T11:00:00+00:00"),
                              ({"date": "2026-09-14T07:00:00"}, "2026-09-14T07:00:00")):
            original = task(due=due)
            snapshot = TodoistNoteReader(TOKEN, Opener({"results": [original], "next_cursor": None})).read_notes(PROJECT, SECTION)
            self.assertEqual(snapshot.tasks[0]["due"], {"datetime": expected})
            self.assertEqual(original["due"], due)


if __name__ == "__main__":
    unittest.main()
