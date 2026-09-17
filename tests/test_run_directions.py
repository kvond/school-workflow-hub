import unittest

from school_workflow_hub.adapters import run_directions
from school_workflow_hub.adapters.hosted_agenda import ConnectionError

IDENTITY = {
    "HUB_IAP_AUDIENCE": "/projects/611166743877/locations/us-central1/services/school-workflow-hub",
    "HUB_ALLOWED_EMAILS": "teacher@example.edu",
}

FILLED = ('{"timezone": "America/New_York", "school_year": "2026-2027", '
          '"school_account": "teacher@example.edu", "infinite_campus_url": "ic.example.edu", '
          '"dashboard_url": "hub.example.app", "agenda_url": "sheet-reference", '
          '"roster_workbook_url": "workbook-reference", '
          '"assessment_history_url": "history-reference", '
          '"roster_sections": "A, B, C", "roster_protected_cells": "B2:B9"}')


class ValueReadingTest(unittest.TestCase):
    """The parsing half, without identity. Nothing here reaches a network."""

    def parse(self, raw):
        return run_directions._values({"HUB_RUN_DIRECTIONS": raw})

    def test_absent_configuration_is_empty_rather_than_an_error(self):
        self.assertEqual(run_directions._values({}), {})
        self.assertEqual(self.parse("   "), {})

    def test_declared_values_are_kept_and_blank_ones_dropped(self):
        values = self.parse('{"timezone": "America/New_York", "school_year": "  "}')
        self.assertEqual(values, {"timezone": "America/New_York"})

    def test_an_undeclared_placeholder_is_refused(self):
        with self.assertRaises(ConnectionError) as caught:
            self.parse('{"timezone": "ET", "admin_password": "x"}')
        self.assertEqual(str(caught.exception), "run_directions_unknown_placeholder")

    def test_malformed_or_oversized_configuration_is_refused(self):
        for raw, reason in (
                ("not json", "run_directions_not_valid_json"),
                ('["a"]', "run_directions_not_an_object"),
                ('{"timezone": 5}', "run_directions_value_not_short_text"),
                ('{"timezone": "' + "x" * 501 + '"}', "run_directions_value_not_short_text")):
            with self.assertRaises(ConnectionError) as caught:
                self.parse(raw)
            self.assertEqual(str(caught.exception), reason)

    def test_every_declared_name_appears_in_the_committed_template(self):
        import json
        from pathlib import Path
        template = json.loads((Path(__file__).resolve().parents[1]
                               / "web" / "run-directions.json").read_text())
        self.assertEqual(set(template["placeholders"]), set(run_directions.DECLARED))


class ReadValuesTest(unittest.TestCase):
    """The whole call, which an unverified request must never complete."""

    def test_unconfigured_identity_refuses_before_any_value_is_read(self):
        with self.assertRaises(ConnectionError) as caught:
            run_directions.read_values("assertion", {"HUB_RUN_DIRECTIONS": FILLED})
        self.assertEqual(str(caught.exception), "connection_not_configured")

    def test_missing_assertion_refuses_with_sign_in_required(self):
        config = dict(IDENTITY, HUB_RUN_DIRECTIONS=FILLED)
        with self.assertRaises(ConnectionError) as caught:
            run_directions.read_values(None, config)
        self.assertEqual(str(caught.exception), "sign_in_required")

    def test_a_verified_read_reports_values_and_what_is_still_missing(self):
        seen = {}

        def authorize(assertion, audience, allowed):
            seen["assertion"] = assertion

        original = run_directions.authorize
        run_directions.authorize = authorize
        try:
            result = run_directions.read_values(
                "signed", dict(IDENTITY, HUB_RUN_DIRECTIONS=FILLED))
        finally:
            run_directions.authorize = original

        self.assertEqual(seen["assertion"], "signed")
        self.assertEqual(result["schema_version"], "hub-run-directions-values/v1")
        self.assertEqual(result["values"]["timezone"], "America/New_York")
        self.assertEqual(result["missing"], ())
        self.assertTrue(result["complete"])
        self.assertEqual(result["external_writes"], 0)
        self.assertEqual(result["live_model_calls"], 0)

    def test_the_optional_checkpoint_is_not_counted_as_missing(self):
        original = run_directions.authorize
        run_directions.authorize = lambda *a: None
        try:
            result = run_directions.read_values(
                "signed", dict(IDENTITY, HUB_RUN_DIRECTIONS='{"timezone": "ET"}'))
        finally:
            run_directions.authorize = original
        self.assertNotIn("checkpoint_override", result["missing"])
        self.assertIn("school_account", result["missing"])
        self.assertFalse(result["complete"])


if __name__ == "__main__":
    unittest.main()
