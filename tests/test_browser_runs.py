from io import BytesIO
import json
import unittest
from unittest.mock import patch

from school_workflow_hub.adapters import browser_runs as api
from school_workflow_hub.core.cloud_state import CloudState, key
from tests.test_cloud_state import MemoryCAS
from tests.test_hosted_agenda import CONFIG
from tests.test_web_hosting import request


class BrowserRunTests(unittest.TestCase):
    def setUp(self):
        self.port = MemoryCAS()
        self.config = {**CONFIG, "HUB_STATE_PROBE_ORIGINS": "https://hub.example"}
        self.addCleanup(patch.stopall)
        patch.dict("os.environ", self.config, clear=True).start()
        self.auth = patch.object(api, "authorize").start()
        self.backend = patch.object(api, "configured_store", return_value=self.port).start()
        self.worker = "11111111-1111-4111-8111-111111111111"
        self.cookie = self.get()["headers"]["Set-Cookie"].split(";", 1)[0]
        self.request_id = "fictional-request-0001"

    def get(self, workflow="roster", cookie=None, worker_id=None, **extra):
        return request("/api/browser-runs", QUERY_STRING="workflow=" + workflow + "&worker_id=" + (worker_id or self.worker),
                       HTTP_COOKIE=cookie or getattr(self, "cookie", ""),
                       HTTP_HOST="hub.example", HTTP_X_GOOG_IAP_JWT_ASSERTION="assertion", **extra)

    def post(self, payload, *, cookie=None, **extra):
        raw = json.dumps({"worker_id": self.worker, **payload}).encode()
        env = {"HTTP_COOKIE": self.cookie if cookie is None else cookie,
               "HTTP_HOST": "hub.example", "HTTP_ORIGIN": "https://hub.example",
               "HTTP_X_GOOG_IAP_JWT_ASSERTION": "assertion", "CONTENT_TYPE": "application/json",
               "CONTENT_LENGTH": str(len(raw)), "wsgi.input": BytesIO(raw)}
        return request("/api/browser-runs", "POST", **{**env, **extra})

    def claim(self, workflow="roster", request_id=None, **kwargs):
        return self.post({"workflow": workflow, "action": "claim",
                          "request_id": request_id or self.request_id}, **kwargs)

    def claim_body(self, workflow="roster"):
        response = self.claim(workflow)
        self.assertEqual(response["status"], "200 OK")
        return json.loads(response["body"])

    def report(self, workflow="roster"):
        return {"workflow": workflow, "outcome": "checked", "summary": "Fictional source checked.",
                "counts": {"checked": 3, "changed": 0, "needs_review": 0},
                "evidence": [{"kind": "source_read", "reference": "synthetic-source",
                              "checked_at": self.port.now.isoformat()}]}

    def finish(self, claim, report=None, workflow="roster", **kwargs):
        return self.post({"workflow": workflow, "action": "finish", "claim": claim,
                          "report": report or self.report(workflow)}, **kwargs)

    def test_get_sets_secure_cookie_without_creating_task_or_leaking_owner(self):
        result = request("/api/browser-runs", QUERY_STRING="workflow=roster&worker_id=" + self.worker)
        cookie = result["headers"]["Set-Cookie"]
        for flag in ("__Host-hub-worker=", "Path=/", "Secure", "HttpOnly", "SameSite=Strict"):
            self.assertIn(flag, cookie)
        self.assertNotIn("Domain", cookie)
        self.assertEqual(self.port.rows, {})
        data = json.loads(result["body"])
        self.assertFalse(data["run_enabled"])
        self.assertFalse(data["server_verified"])
        self.assertIsNone(data["active_claim"])
        self.assertNotIn(cookie.split(";", 1)[0].split("=", 1)[1].encode(), result["body"])

    def test_claim_is_canonical_and_reload_resumes_only_cookie_owner(self):
        data = self.claim_body()
        self.assertFalse(data["completed"])
        self.assertEqual(data["claim"]["task_id"], key("task", api.ROSTER_SYNC_SCOPE))
        own = json.loads(self.get()["body"])
        self.assertEqual(own["active_claim"], data["claim"])
        other = json.loads(self.get(cookie=api.COOKIE + "=" + "b" * 43)["body"])
        self.assertTrue(other["busy"])
        self.assertFalse(other["owned_by_this_browser"])
        self.assertIsNone(other["active_claim"])
        self.assertNotIn("owner", json.dumps(own))
        self.assertNotIn(self.cookie.split("=", 1)[1], json.dumps(own))

    def test_same_cookie_different_worker_cannot_adopt_or_finish_active_or_completed_run(self):
        claim = self.claim_body()["claim"]
        other = "22222222-2222-4222-8222-222222222222"
        status = json.loads(self.get(worker_id=other)["body"])
        self.assertTrue(status["busy"])
        self.assertFalse(status["owned_by_this_browser"])
        self.assertIsNone(status["active_claim"])
        other_claim = {"workflow": "roster", "action": "claim", "worker_id": other,
                       "request_id": self.request_id}
        self.assertEqual(self.post(other_claim)["status"], "409 Conflict")
        result = self.post({"workflow": "roster", "action": "finish", "worker_id": other,
                            "claim": claim, "report": self.report()})
        self.assertEqual(result["status"], "409 Conflict")
        other_claim["request_id"] = "fictional-request-0002"
        self.assertEqual(self.post(other_claim)["status"], "409 Conflict")
        self.finish(claim)
        other_claim["request_id"] = self.request_id
        self.assertEqual(self.post(other_claim)["status"], "409 Conflict")

    def test_worker_id_is_required_and_must_be_uuid_v4(self):
        for value in ("", "not-a-worker", "a" * 36, "11111111-1111-1111-1111-111111111111"):
            result = self.post({"workflow": "roster", "action": "claim", "request_id": self.request_id,
                                "worker_id": value})
            self.assertEqual(result["status"], "400 Bad Request")
        result = request("/api/browser-runs", QUERY_STRING="workflow=roster")
        self.assertEqual(result["status"], "400 Bad Request")

    def test_new_request_is_busy_even_in_same_browser(self):
        first = self.claim_body()["claim"]
        repeated = json.loads(self.claim()["body"])["claim"]
        self.assertEqual(first, repeated)
        for cookie in (self.cookie, api.COOKIE + "=" + "b" * 43):
            result = self.claim(request_id="fictional-request-0002", cookie=cookie)
            self.assertEqual(result["status"], "409 Conflict")
            self.assertEqual(json.loads(result["body"])["error"], "browser_worker_busy")

    def test_uncertain_claim_retry_recovers_without_duplicate_run(self):
        store = CloudState(self.port)
        store.create_task(api.ROSTER_SYNC_SCOPE)
        self.port.fail_after_commit = True
        self.assertEqual(self.claim()["status"], "503 Service Unavailable")
        result = self.claim_body()
        self.assertEqual(result["claim"]["fence"], 1)
        self.assertEqual(sum(k.startswith("run_") for k in self.port.rows), 1)

    def test_renewed_claim_recovers_after_lost_response_and_old_handle_can_finish(self):
        claim = self.claim_body()["claim"]
        self.port.advance(500)
        self.port.fail_after_commit = True
        result = self.post({"workflow": "roster", "action": "renew", "claim": claim})
        self.assertEqual(result["status"], "503 Service Unavailable")
        recovered = json.loads(self.claim()["body"])["claim"]
        self.assertNotEqual(recovered["expires_at"], claim["expires_at"])
        self.port.advance(450)
        self.assertEqual(self.finish(claim)["status"], "200 OK")

    def test_finish_persists_attributed_report_without_baseline_or_school_verification(self):
        claim = self.claim_body()["claim"]
        result = self.finish(claim)
        self.assertEqual(result["status"], "200 OK")
        body = json.loads(result["body"])
        self.assertEqual(body["receipt"]["provenance"], "browser_report")
        self.assertFalse(body["receipt"]["server_verified"])
        self.assertEqual(body["external_writes"], 0)
        task = CloudState(self.port).read_task(claim["task_id"])
        self.assertIsNone(task["baseline"])
        self.assertEqual(task["status"], "Needs you")
        record = self.port.rows[task["last_result"]["record_ids"]["browser_report"]].value["payload"]
        self.assertEqual(record["provenance"], "browser_report")
        self.assertFalse(record["server_verified"])
        status = json.loads(self.get()["body"])
        self.assertEqual(status["last_report"], body["receipt"])
        self.assertNotIn("Fictional source", json.dumps(status))
        self.assertNotIn("synthetic-source", json.dumps(status))

    def test_lost_finish_response_retries_identical_payload_and_rejects_changed_result(self):
        claim = self.claim_body()["claim"]
        report = self.report()
        self.port.fail_after_commit = True
        self.assertEqual(self.finish(claim, report)["status"], "503 Service Unavailable")
        size = len(self.port.rows)
        self.port.advance(40)
        result = self.finish(claim, report)
        self.assertEqual(result["status"], "200 OK")
        self.assertEqual(len(self.port.rows), size)
        recovered = self.claim_body()
        self.assertTrue(recovered["completed"])
        self.assertEqual(recovered["receipt"], json.loads(result["body"])["receipt"])
        self.assertEqual(self.finish(claim, {**report, "summary": "Different report"})["status"], "409 Conflict")

    def test_other_cookie_cannot_take_completed_claim_or_finish_it(self):
        claim = self.claim_body()["claim"]
        report = self.report()
        self.finish(claim, report)
        other = api.COOKIE + "=" + "b" * 43
        self.assertEqual(self.claim(cookie=other)["status"], "409 Conflict")
        self.assertEqual(self.finish(claim, report, cookie=other)["status"], "409 Conflict")

    def test_completed_claim_requires_record_readback(self):
        claim = self.claim_body()["claim"]
        self.finish(claim)
        task = self.port.rows[claim["task_id"]].value
        del self.port.rows[task["last_result"]["record_ids"]["browser_report"]]
        self.assertEqual(self.claim()["status"], "503 Service Unavailable")

    def test_forged_claim_fields_and_owner_are_rejected(self):
        claim = self.claim_body()["claim"]
        for altered in ({**claim, "owner": "attacker"}, {**claim, "fence": claim["fence"] + 1},
                        {**claim, "input_hash": "a" * 64}, {**claim, "run_id": "run_" + "a" * 64},
                        {**claim, "task_id": key("task", api.SCOPES["grading"])},
                        {**claim, "expires_at": "not-a-time"}):
            with self.subTest(altered=altered):
                result = self.post({"workflow": "roster", "action": "renew", "claim": altered})
                self.assertIn(result["status"], {"400 Bad Request", "409 Conflict"})
        self.assertEqual(self.finish(claim, cookie=api.COOKIE + "=" + "b" * 43)["status"], "409 Conflict")

    def test_expired_owner_cannot_publish_or_renew_after_takeover(self):
        old = self.claim_body()["claim"]
        self.port.advance(901)
        expired = json.loads(self.get()["body"])
        self.assertFalse(expired["busy"])
        self.assertIsNone(expired["active_claim"])
        self.assertTrue(expired["recovery_required"])
        self.assertEqual(expired["next_action"], "expired_run_needs_recovery")
        self.assertEqual(self.claim()["status"], "409 Conflict")
        new = self.claim(request_id="fictional-request-0002", cookie=api.COOKIE + "=" + "b" * 43)
        self.assertEqual(new["status"], "200 OK")
        self.assertTrue(json.loads(new["body"])["recovery_required"])
        reloaded = json.loads(self.get(cookie=api.COOKIE + "=" + "b" * 43)["body"])
        self.assertTrue(reloaded["recovery_required"])
        self.assertEqual(self.finish(old)["status"], "409 Conflict")
        self.assertEqual(self.post({"workflow": "roster", "action": "renew", "claim": old})["status"], "409 Conflict")

    def test_release_preserves_history_and_old_request_does_not_reacquire(self):
        claim = self.claim_body()["claim"]
        result = self.post({"workflow": "roster", "action": "release", "claim": claim, "reason": "partial_scan"})
        self.assertEqual(result["status"], "200 OK")
        task = CloudState(self.port).read_task(claim["task_id"])
        self.assertEqual(task["next_action"], "partial_scan")
        self.assertIsNone(task["baseline"])
        status = json.loads(self.get()["body"])
        self.assertTrue(status["recovery_required"])
        self.assertEqual(status["next_action"], "partial_scan")
        self.assertEqual(self.claim()["status"], "409 Conflict")
        self.assertEqual(self.finish(claim)["status"], "409 Conflict")

    def test_grading_scope_is_shared_and_posted_report_requires_readback(self):
        claim = self.claim_body("grading")["claim"]
        self.assertEqual(claim["task_id"], key("task", api.SCOPES["grading"]))
        report = self.report("grading")
        report.update(outcome="posted", counts={"checked": 3, "changed": 2, "needs_review": 0})
        self.assertEqual(self.finish(claim, report, "grading")["status"], "400 Bad Request")
        report["evidence"].append({"kind": "grade_readback", "reference": "synthetic-score-readback",
                                   "checked_at": self.port.now.isoformat()})
        result = self.finish(claim, report, "grading")
        self.assertEqual(result["status"], "200 OK")
        self.assertFalse(json.loads(result["body"])["server_verified"])

    def test_strict_json_handles_and_report_cannot_supply_baseline_or_verification(self):
        claim = self.claim_body()["claim"]
        report = self.report()
        invalid = [
            {"workflow": "roster", "action": "claim", "request_id": self.request_id, "owner": "x"},
            {"workflow": "roster", "action": "finish", "claim": claim, "report": report, "baseline": {}},
            {"workflow": "roster", "action": "finish", "claim": claim, "report": {**report, "server_verified": True}},
            {"workflow": "roster", "action": "finish", "claim": claim, "report": {**report, "counts": {"checked": True, "changed": 0, "needs_review": 0}}},
            {"workflow": "roster", "action": "release", "claim": claim, "reason": "arbitrary-private-message"},
        ]
        for payload in invalid:
            self.assertEqual(self.post(payload)["status"], "400 Bad Request")
        raw = b'{"workflow":"roster","workflow":"grading","action":"claim","request_id":"fictional-request-0001"}'
        self.assertEqual(self.post({}, **{"CONTENT_LENGTH": str(len(raw)), "wsgi.input": BytesIO(raw)})["status"], "400 Bad Request")
        self.assertEqual(self.post({}, CONTENT_LENGTH="100001")["status"], "400 Bad Request")

    def test_unsigned_and_cross_origin_requests_never_access_state(self):
        self.backend.reset_mock()
        from school_workflow_hub.adapters.hosted_agenda import ConnectionError
        self.auth.side_effect = ConnectionError("sign_in_required")
        self.assertEqual(self.get()["status"], "403 Forbidden")
        self.assertEqual(self.claim()["status"], "403 Forbidden")
        self.backend.assert_not_called()
        self.auth.side_effect = None
        for extra in ({"HTTP_ORIGIN": "https://attacker.example"}, {"HTTP_ORIGIN": "http://hub.example"},
                      {"HTTP_ORIGIN": "https://hub.example/"}, {"HTTP_ORIGIN": ""},
                      {"HTTP_HOST": "attacker.example"}, {"CONTENT_TYPE": "text/plain"},
                      {"QUERY_STRING": "workflow=roster"}):
            self.assertEqual(self.claim(**extra)["status"], "403 Forbidden")
        self.assertEqual(self.claim(cookie="")["status"], "403 Forbidden")
        self.backend.assert_not_called()

    def test_malformed_query_cookie_and_methods_are_rejected(self):
        for query in ("", "workflow=", "workflow=roster&workflow=grading", "workflow=roster&other=1", "workflow=unknown"):
            result = request("/api/browser-runs", QUERY_STRING=query)
            self.assertEqual(result["status"], "400 Bad Request")
        self.assertEqual(self.get(cookie=self.cookie + "; " + self.cookie)["status"], "403 Forbidden")
        for method in ("HEAD", "PUT", "PATCH", "DELETE"):
            self.assertEqual(request("/api/browser-runs", method)["status"], "405 Method Not Allowed")


if __name__ == "__main__":
    unittest.main()
