import json
import unittest
from unittest.mock import Mock, patch

from school_workflow_hub.adapters.firestore_state import FirestoreState, configured_store
from school_workflow_hub.core.cloud_state import Conflict, StoreUnavailable, key

STAMP = "2026-09-15T12:00:00.000001Z"


class FirestoreAdapterTests(unittest.TestCase):
    def store(self, transport):
        return FirestoreState("fictional-hub", token_provider=lambda: "private-token", transport=transport)

    def test_missing_and_found_out_of_order_are_bound_to_requested_ids(self):
        a, b = key("task", "a"), key("task", "b")
        store = self.store(lambda op, body: [
            {"missing": body["documents"][1], "readTime": STAMP},
            {"found": {"name": body["documents"][0], "updateTime": STAMP,
                       "fields": {"payload": {"stringValue": '{"status":"Queued"}'}}}, "readTime": STAMP}])
        snapshot = store.read([a, b])
        self.assertIsNone(snapshot.documents[b])
        self.assertEqual(snapshot.documents[a].value, {"status": "Queued"})
        self.assertEqual(snapshot.documents[a].version, STAMP)

    def test_partial_duplicate_foreign_and_malformed_reads_are_rejected(self):
        record_id = key("task", "a")
        for mode in ("empty", "duplicate", "foreign", "malformed"):
            def transport(op, body):
                row = {"missing": body["documents"][0], "readTime": STAMP}
                return {"empty": [], "duplicate": [row, row], "foreign": [{**row, "missing": "elsewhere"}],
                        "malformed": [{**row, "readTime": "yesterday"}]}[mode]
            with self.subTest(mode=mode), self.assertRaises(StoreUnavailable):
                self.store(transport).read([record_id])

    def test_commit_has_atomic_create_and_update_preconditions(self):
        sent = []
        def transport(operation, body):
            sent.append((operation, body))
            return {"commitTime": STAMP, "writeResults": [{}, {}]}
        self.store(transport).commit({key("task", "a"): ({"status": "Working"}, STAMP),
                                      key("run", "b"): ({"owner": "worker"}, None)})
        self.assertEqual(sent[0][0], "commit")
        self.assertEqual(sent[0][1]["writes"][0]["currentDocument"], {"updateTime": STAMP})
        self.assertEqual(sent[0][1]["writes"][1]["currentDocument"], {"exists": False})

    def test_commit_acknowledgment_must_cover_every_write(self):
        with self.assertRaises(StoreUnavailable):
            self.store(lambda op, body: {"commitTime": STAMP, "writeResults": []}).commit(
                {key("task", "a"): ({}, None)})

    def test_location_and_record_paths_cannot_escape_fixed_service(self):
        for project in ("https://attacker.example", "../other", "x", "valid-project/other"):
            with self.assertRaises(ValueError):
                FirestoreState(project, token_provider=lambda: "token")
        for record in ("../credentials", "task_abc", "/projects/other", "https://other"):
            with self.assertRaises(ValueError):
                self.store(None).read([record])

    def test_disabled_store_never_acquires_credentials(self):
        with patch("school_workflow_hub.adapters.firestore_state.attached_token") as token:
            with self.assertRaises(StoreUnavailable):
                configured_store({})
        token.assert_not_called()

    def http_store(self, status, body):
        response = unittest.mock.MagicMock()
        response.status_code = status
        response.raw.read.return_value = json.dumps(body).encode()
        response.iter_content.return_value = [json.dumps(body).encode()]
        return response

    def test_http_precondition_failure_is_contention_not_success(self):
        response = self.http_store(400, {"error": {"status": "FAILED_PRECONDITION"}})
        with patch("requests.Session") as session:
            session.return_value.__enter__.return_value.post.return_value = response
            with self.assertRaises(Conflict):
                self.store(None).commit({key("task", "a"): ({}, STAMP)})

    def test_redirect_permission_error_and_private_error_detail_are_redacted(self):
        for status in (302, 403, 429, 500):
            with self.subTest(status=status), patch("requests.Session") as session:
                session.return_value.__enter__.return_value.post.return_value = self.http_store(
                    status, {"secret": "private-source-data"})
                with self.assertRaisesRegex(StoreUnavailable, "^state_service_unavailable$"):
                    self.store(None).read([key("task", "a")])
                call = session.return_value.__enter__.return_value.post.call_args
                self.assertFalse(call.kwargs["allow_redirects"])
                self.assertTrue(call.args[0].startswith("https://firestore.googleapis.com/v1/"))

    def test_actual_transport_sends_only_current_token_to_fixed_endpoint(self):
        record_id = key("task", "a")
        store = self.store(None)
        response = self.http_store(200, [{"missing": store._name(record_id), "readTime": STAMP}])
        with patch("requests.Session") as session:
            session.return_value.__enter__.return_value.post.return_value = response
            store.read([record_id])
            call = session.return_value.__enter__.return_value.post.call_args
        self.assertEqual(call.kwargs["headers"], {"Authorization": "Bearer private-token"})
        self.assertEqual(call.kwargs["timeout"], (5, 15))

    def test_guarded_commit_checks_after_token_acquisition_and_before_single_request(self):
        events = []
        store = FirestoreState("fictional-hub", token_provider=lambda: events.append("token") or "private-token")
        guard = lambda: events.append("guard")
        response = self.http_store(200, {"commitTime": STAMP, "writeResults": [{}]})
        with patch("requests.Session") as session:
            session.return_value.__enter__.return_value.post.side_effect = lambda *a, **k: events.append("request") or response
            store.commit_guarded({key("task", "a"): ({}, None)}, before_write=guard)
        self.assertEqual(events, ["token", "guard", "request"])

    def test_guard_denial_after_slow_token_is_not_an_uncertain_write_or_retry(self):
        for error in (Conflict("source_window_closed"), StoreUnavailable("owner_read_failed")):
            events = []
            def token():
                events.append("slow-auth-finished")
                return "private-token"
            store = FirestoreState("fictional-hub", token_provider=token)
            guard = Mock(side_effect=error)
            with patch("requests.Session") as session, self.assertRaises(type(error)) as caught:
                store.commit_guarded({key("task", "a"): ({}, None)}, before_write=guard)
            self.assertIs(caught.exception, error)
            self.assertEqual(events, ["slow-auth-finished"])
            guard.assert_called_once_with()
            session.assert_not_called()

    def test_guarded_commit_never_retries_an_uncertain_provider_response(self):
        with patch("requests.Session") as session:
            post = session.return_value.__enter__.return_value.post
            post.side_effect = TimeoutError("fictional timeout")
            guard = Mock()
            with self.assertRaises(StoreUnavailable):
                self.store(None).commit_guarded({key("task", "a"): ({}, None)}, before_write=guard)
            post.assert_called_once()
            guard.assert_called_once_with()
