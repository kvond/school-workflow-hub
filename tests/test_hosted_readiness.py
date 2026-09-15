import json
import unittest
from unittest.mock import patch

from school_workflow_hub.adapters import hosted_agenda as pilot
from tests.test_hosted_agenda import CONFIG
from tests.test_web_hosting import request


class ReadinessTests(unittest.TestCase):
    def test_unconfigured_does_not_authenticate_or_read(self):
        with patch.object(pilot, "authorize") as auth, patch.object(pilot, "service_token") as token:
            result = pilot.check_readiness("assertion", {})
        auth.assert_not_called()
        token.assert_not_called()
        self.assertFalse(result["authentication_verified"])
        self.assertEqual(result["checks"]["authentication"]["status"], "not_configured")

    def test_configured_environment_and_email_header_do_not_prove_authentication(self):
        with patch.dict("os.environ", CONFIG, clear=True), patch.object(pilot, "service_token") as token:
            response = request("/api/readiness",
                               HTTP_X_GOOG_AUTHENTICATED_USER_EMAIL="teacher@example.com",
                               HTTP_AUTHORIZATION="Bearer not-an-IAP-assertion")
        result = json.loads(response["body"])
        token.assert_not_called()
        self.assertFalse(result["authentication_verified"])
        self.assertEqual(result["checks"]["authentication"]["status"], "sign_in_required")

    def test_invalid_signature_never_reaches_source(self):
        with patch.object(pilot, "authorize", side_effect=pilot.ConnectionError("sign_in_required")), \
                patch.object(pilot, "service_token") as token:
            result = pilot.check_readiness("forged-assertion", CONFIG)
        token.assert_not_called()
        self.assertFalse(result["school_connected"])
        self.assertNotIn("forged-assertion", json.dumps(result))

    def test_fresh_metadata_is_narrowly_scoped_and_cannot_enable_actions(self):
        with patch.object(pilot, "authorize") as auth, \
                patch.object(pilot, "service_token", return_value="private-token"), \
                patch.object(pilot, "SheetsReader") as reader:
            reader.return_value.read.return_value = {"sheets": [{}, {}]}
            result = pilot.check_readiness("signed-assertion", CONFIG,
                                           teaching_provider_configured=True)
        auth.assert_called_once_with("signed-assertion", CONFIG["HUB_IAP_AUDIENCE"],
                                     {"teacher@example.com"})
        reader.return_value.read.assert_called_once_with(CONFIG["HUB_AGENDA_SPREADSHEET_ID"])
        self.assertTrue(result["authentication_verified"])
        self.assertEqual(result["mode"], "connected_readonly")
        self.assertEqual(result["school_connection_scope"], "agenda_metadata_only")
        self.assertEqual(result["checks"]["agenda_read"]["tab_count"], 2)
        self.assertFalse(result["checks"]["agenda_read"]["cell_data_loaded"])
        self.assertEqual(result["checks"]["dated_lessons"]["status"], "not_checked")
        self.assertEqual(result["checks"]["teaching_results"]["status"], "configured_not_verified")
        for field in ("persistent_storage_verified", "save_enabled", "run_enabled"):
            self.assertIs(result[field], False)
        self.assertEqual(result["external_writes"], 0)
        for value in (CONFIG["HUB_AGENDA_SPREADSHEET_ID"], CONFIG["HUB_IAP_AUDIENCE"],
                      CONFIG["HUB_READER_SERVICE_ACCOUNT"], CONFIG["HUB_ALLOWED_EMAILS"],
                      "private-token", "signed-assertion"):
            self.assertNotIn(value, json.dumps(result))

    def test_source_failure_does_not_erase_verified_sign_in_or_leak_details(self):
        with patch.object(pilot, "authorize"), \
                patch.object(pilot, "service_token", side_effect=RuntimeError("private-source-token")):
            result = pilot.check_readiness("assertion", CONFIG)
        self.assertTrue(result["authentication_verified"])
        self.assertFalse(result["school_connected"])
        self.assertEqual(result["checks"]["agenda_read"]["status"], "unavailable")
        self.assertNotIn("private-source-token", json.dumps(result))

    def test_invalid_metadata_cannot_be_reported_as_verified(self):
        with patch.object(pilot, "authorize"), patch.object(pilot, "service_token"), \
                patch.object(pilot, "SheetsReader") as reader:
            reader.return_value.read.return_value = {"sheets": "malformed"}
            result = pilot.check_readiness("assertion", CONFIG)
        self.assertFalse(result["school_connected"])
        self.assertEqual(result["checks"]["agenda_read"]["status"], "unavailable")

    def test_sign_in_can_work_before_agenda_configuration(self):
        with patch.object(pilot, "authorize"), patch.object(pilot, "service_token") as token:
            result = pilot.check_readiness("assertion", {**CONFIG, "HUB_AGENDA_READ_ENABLED": "false"})
        token.assert_not_called()
        self.assertTrue(result["authentication_verified"])
        self.assertEqual(result["checks"]["agenda_read"]["status"], "not_configured")

    def test_head_and_mutating_methods_do_not_probe_sources(self):
        with patch.object(pilot, "check_readiness") as check:
            self.assertEqual(request("/api/readiness", "HEAD")["body"], b"")
            self.assertEqual(request("/api/readiness", "POST")["status"], "405 Method Not Allowed")
            self.assertEqual(request("/healthz")["status"], "200 OK")
        check.assert_not_called()

    def test_get_route_passes_only_signed_assertion(self):
        with patch.object(pilot, "check_readiness", return_value={"external_writes": 0}) as check:
            response = request("/api/readiness", HTTP_X_GOOG_IAP_JWT_ASSERTION="assertion")
        check.assert_called_once_with("assertion", teaching_provider_configured=False)
        self.assertEqual(response["headers"]["Cache-Control"], "no-store")
