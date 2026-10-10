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
        with patch.object(morning.ledger, "read", return_value={"configured": True}), \
                patch.object(morning.completion, "assignment_readiness", return_value={"status": "READY"}):
            for bindings in ([], [binding, binding]):
                result = morning.make_list("t", {"HUB_IC_ASSIGNMENT_MAP": json.dumps(bindings)}, agenda, "biology", "Wk 05", FORM["form_id"], loader, scorer)
                self.assertEqual(result["code"], "mapping_required")
            found["problems"] = ["roster unavailable"]
            self.assertEqual(morning.make_list("t", {}, agenda, "biology", "Wk 05", FORM["form_id"], loader, scorer)["code"], "coverage_incomplete")
        self.assertTrue(loader.call_args.kwargs["fresh"])

    def test_unmatched_source_files_block_scoring(self):
        agenda = Mock(classes={"biology": {"name": "Biology"}})
        found = {"lessons": [{"topic": "Matter", "forms": [FORM]}], "problems": [],
                 "other_files": [{"matched": True, "last": "Brook"}]}
        loader = Mock(return_value=found)
        scorer = Mock()
        binding = {"class_key": "biology", "form_id": FORM["form_id"], "targets": []}
        with patch.object(morning.ledger, "read", return_value={"configured": True}):
            result = morning.make_list("t", {"HUB_IC_ASSIGNMENT_MAP": json.dumps([binding])}, agenda,
                                       "biology", "Wk 05", FORM["form_id"], loader, scorer)
        self.assertEqual(result, {"code": "coverage_incomplete"})
        scorer.assert_not_called()

    def test_all_checked_source_rows_are_held_instead_of_reported_empty(self):
        agenda = Mock(classes={"biology": {"name": "Biology"}})
        found = {"lessons": [{"topic": "Matter", "forms": [FORM]}], "problems": []}
        binding = {"class_key": "biology", "form_id": FORM["form_id"], "targets": []}
        with patch.object(morning.ledger, "read", return_value={"configured": True}), \
                patch.object(morning.completion, "assignment_readiness", return_value={"status": "READY"}):
            for score in ({"problem": "unreadable"}, {"notes_score": 7, "note": "needs check"},
                          {"notes_score": None, "revision_score": None}):
                value = morning.make_list("t", {"HUB_IC_ASSIGNMENT_MAP": json.dumps([binding])}, agenda,
                    "biology", "Wk 05", FORM["form_id"], Mock(return_value=found), Mock(return_value=[(ROW, score)]))
                self.assertEqual(value, {"code": "source_scores_held", "source_holds": {"checks": 1}})
                self.assertNotIn("Ann", json.dumps(value))

    def test_empty_before_start_is_distinct_from_checked_submitted_work(self):
        form = {**FORM, "students": [], "missing": [ROW], "window": None}
        found = {"lessons": [{"topic": "Matter", "forms": [form]}], "problems": []}
        with patch.object(morning.ledger, "read", return_value={"configured": True}), \
                patch.object(morning.completion, "assignment_readiness", return_value={"status": "READY"}):
            value = morning.make_list("t", {}, Mock(classes={"biology": {"name": "Biology"}}), "biology",
                "Wk 05", form["form_id"], Mock(return_value=found), Mock(return_value=[]))
        self.assertEqual(value, {"code": "no_scores"})

    def test_mixed_safe_scores_and_checks_keep_the_existing_list_behavior(self):
        other = {**ROW, "last": "Brook"}
        form = {**FORM, "students": [ROW, other]}
        found = {"lessons": [{"topic": "Matter", "forms": [form]}], "problems": []}
        binding = {"class_key": "biology", "form_id": FORM["form_id"], "targets": []}
        with patch.object(morning.ledger, "read", return_value={"configured": True}), \
                patch.object(morning.completion, "assignment_readiness", return_value={"status": "READY"}):
            value = morning.make_list("t", {"HUB_IC_ASSIGNMENT_MAP": json.dumps([binding])},
                Mock(classes={"biology": {"name": "Biology"}}), "biology", "Wk 05", form["form_id"],
                Mock(return_value=found), Mock(return_value=[(ROW, {"notes_score": 8}), (other, {"problem": "held"})]))
        self.assertEqual(value["code"], "ready")
        self.assertEqual([r["kind"] for r in value["list"]["rows"]], ["score", "check"])

    def test_source_hold_receipt_is_counts_only_and_never_claims_ic_results(self):
        item = {**report()["items"][0], "class_name": "Biology", "week": "Wk 05", "code": "source_scores_held",
                "filled": 0, "raised": 0, "kept": 0, "left": 0,
                "reasons": {"flag": 0, "exempt": 0, "excused": 0, "check": 0, "name": 0, "missing": 0},
                "source_holds": {"checks": 2}}
        value = {**report(), "code": "source_scores_held", "items": [item]}
        self.assertEqual(morning.validate_report(value), value)
        for changes in ({"source_holds": {"checks": 0}}, {"source_holds": {"checks": True}},
                        {"source_holds": {"checks": 2, "names": ["Private Person"]}},
                        {"code": "no_scores"}, {"filled": 1}, {"class_name": None}):
            invalid = copy.deepcopy(value); invalid["items"][0].update(changes)
            with self.assertRaises(ValueError): morning.validate_report(invalid)
        with self.assertRaises(ValueError): morning.validate_report({**value, "code": "finished"})

    def test_receipts_accept_only_counts_and_allowlisted_reasons(self):
        self.assertEqual(morning.validate_report(report()), report())
        for mutate in (lambda r: r.update(names=["Private Person"]), lambda r: r["items"][0].update(filled=True), lambda r: r["items"][0].update(code="raw provider error"), lambda r: r["items"][0]["reasons"].update(student="Private Person")):
            value = report(); mutate(value)
            with self.assertRaises(ValueError): morning.validate_report(value)

    def test_receipt_retry_is_read_only(self):
        with patch.object(morning, "reports", return_value=[report()]), patch.object(morning.google, "append_rows") as append:
            morning.record("t", "w", {}, report())
            append.assert_not_called()

    def test_preflight_receipt_is_empty_and_contains_only_safe_bounded_diagnostics(self):
        value = {**report(), "code": "receipt_unconfirmed", "items": [],
                 "preflight": {"stage": "summary_publish", "cause": "hub_http_error", "status": 503}}
        self.assertEqual(morning.validate_report(value), value)
        for change in ({"cause": "Private Person"}, {"stage": "https://secret.example.test/"},
                       {"headers": {"Authorization": "private"}}, {"status": True}, {"status": 600}, {"cause": []}):
            invalid = copy.deepcopy(value); invalid["preflight"].update(change)
            with self.assertRaises(ValueError): morning.validate_report(invalid)
        for change in ({"code": "finished"}, {"items": report()["items"]}):
            with self.assertRaises(ValueError): morning.validate_report({**value, **change})
        with patch.object(morning, "reports", return_value=[value]), patch.object(morning.google, "append_rows") as append:
            morning.record("t", "w", {}, value)
            append.assert_not_called()

    def test_receipt_requires_destination_readback(self):
        with patch.object(morning, "reports", return_value=[]), patch.object(morning.ledger, "find", return_value={"id": "FICTIONAL"}), patch.object(morning.ledger, "_first_tab", return_value="'Sheet1'"), patch.object(morning.google, "append_rows") as append:
            with self.assertRaises(morning.google.GoogleError): morning.record("t", "w", {}, report())
            append.assert_called_once()

class MorningEndpointTests(unittest.TestCase):
    def request(self, method, path, origin=None, value=None):
        import io
        from hub_v2 import app
        payload = json.dumps(report() if value is None else value).encode()
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

    def test_report_route_accepts_safe_preflight_metadata_and_rejects_private_fields(self):
        value = {**report(), "code": "receipt_unconfirmed", "items": [],
                 "preflight": {"stage": "summary_publish", "cause": "hub_bridge_missing"}}
        with patch.object(morning.google, "verify_teacher"), patch.object(morning.google, "service_token", return_value="fictional"), patch.object(morning, "record") as record:
            self.assertEqual(self.request("POST", "/v2/api/morning/reports", "https://hub.example.test", value), ("200 OK", {"recorded": True}))
            self.assertEqual(record.call_args.args[-1], value)
            record.reset_mock()
            value["preflight"]["raw_error"] = "Private Person"
            self.assertEqual(self.request("POST", "/v2/api/morning/reports", "https://hub.example.test", value)[0], "400 Bad Request")
            record.assert_not_called()

if __name__ == '__main__': unittest.main()
