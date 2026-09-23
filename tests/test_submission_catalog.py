"""Lesson metadata tests: synthetic Sheets only, never student source content."""
from contextlib import ExitStack
from copy import deepcopy
import json
import re
import unittest
from unittest.mock import patch

from school_workflow_hub.adapters import submission_catalog as catalog
from school_workflow_hub.adapters import shared_form_intake as intake
from school_workflow_hub.adapters.hosted_agenda import ConnectionError
from school_workflow_hub.adapters.google_sheets import SheetsReadError, validate_ranges
from school_workflow_hub.web import application

SOURCE, AGENDA = "registry_" + "x" * 24, "agenda_" + "y" * 24
DECK_A, DECK_B, FORM_A, FORM_B = (c * 24 for c in "abfg")
CONFIG = {"HUB_IAP_AUDIENCE": "/projects/123/locations/us-central1/services/synthetic",
          "HUB_ALLOWED_EMAILS": "teacher@example.invalid", "HUB_GRADING_SPREADSHEET_ID": SOURCE,
          "HUB_INTAKE_DECK_ASSIGNMENTS": json.dumps({DECK_A: "forensics:registered"})}


class Registry:
    def __init__(self, rows=1300, columns=4):
        self.rows, self.columns, self.calls = rows, columns, []
        self.values = [["assignment_key", "class_key", "assignment_title", "unused_metadata"],
                       ["forensics:registered", "forensics", "Registered lesson", "not returned"],
                       ["anatomy:registered", "anatomy", "Other class lesson", "not returned"]]

    def read(self, source, ranges=()):
        assert source == SOURCE
        self.calls.append(ranges)
        properties = {"title": "Assignments", "sheetId": 1,
                      "gridProperties": {"rowCount": self.rows, "columnCount": self.columns}}
        if not ranges:
            # Other worksheet names are metadata only, never permission to read cells.
            return {"sheets": [{"properties": properties},
                    {"properties": {"title": "Answers", "sheetId": 2}},
                    {"properties": {"title": "Submissions", "sheetId": 3}}]}
        validate_ranges(ranges)
        assert len(ranges) == 1
        match = re.fullmatch(r"'Assignments'!A(\d+):[A-Z]+(\d+)", ranges[0])
        assert match, "A catalog must never read Answers or Submissions"
        top, bottom = map(int, match.groups())
        values = self.values[top - 1:bottom]
        return {"sheets": [{"properties": {"title": "Assignments", "sheetId": 1},
                "data": [{"startRow": top - 1, "rowData": [
                    {"values": [{"formattedValue": value} for value in row]} for row in values]}]}]}


class Agenda:
    def __init__(self):
        self.calls = []

    def read(self, source, ranges=()):
        assert source == AGENDA
        self.calls.append(ranges)
        if not ranges:
            return {"sheets": [{"properties": {"title": "🔬 Forensics"}}]}
        assert ranges == ("'🔬 Forensics'!A3:U60",)
        header = [""] * 21
        header[15] = "_studentURL"
        header[20] = "_turnInURL"
        rows = [header]
        for deck, form, title in [(DECK_A, FORM_A, "Current registered title"),
                                  (DECK_B, FORM_B, "New lesson without uploads")]:
            row = [""] * 21
            row[0], row[2] = "Wk 03", title
            row[15] = "https://docs.google.com/presentation/d/" + deck + "/copy"
            row[20] = "https://docs.google.com/forms/d/" + form + "/viewform"
            rows.append(row)
        return {"sheets": [{"properties": {"title": "🔬 Forensics"}, "data": [
            {"startRow": 2, "rowData": [{"values": [{"formattedValue": v} for v in row]}
                                        for row in rows]}]}]}


class CatalogTests(unittest.TestCase):
    def harness(self, registry=None):
        registry, agenda = registry or Registry(), Agenda()
        stack = ExitStack()
        self.addCleanup(stack.close)
        authorize = stack.enter_context(patch.object(catalog, "authorize"))
        stack.enter_context(patch.object(catalog, "service_token", return_value="synthetic-token"))
        stack.enter_context(patch.object(catalog, "SheetsReader", return_value=registry))
        stack.enter_context(patch.object(intake, "authenticated_reader", return_value=(agenda, AGENDA)))
        # Every forbidden source path raises immediately if a future refactor uses it.
        for target in ("school_workflow_hub.adapters.grading_records.read_records",
                       "school_workflow_hub.adapters.roster_matching.read_roster_snapshot",
                       "school_workflow_hub.adapters.shared_form_intake.read_roster",
                       "school_workflow_hub.adapters.shared_form_intake.read_catalog_decks",
                       "school_workflow_hub.adapters.shared_form_intake.read_deck",
                       "school_workflow_hub.adapters.forms_submissions.FormsReader.responses"):
            stack.enter_context(patch(target, side_effect=AssertionError("student source read forbidden")))
        return registry, agenda, authorize

    def test_current_registered_and_new_lessons_without_any_upload_reads(self):
        registry, agenda, authorize = self.harness()
        result = catalog.read_catalog("synthetic-assertion", "forensics", CONFIG)
        authorize.assert_called_once()
        self.assertEqual(result["schema_version"], "hub-submission-catalog/v1")
        self.assertEqual(result["class_key"], "forensics")
        self.assertEqual(result["external_writes"], 0)
        self.assertEqual(result["live_model_calls"], 0)
        self.assertEqual(result["coverage"], {"complete": True, "issues": []})
        self.assertEqual(len(result["assignments"]), 2)
        saved, discovered = result["assignments"]
        self.assertEqual(saved["assignment_key"], "forensics:registered")
        self.assertFalse(saved["discovery_only"])
        self.assertRegex(discovered["assignment_key"], r"^discovery:forensics:[a-f0-9]{32}$")
        self.assertTrue(discovered["discovery_only"])
        self.assertEqual(discovered["registry_assignment_key"], "")
        self.assertEqual([a["form_id"] for a in result["assignments"]], [FORM_A, FORM_B])
        self.assertNotIn("Other class lesson", json.dumps(result))
        self.assertEqual(len([r for r in registry.calls if r]), 2, "Registry pagination remains bounded")
        self.assertEqual(agenda.calls, [(), ("'🔬 Forensics'!A3:U60",)])

    def test_empty_registry_still_exposes_new_agenda_lessons(self):
        registry = Registry()
        registry.values = registry.values[:1]
        self.harness(registry)
        result = catalog.read_catalog("assertion", "forensics", {**CONFIG, "HUB_INTAKE_DECK_ASSIGNMENTS": {}})
        self.assertEqual(len(result["assignments"]), 2)
        self.assertTrue(all(a["discovery_only"] for a in result["assignments"]))

    def test_exact_registry_source_binding_matches_refresh_without_private_pin(self):
        for field, value in (("source_deck_id", DECK_A),
                             ("source_url", "https://docs.google.com/presentation/d/" + DECK_A + "/copy")):
            registry = Registry()
            registry.values[0][3] = field
            registry.values[1][3] = value
            registry.values[2][3] = ""
            self.harness(registry)
            with self.subTest(field=field):
                config = {**CONFIG, "HUB_INTAKE_DECK_ASSIGNMENTS": {}}
                result = catalog.read_catalog("assertion", "forensics", config)
                saved, discovered = result["assignments"]
                self.assertEqual(saved["assignment_key"], "forensics:registered")
                self.assertFalse(saved["discovery_only"])
                self.assertTrue(saved["registry_mapping_verified"])
                self.assertTrue(discovered["discovery_only"])
                raw = catalog._registry(registry, SOURCE)
                expected = intake.agenda_catalog("assertion", config, {"assignments": raw},
                                                  class_key="forensics", discovery=True)
                self.assertEqual([a["assignment_key"] for a in result["assignments"]],
                                 [a["assignment_key"] for a in expected])

    def test_authentication_precedes_all_metadata_and_tokens(self):
        registry, agenda, authorize = self.harness()
        authorize.side_effect = ConnectionError("sign_in_required")
        with patch.object(catalog, "service_token") as token, self.assertRaisesRegex(ConnectionError, "sign_in_required"):
            catalog.read_catalog("bad", "forensics", CONFIG)
        token.assert_not_called()
        self.assertEqual(registry.calls + agenda.calls, [])

    def test_invalid_classes_never_read_any_source(self):
        registry, agenda, authorize = self.harness()
        for course in ("all", "FORensics", "", None, []):
            with self.subTest(course=course), self.assertRaises(ValueError):
                catalog.read_catalog("assertion", course, CONFIG)
        self.assertEqual(registry.calls + agenda.calls, [])
        authorize.assert_not_called()

    def test_output_is_allowlisted_and_structural_issues_preserved(self):
        self.harness()
        entry = {"assignment_key": "forensics:registered", "class_key": "forensics",
                 "assignment_title": "Synthetic", "source_text": "never expose",
                 "form_review_action": "Verify the two synthetic Turn it in links.",
                 "answers": "never expose", "catalog_issues": [{"source_reference": "Synthetic!P4",
                    "error": "intake_source_deck_unavailable"}]}
        with patch.object(catalog, "agenda_catalog", return_value=[entry]):
            result = catalog.read_catalog("assertion", "forensics", CONFIG)
        self.assertNotIn("never expose", json.dumps(result))
        self.assertFalse(result["coverage"]["complete"])
        self.assertEqual(result["assignments"][0]["form_review_action"], entry["form_review_action"])
        self.assertEqual(result["coverage"]["issues"][0]["error"], "intake_source_deck_unavailable")

    def test_wrong_class_and_duplicate_catalog_keys_are_rejected(self):
        self.harness()
        entry = {"assignment_key": "anatomy:other", "class_key": "anatomy"}
        for entries in ([entry], [{**entry, "class_key": "forensics"}] * 2):
            with patch.object(catalog, "agenda_catalog", return_value=entries), self.assertRaises(ConnectionError):
                catalog.read_catalog("assertion", "forensics", CONFIG)

    def test_registry_rejects_duplicate_headers_keys_and_oversized_grids(self):
        for mutate in (lambda r: r.values[0].append("assignment_key"),
                       lambda r: r.values.append(r.values[1]),
                       lambda r: setattr(r, "rows", 2001),
                       lambda r: setattr(r, "columns", 41)):
            registry = Registry()
            mutate(registry)
            with self.subTest(mutate=mutate), self.assertRaises(ConnectionError):
                catalog._registry(registry, SOURCE)

    def test_registry_never_accepts_extra_table_data(self):
        registry = Registry()
        original = registry.read
        def changed(source, ranges=()):
            result = original(source, ranges)
            if ranges:
                result["sheets"].append({"properties": {"title": "Answers", "sheetId": 2}})
            return result
        registry.read = changed
        with self.assertRaisesRegex(ConnectionError, "registry_changed"):
            catalog._registry(registry, SOURCE)

    def test_registry_geometry_change_and_bad_block_fail_closed(self):
        for kind in ("changed", "block"):
            registry = Registry()
            original = registry.read
            count = 0
            def changed(source, ranges=()):
                nonlocal count
                count += 1
                result = original(source, ranges)
                if kind == "changed" and not ranges and count > 1:
                    result["sheets"][0]["properties"]["sheetId"] = 99
                if kind == "block" and ranges:
                    result["sheets"][0]["data"][0]["startColumn"] = 1
                return result
            registry.read = changed
            with self.subTest(kind=kind), self.assertRaises(ConnectionError):
                catalog._registry(registry, SOURCE)

    def test_source_failures_never_echo_provider_data(self):
        self.harness()
        for error in (SheetsReadError("private teacher URL or token"),
                      ConnectionError("private teacher URL or token")):
            with patch.object(catalog, "agenda_catalog", side_effect=error), self.assertRaisesRegex(ConnectionError, "^submission_catalog_unavailable$"):
                catalog.read_catalog("assertion", "forensics", CONFIG)

    def request(self, query="class_key=forensics", method="GET"):
        state = {}
        def start(status, headers):
            state.update(status=status, headers=dict(headers))
        raw = application({"PATH_INFO": "/api/submission-catalog", "REQUEST_METHOD": method,
                           "QUERY_STRING": query, "HTTP_X_GOOG_IAP_JWT_ASSERTION": "synthetic"}, start)
        return state, json.loads(b"".join(raw))

    def test_http_exact_query_method_and_no_store(self):
        for query in ("", "class_key=", "class_key=all", "class_key=forensics&date=2026-09-23",
                      "class_key=forensics&class_key=anatomy", "class_key=forensics&extra=", "x" * 129):
            with self.subTest(query=query):
                state, _ = self.request(query)
                self.assertEqual(state["status"], "400 Bad Request")
        with patch.object(catalog, "read_catalog") as read:
            state, _ = self.request(method="POST")
            self.assertEqual(state["status"], "405 Method Not Allowed")
            read.assert_not_called()
        with patch.object(catalog, "read_catalog", return_value={"external_writes": 0}) as read:
            state, body = self.request()
            self.assertEqual(state["status"], "200 OK")
            self.assertEqual(state["headers"]["Cache-Control"], "no-store")
            self.assertEqual(read.call_args.args[:2], ("synthetic", "forensics"))

    def test_http_auth_and_unavailable_are_not_misclassified_as_bad_input(self):
        for error, status, code in [(ConnectionError("sign_in_required"), "403 Forbidden", "sign_in_required"),
                                    (ConnectionError("agenda_layout_changed"), "503 Service Unavailable", "agenda_layout_changed"),
                                    (ConnectionError("private URL"), "503 Service Unavailable", "submission_catalog_unavailable"),
                                    (RuntimeError("private URL"), "503 Service Unavailable", "submission_catalog_unavailable")]:
            with patch.object(catalog, "read_catalog", side_effect=error):
                state, body = self.request()
                self.assertEqual(state["status"], status)
                self.assertEqual(body["error"], code)


if __name__ == "__main__":
    unittest.main()
