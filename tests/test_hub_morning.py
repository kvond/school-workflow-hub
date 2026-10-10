"""Morning lists and receipts use fictional records only."""
import copy
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from hub_v2 import app, morning

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

class ClosedAssignmentRecheckTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
        self.ready = {"status": "READY"}
        self.mark = {"configured": True, "entered_at": {"at": "2026-10-02T12:46:00Z"}}
        self.form = copy.deepcopy(FORM)
        self.form.update(grade_readiness=self.ready,
            window={"from": "2026-09-16T04:00:00Z", "at": "2026-10-01T03:59:59Z"})
        self.form["students"][0].update(submitted_at="2026-09-22T14:00:00Z", files=[{
            "id": "FICTIONAL_CURRENT_FILE_123", "mime": morning.completion.SLIDES,
            "source_form_id": self.form["form_id"], "source_response_id": "ORIGINAL_RESPONSE_123",
            "source_submitted_at": "2026-09-22T14:00:00Z"}])
        self.late = {**copy.deepcopy(ROW), "last": "Brook", "late": True,
                     "submitted_at": "2026-10-07T19:43:00Z", "files": [{
                         "id": "FICTIONAL_LATE_FILE_123", "mime": morning.completion.SLIDES,
                         "source_form_id": self.form["form_id"], "source_response_id": "LATER_RESPONSE_123",
                         "source_submitted_at": "2026-10-07T19:43:00Z"}]}
        self.form["late"] = [self.late]
        self.form["missing"] = [{**ROW, "last": "Cole"}]
        self.found = {"lessons": [{"topic": "Matter", "forms": [self.form]}], "problems": []}
        self.agenda = Mock(classes={"biology": {"name": "Biology"}})
        self.config = {"HUB_IC_ASSIGNMENT_MAP": json.dumps([{
            "class_key": "biology", "form_id": self.form["form_id"], "targets": []}])}

    def make(self, scorer, mark=None, ready=None):
        with patch.object(morning, "datetime", wraps=datetime) as clock, \
                patch.object(morning.ledger, "read", return_value=self.mark if mark is None else mark), \
                patch.object(morning.completion, "assignment_readiness", return_value=ready or self.ready):
            clock.now.return_value = self.now
            return morning.make_list("token", self.config, self.agenda, "biology", "Wk 02",
                                     self.form["form_id"], Mock(return_value=self.found), scorer)

    def test_current_file_revision_is_rechecked_without_a_new_form_event(self):
        before = copy.deepcopy(self.found)
        versions = {"FICTIONAL_CURRENT_FILE_123": "2", "FICTIONAL_LATE_FILE_123": "1"}

        def current_score(token, agenda, cls, week, fid, file_id, found, *, include_late=False):
            self.assertTrue(include_late)
            self.assertIs(found, self.found)
            return {"notes_score": 8, "revision_score": 9, "file_updated": "2026-10-07T12:00:00Z",
                    "observed_version": versions[file_id]}

        with patch.object(morning.completion, "score_file", side_effect=current_score) as score:
            first = self.make(app._score_rows)
            versions["FICTIONAL_CURRENT_FILE_123"] = "3"
            second = self.make(app._score_rows)
        self.assertEqual(first["code"], "ready")
        self.assertEqual(second["code"], "ready")
        self.assertNotEqual(first["revision"], second["revision"])
        self.assertEqual(score.call_count, 4)
        self.assertEqual({r["last"]: r["kind"] for r in first["list"]["rows"]},
                         {"Avery": "score", "Brook": "score", "Cole": "none"})
        self.assertEqual(self.found, before)

    def test_recheck_preserves_lateness_and_source_provenance_and_creates_no_absence_zero(self):
        before = copy.deepcopy(self.found)
        scorer = Mock(return_value=[(self.form["students"][0], {"notes_score": 8}),
                                    (self.late, {"revision_score": 9})])
        result = self.make(scorer)
        self.assertEqual(scorer.call_args.kwargs, {"include_late": True})
        self.assertIs(scorer.call_args.args[-1], self.found)
        self.assertEqual(self.found, before)
        self.assertEqual(result["list"]["status"], "closed")
        self.assertFalse(any(r["kind"] == "zero" for r in result["list"]["rows"]))
        self.assertEqual(next(r for r in result["list"]["rows"] if r["last"] == "Brook")["revision"], 9)

    def replacement(self, ident="FICTIONAL_REPLACEMENT_FILE_123"):
        row = self.form["students"][0]
        row.update(source="form", matched_by="email", email="s.ann.avery@school.example")
        snapshot = {"source": "form", "matched_by": "email", "email": row["email"], "late": True,
                    "submitted_at": "2026-10-07T19:43:00Z", "files": [{"id": ident,
                        "mime": morning.completion.SLIDES, "source_form_id": self.form["form_id"],
                        "source_response_id": ident + "_RESPONSE", "source_submitted_at": "2026-10-07T19:43:00Z"}]}
        row["earlier"] = [snapshot]
        return snapshot

    def test_later_replacement_hidden_in_earlier_is_rechecked_once_not_the_old_on_time_file(self):
        replacement = self.replacement()
        before = copy.deepcopy(self.found)
        with patch.object(morning.completion, "score_file", return_value={"notes_score": 8, "revision_score": 10}) as score:
            result = self.make(app._score_rows)
        self.assertEqual(result["code"], "ready")
        ids = [c.args[5] for c in score.call_args_list]
        self.assertEqual(sorted(ids), ["FICTIONAL_LATE_FILE_123", replacement["files"][0]["id"]])
        self.assertNotIn("FICTIONAL_CURRENT_FILE_123", ids)
        self.assertEqual(sum(r["last"] == "Avery" for r in result["list"]["rows"]), 1)
        self.assertFalse(any(r["kind"] == "zero" for r in result["list"]["rows"]))
        self.assertEqual(self.found, before)

    def test_ambiguous_replacement_dates_and_unverified_history_hold_before_scoring(self):
        replacement = self.replacement()
        row = self.form["students"][0]
        tied = copy.deepcopy(replacement)
        tied["files"][0].update(id="ANOTHER_REPLACEMENT_123", source_response_id="ANOTHER_RESPONSE_123")
        row["earlier"].append(tied)
        scorer = Mock()
        held = self.make(scorer)
        self.assertEqual(held["code"], "coverage_incomplete")
        self.assertIn("Biology · Wk 02 · Matter · Avery, Ann (A4 Fictional)", held["problem"])
        scorer.assert_not_called()
        row["earlier"] = [{**replacement, "source": "folder", "matched_by": "file name"}]
        self.assertEqual(self.make(scorer)["code"], "coverage_incomplete")
        scorer.assert_not_called()

    def test_absence_checks_excused_and_duplicate_identities_keep_their_holds(self):
        self.form["missing"] += [{**ROW, "last": "Dale", "unsorted": True}, {**ROW, "last": "Eve"}]
        self.form["elsewhere"] = [{"who": "Eve, Ann", "section": ROW["section"]}]
        self.form["excused"] = [{**ROW, "last": "Fox"}]
        self.form["late"].append(copy.deepcopy(self.form["students"][0]))
        values = morning.rows(self.form, [(self.form["students"][0], {"notes_score": 8}),
                                         (self.late, {"revision_score": 9})], self.now, recheck=True)
        self.assertEqual({r["last"]: r["kind"] for r in values}, {
            "Avery": "check", "Brook": "score", "Cole": "none", "Dale": "check", "Eve": "check", "Fox": "excused"})
        self.assertTrue(all(r["notes"] is None and r["revision"] is None for r in values if r["kind"] != "score"))

    def test_held_current_sources_never_become_no_scores_or_a_zero_list(self):
        scorer = Mock(return_value=[(r, {"problem": "needs review"})
                                    for r in self.form["students"] + self.form["late"]])
        self.assertEqual(self.make(scorer), {"code": "source_scores_held", "source_holds": {"checks": 2}})
        blocked = Mock(return_value=[(self.late, {"code": "deck_not_ready", "problem": "Copy needs codes"})])
        self.assertEqual(self.make(blocked)["code"], "coverage_incomplete")
        untouched = Mock()
        self.assertEqual(self.make(untouched, ready={"status": "NOT_READY", "problem": "Teacher hold"})["code"], "coverage_incomplete")
        untouched.assert_not_called()
        self.found["other_files"] = [{"files": [{"id": "UNRESOLVED_SOURCE_123"}]}]
        self.assertEqual(self.make(untouched)["code"], "coverage_incomplete")
        untouched.assert_not_called()

    def test_closed_first_entry_and_open_assignments_keep_default_candidate_and_zero_rules(self):
        scorer = Mock(return_value=[(self.form["students"][0], {"notes_score": 8})])
        result = self.make(scorer, mark={"configured": True})
        self.assertEqual(scorer.call_args.kwargs, {})
        self.assertEqual([r["kind"] for r in result["list"]["rows"]], ["score", "zero", "zero"])
        self.form["window"]["at"] = "2026-10-14T03:59:59Z"
        result = self.make(scorer)
        self.assertEqual(scorer.call_args.kwargs, {})
        self.assertEqual([r["kind"] for r in result["list"]["rows"]], ["score", "zero", "zero"])

    def test_predicate_requires_known_closed_window_and_valid_nonfuture_entry(self):
        self.assertTrue(morning.closed_recheck(self.form, self.mark, self.now))
        for mark in ({"configured": False, "entered_at": self.mark["entered_at"]},
                     {**self.mark, "problem": "unreadable"}, {"configured": True},
                     {**self.mark, "entered_at": {"at": "not a timestamp"}},
                     {**self.mark, "entered_at": {"at": "2026-09-29T00:00:00Z"}},
                     {**self.mark, "entered_at": {"at": "2026-10-12T00:00:00Z"}}):
            self.assertFalse(morning.closed_recheck(self.form, mark, self.now))
        for window in (None, {"at": "unknown"}, {"at": "2026-10-14T00:00:00Z"}):
            self.assertFalse(morning.closed_recheck({**self.form, "window": window}, self.mark, self.now))
        for mark in ({"configured": False}, {**self.mark, "entered_at": {"at": "unknown"}}):
            scorer = Mock()
            self.assertEqual(self.make(scorer, mark=mark)["code"], "receipt_unconfirmed")
            scorer.assert_not_called()


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
