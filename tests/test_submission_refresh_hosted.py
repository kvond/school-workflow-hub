"""Authenticated read-only refresh boundary; all inputs and readers are synthetic."""
import io
import json
import unittest
from unittest.mock import patch

from school_workflow_hub.adapters import submission_refresh as refresh
from school_workflow_hub.adapters.hosted_agenda import ConnectionError
from school_workflow_hub.web import application


class RefreshEndpointTests(unittest.TestCase):
    def call(self, *, method="POST", origin="https://hub.example", query="", body=None,
             content_type="application/json"):
        data = json.dumps(body if body is not None else {
            "class_key": "forensics", "assignment_key": "all"}).encode()
        captured = {}
        environ = {"REQUEST_METHOD": method, "PATH_INFO": "/api/submission-monitor/refresh",
            "HTTP_ORIGIN": origin, "QUERY_STRING": query, "CONTENT_TYPE": content_type,
            "CONTENT_LENGTH": str(len(data)), "wsgi.input": io.BytesIO(data),
            "HTTP_X_GOOG_IAP_JWT_ASSERTION": "synthetic-signed-assertion"}
        with patch.dict("os.environ", {"HUB_STATE_PROBE_ORIGINS": "https://hub.example"}):
            raw = application(environ, lambda status, headers: captured.update(status=status, headers=headers))
        captured["body"] = json.loads(b"".join(raw))
        return captured

    def test_refresh_dispatches_to_read_only_adapter_and_preserves_scope(self):
        expected = {"schema_version": refresh.SCHEMA, "external_writes": 0,
                    "live_model_calls": 0, "files": []}
        with patch.object(refresh, "refresh", return_value=expected) as adapter:
            result = self.call(body={"class_key": "forensics", "assignment_key": "forensics:F03-A"})
        self.assertEqual(result["status"], "200 OK")
        self.assertEqual(result["body"], expected)
        self.assertEqual(adapter.call_args.args[:2], ("synthetic-signed-assertion",
            {"class_key": "forensics", "assignment_key": "forensics:F03-A"}))
        self.assertIn(("Cache-Control", "no-store"), result["headers"])

    def test_get_and_cross_origin_or_query_requests_never_start_discovery(self):
        with patch.object(refresh, "refresh") as adapter:
            for kwargs, status in [({"method": "GET"}, "405 Method Not Allowed"),
                    ({"origin": "https://other.example"}, "403 Forbidden"),
                    ({"query": "date=2026-09-14"}, "403 Forbidden"),
                    ({"content_type": "text/plain"}, "403 Forbidden")]:
                self.assertEqual(self.call(**kwargs)["status"], status)
            adapter.assert_not_called()

    def test_sign_in_is_required_before_source_or_token_access(self):
        with patch.object(refresh, "read_records", side_effect=ConnectionError("sign_in_required")), \
             patch.object(refresh, "service_token") as token:
            result = self.call()
        self.assertEqual(result["status"], "403 Forbidden")
        token.assert_not_called()

    def test_rejects_browser_source_ids_and_date_scope(self):
        with patch.object(refresh, "read_records") as records:
            for body in [{"class_key": "forensics", "assignment_key": "all", "folder_id": "x" * 24},
                         {"class_key": "forensics", "date": "2026-09-14"},
                         {"class_key": "unknown", "assignment_key": "all"}]:
                self.assertEqual(self.call(body=body)["status"], "400 Bad Request")
            records.assert_not_called()

    def test_errors_do_not_expose_provider_messages_or_private_content(self):
        with patch.object(refresh, "refresh", side_effect=ConnectionError("private student material")):
            result = self.call()
        self.assertEqual(result["status"], "503 Service Unavailable")
        self.assertEqual(result["body"]["error"], "submission_refresh_unavailable")


if __name__ == "__main__":
    unittest.main()
