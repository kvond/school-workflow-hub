"""Morning lists and receipts use fictional records only."""
import copy
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from hub_v2 import morning

NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
ROW = {"last": "Avery", "first": "Ann", "section": "A4 Fictional", "matched": True, "files": []}
FORM = {"form_id": "FICTIONAL_FORM_1234", "label": "Matter", "problem": None, "students": [ROW], "missing": [], "late": [], "excused": [], "elsewhere": [],
        "window": {"from": "2026-09-30T04:00:00Z", "at": "2026-10-14T03:59:59Z"}}

def report():
    return {"day": "2026-10-01", "run_id": "11111111-1111-4111-8111-111111111111", "code": "finished", "roster": "unavailable", "items": [{"assignment": "Matter", "code": "read_back", "filled": 1, "raised": 1, "kept": 2, "left": 1, "reasons": {"flag": 1, "excused": 0, "check": 0, "name": 0, "missing": 0}}]}

class MorningTests(unittest.TestCase):
    def test_rows_keep_unscored_unsorted_wrong_lesson_and_excused_held(self):
        form = copy.deepcopy(FORM)
        form["missing"] = [{**ROW, "last": "Brook", "unsorted": True}, {**ROW, "last": "Cole"}, {**ROW, "last": "Dale"}]
        form["elsewhere"] = [{"who": "Cole, Ann", "section": ROW["section"]}]
        form["excused"] = [{**ROW, "last": "Eve"}]
        rows = {r["last"]: r for r in morning.rows(form, [(ROW, {"problem": "unreadable"})], NOW)}
        self.assertEqual({k: v["kind"] for k, v in rows.items()}, {"Avery": "check", "Brook": "check", "Cole": "check", "Dale": "zero", "Eve": "excused"})
        self.assertIsNone(rows["Avery"]["notes"])
        self.assertEqual(rows["Dale"]["notes"], 0)

    def test_before_open_and_duplicate_roster_never_become_zeros(self):
        form = copy.deepcopy(FORM)
        form["window"]["from"] = "2026-10-05T04:00:00Z"
        form["missing"] = [{**ROW, "last": "Brook"}, ROW]
        result = morning.rows(form, [(ROW, {"notes_score": 8, "revision_score": None})], NOW)
        self.assertEqual([r["kind"] for r in result], ["check", "none"])

    def test_missing_or_duplicate_binding_and_coverage_errors_fail_closed(self):
        agenda = Mock(classes={"biology": {"name": "Biology"}})
        found = {"lessons": [{"topic": "Matter", "forms": [FORM]}], "problems": []}
        loader = Mock(return_value=found)
        scorer = Mock(return_value=[(ROW, {"notes_score": 8, "revision_score": None})])
        binding = {"class_key": "biology", "form_id": FORM["form_id"], "targets": []}
        with patch.object(morning.ledger, "read", return_value={"configured": True}):
            for bindings in ([], [binding, binding]):
                result = morning.make_list("t", {"HUB_IC_ASSIGNMENT_MAP": json.dumps(bindings)}, agenda, "biology", "Wk 05", FORM["form_id"], loader, scorer)
                self.assertEqual(result["code"], "mapping_required")
            found["problems"] = ["roster unavailable"]
            self.assertEqual(morning.make_list("t", {}, agenda, "biology", "Wk 05", FORM["form_id"], loader, scorer)["code"], "coverage_incomplete")
        self.assertTrue(loader.call_args.kwargs["fresh"])

    def test_receipts_accept_only_counts_and_allowlisted_reasons(self):
        self.assertEqual(morning.validate_report(report()), report())
        for mutate in (lambda r: r.update(names=["Private Person"]), lambda r: r["items"][0].update(filled=True), lambda r: r["items"][0].update(code="raw provider error"), lambda r: r["items"][0]["reasons"].update(student="Private Person")):
            value = report(); mutate(value)
            with self.assertRaises(ValueError): morning.validate_report(value)

    def test_receipt_retry_is_read_only(self):
        with patch.object(morning, "reports", return_value=[report()]), patch.object(morning.google, "append_rows") as append:
            morning.record("t", "w", {}, report())
            append.assert_not_called()

    def test_receipt_requires_destination_readback(self):
        with patch.object(morning, "reports", return_value=[]), patch.object(morning.ledger, "find", return_value={"id": "FICTIONAL"}), patch.object(morning.ledger, "_first_tab", return_value="'Sheet1'"), patch.object(morning.google, "append_rows") as append:
            with self.assertRaises(morning.google.GoogleError): morning.record("t", "w", {}, report())
            append.assert_called_once()

class MorningEndpointTests(unittest.TestCase):
    def request(self, method, path, origin=None):
        import io
        from hub_v2 import app
        payload = json.dumps(report()).encode()
        env = {"REQUEST_METHOD": method, "PATH_INFO": path, "HTTP_ORIGIN": origin,
               "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(payload)),
               "wsgi.input": io.BytesIO(payload)}
        status = []
        body = app.application(env, lambda code, headers: status.append(code),
                               {"HUB_STATE_PROBE_ORIGINS": "https://hub.example.test"})
        return status[0], json.loads(b"".join(body))

    def test_reports_require_teacher_authentication_and_exact_origin(self):
        with patch.object(morning.google, "verify_teacher", side_effect=morning.google.SignInRequired), patch.object(morning, "record") as record:
            self.assertEqual(self.request("POST", "/v2/api/morning/reports", "https://hub.example.test")[0], "401 Unauthorized")
            self.assertEqual(self.request("GET", "/v2/api/morning/reports")[0], "401 Unauthorized")
            record.assert_not_called()
        with patch.object(morning.google, "verify_teacher"), patch.object(morning, "record") as record:
            self.assertEqual(self.request("POST", "/v2/api/morning/reports", "https://other.example.test")[0], "403 Forbidden")
            record.assert_not_called()

    def test_authenticated_report_saves_only_a_validated_receipt(self):
        with patch.object(morning.google, "verify_teacher"), patch.object(morning.google, "service_token", return_value="fictional"), patch.object(morning, "record") as record:
            self.assertEqual(self.request("POST", "/v2/api/morning/reports", "https://hub.example.test"), ("200 OK", {"recorded": True}))
            self.assertEqual(record.call_args.args[-1], report())

if __name__ == '__main__': unittest.main()
