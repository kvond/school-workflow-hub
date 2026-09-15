from io import BytesIO
import json
import unittest
from unittest.mock import patch

from school_workflow_hub.adapters.storage_probe import read_probe, run_probe
from school_workflow_hub.core.cloud_state import StoreUnavailable
from tests.test_cloud_state import MemoryCAS
from tests.test_hosted_agenda import CONFIG
from tests.test_web_hosting import request


class StorageProbeTests(unittest.TestCase):
    def test_real_port_contract_roundtrip_retry_and_changed_revision(self):
        port = MemoryCAS()
        first = run_probe(lambda: port, "fictional-probe-1", "revision-one")
        self.assertTrue(first["write_readback_verified"])
        self.assertTrue(first["duplicate_claim_rejected"])
        self.assertFalse(first["persistent_storage_verified"])
        count = len(port.rows)
        run_probe(lambda: port, "fictional-probe-1", "revision-one")
        self.assertEqual(len(port.rows), count)
        later = read_probe(lambda: port, "fictional-probe-1", "revision-two")
        self.assertTrue(later["persistent_storage_verified"])
        self.assertEqual(later["student_records_processed"], 0)
        self.assertFalse(later["run_enabled"])

    def test_probe_missing_or_incomplete_cannot_verify_storage(self):
        with self.assertRaises(StoreUnavailable):
            read_probe(MemoryCAS, "fictional-probe-1", "revision-one")

    def post(self, **extra):
        payload = b'{"action":"verify","probe_id":"fictional-probe-1"}'
        env = {"CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(payload)),
               "wsgi.input": BytesIO(payload), "HTTP_ORIGIN": "https://example.run.app",
               "HTTP_X_GOOG_IAP_JWT_ASSERTION": "assertion"}
        return request("/api/storage-check", "POST", **{**env, **extra})

    def test_only_signed_same_origin_exact_fictional_probe_is_accepted(self):
        config = {**CONFIG, "K_REVISION": "revision-one", "HUB_STATE_PROBE_ORIGINS": "https://example.run.app"}
        with patch.dict("os.environ", config, clear=True), \
                patch("school_workflow_hub.adapters.hosted_agenda.authorize"), \
                patch("school_workflow_hub.adapters.storage_probe.run_probe", return_value={"school_writes": 0}) as probe:
            self.assertEqual(self.post()["status"], "200 OK")
            probe.assert_called_once()
            probe.reset_mock()
            for extra in ({"HTTP_ORIGIN": "https://attacker.example"}, {"HTTP_ORIGIN": ""},
                          {"CONTENT_TYPE": "text/plain"}, {"QUERY_STRING": "mode=real"}):
                self.assertEqual(self.post(**extra)["status"], "403 Forbidden")
            self.assertEqual(self.post(CONTENT_LENGTH="1000000")["status"], "400 Bad Request")
            probe.assert_not_called()

    def test_unsigned_post_never_uses_runtime_credentials(self):
        with patch.dict("os.environ", CONFIG, clear=True), \
                patch("school_workflow_hub.adapters.storage_probe.run_probe") as probe:
            result = self.post(HTTP_X_GOOG_IAP_JWT_ASSERTION=None)
        self.assertEqual(result["status"], "403 Forbidden")
        probe.assert_not_called()

    def test_readiness_flag_requires_redeploy_readback_and_never_enables_execution(self):
        from school_workflow_hub.adapters.hosted_agenda import check_readiness
        config = {**CONFIG, "HUB_STATE_BACKEND": "firestore", "HUB_STATE_ACCEPTANCE_PROBE": "fictional-probe-1",
                  "K_REVISION": "revision-two"}
        with patch("school_workflow_hub.adapters.hosted_agenda.authorize"), \
                patch("school_workflow_hub.adapters.hosted_agenda.service_token"), \
                patch("school_workflow_hub.adapters.hosted_agenda.SheetsReader") as reader, \
                patch("school_workflow_hub.adapters.storage_probe.read_probe", return_value={
                    "status": "restart_verified", "persistent_storage_verified": True}):
            reader.return_value.read.return_value = {"sheets": []}
            result = check_readiness("assertion", config)
        self.assertTrue(result["persistent_storage_verified"])
        self.assertFalse(result["save_enabled"])
        self.assertFalse(result["run_enabled"])

    def test_scope_rejects_links_and_student_payload(self):
        with self.assertRaises(ValueError):
            run_probe(MemoryCAS, "https://example.com/student", "revision-one")
