"""Exact partial IC maps on fictional sources; no school APIs or grade writes."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

from hub_v2 import app, completion, google, ledger, morning, morning_entry
from test_morning_eligible_entry import TARGET, RUN

NOW = datetime(2026, 10, 11, 14, tzinfo=timezone.utc)
FORM = "FICTIONAL_REASONING_FORM_123"
READY = {"status": "READY"}


def target(section="A2 Fictional", column="notes"):
    value = deepcopy(TARGET)
    sid = "101" if section == "A2 Fictional" else "102"
    aid = ("301" if column == "notes" else "302") if sid == "101" else ("303" if column == "notes" else "304")
    value.update(section=section, column=column, section_id=sid, assignment_id=aid,
                 score_group=str(int(aid) + 100), label_code="IDN" if column == "notes" else "IDR")
    value["url"] = ("https://ic.fictional.example/campus/sis/control-center/classroom/curriculum/resource/" + aid
        + "/view(secondaryRouteOutlet:multiscore/assignmentEditor/sectionID/" + sid + "/objectSectionID/" + aid
        + "/calendarID/854/structureID/854)?classroomSectionID=" + sid
        + "&classroomTermID=501&year=2027&schoolID=55&calendarID=854&structureID=854")
    return value


def student(section="A2 Fictional", last="Avery"):
    return {"last": last, "first": "Ann", "section": section, "matched": True,
            "files": [{"id": "FICTIONAL_ORIGINAL_" + last, "mime": google.PPTX}], "earlier": []}


class PartialComponentTests(unittest.TestCase):
    def setUp(self):
        self.students = [student(), student("A3 Fictional", "Brook")]
        self.form = {"form_id": FORM, "label": "Inductive and Deductive Reasoning", "problem": None,
            "grade_readiness": READY, "students": self.students, "late": [], "missing": [], "excused": [], "elsewhere": [],
            "window": {"from": "2026-09-16T04:00:00Z", "at": "2026-10-01T03:59:59Z"}}
        self.found = {"lessons": [{"topic": "Reasoning", "forms": [self.form]}], "problems": []}
        self.scored = [(self.students[0], {"notes_score": 7.8, "revision_score": 6.7}),
                       (self.students[1], {"notes_score": 8.2, "revision_score": 9.1})]
        self.agenda = Mock(classes={"forensics": {"name": "Forensics"}})
        self.notes = [target(), target("A3 Fictional")]

    def make(self, targets=None, *, config=None):
        config = config if config is not None else {"HUB_IC_ASSIGNMENT_MAP": json.dumps([
            {"class_key": "forensics", "form_id": FORM, "targets": targets if targets is not None else self.notes}])}
        with patch.object(morning, "datetime", wraps=datetime) as clock, \
             patch.object(ledger, "read", return_value={"configured": True}), \
             patch.object(completion, "assignment_readiness", return_value=READY):
            clock.now.return_value = NOW
            return morning.make_list("fictional", config, self.agenda, "forensics", "Wk 03", FORM,
                                     Mock(return_value=self.found), Mock(return_value=self.scored))

    def test_notes_only_uses_exact_measured_values_and_leaves_full_source_scores_unchanged(self):
        before = deepcopy((self.found, self.scored, self.notes))
        result = self.make()
        self.assertEqual(result["code"], "ready")
        self.assertTrue(result["partial_components"])
        self.assertEqual([(r["notes"], r["revision"]) for r in result["list"]["rows"]], [(7.8, None), (8.2, None)])
        self.assertEqual(result["targets"], self.notes)
        self.assertEqual((self.found, self.scored, self.notes), before)

    def test_revision_only_is_symmetric_without_renaming_or_inventing_notes(self):
        result = self.make([target(column="revision"), target("A3 Fictional", "revision")])
        self.assertEqual([(r["notes"], r["revision"]) for r in result["list"]["rows"]], [(None, 6.7), (None, 9.1)])
        self.assertTrue(all(t["column"] == "revision" for t in result["targets"]))

    def test_partial_map_omits_automatic_absence_zeros_but_preserves_a_measured_zero(self):
        self.form["missing"] = [student(last="Missing")]
        self.scored[0][1]["notes_score"] = 0
        result = self.make()
        self.assertFalse(any(r["kind"] == "zero" for r in result["list"]["rows"]))
        self.assertNotIn("Missing", [r["last"] for r in result["list"]["rows"]])
        self.assertEqual((result["list"]["rows"][0]["kind"], result["list"]["rows"][0]["notes"]), ("score", 0))
        self.assertEqual(self.form["missing"][0]["last"], "Missing")

    def test_entirely_unmapped_section_is_mapping_required_and_never_silently_omitted(self):
        for targets in [[target()], [target(), target(column="revision")]]:
            self.assertEqual(self.make(targets), {"code": "mapping_required"})

    def test_bad_duplicate_foreign_url_or_conflicting_section_bindings_fail_closed(self):
        changes = [lambda t: t.append(deepcopy(t[0])),
                   lambda t: t[0].update(url=t[0]["url"].replace("301/view", "999/view")),
                   lambda t: t[0].update(column="invented"),
                   lambda t: t[0].update(year="2028"),
                   lambda t: t[0].update(unexpected=True),
                   lambda t: t.append({**target(column="revision"), "section": "Different section"})]
        for mutate in changes:
            targets = deepcopy(self.notes); mutate(targets)
            with self.subTest(targets=targets):
                self.assertEqual(self.make(targets), {"code": "mapping_required"})
        for config in [{}, {"HUB_IC_ASSIGNMENT_MAP": json.dumps([
                {"class_key": "forensics", "form_id": FORM, "targets": self.notes}] * 2)}]:
            self.assertEqual(self.make(config=config), {"code": "mapping_required"})

    def test_full_maps_preserve_both_components_absence_zeros_and_original_revision_hash(self):
        self.form["missing"] = [student(last="Missing")]
        targets = [target(s, c) for s in ("A2 Fictional", "A3 Fictional") for c in ("notes", "revision")]
        expected = morning.rows(self.form, self.scored, NOW)
        result = self.make(targets)
        self.assertEqual(result["list"]["rows"], expected)
        self.assertEqual(result["targets"], targets)
        self.assertNotIn("partial_components", result)
        self.assertEqual(result["revision"], hashlib.sha256(json.dumps([self.form, self.scored], sort_keys=True, default=str).encode()).hexdigest())
        self.assertEqual(next(r for r in expected if r["last"] == "Missing")["kind"], "zero")

    def test_unmapped_only_component_is_not_a_zero_or_new_source_hold(self):
        for _, score in self.scored:
            score["notes_score"] = None
        self.assertEqual(self.make(), {"code": "no_scores"})
        self.assertEqual(self.scored[0][1]["revision_score"], 6.7)

    def test_real_checks_excused_and_unused_section_targets_are_preserved_or_filtered_without_new_scores(self):
        self.scored[1] = (self.students[1], {"problem": "Exact current original is unreadable"})
        self.form["excused"] = [student(last="Excused")]
        result = self.make()
        self.assertEqual([r["kind"] for r in result["list"]["rows"]], ["score", "check", "excused"])
        self.assertEqual(result["targets"], [self.notes[0]])
        self.assertEqual(result["list"]["rows"][1]["reason"], "Exact current original is unreadable")
        self.assertEqual(self.scored[1][1], {"problem": "Exact current original is unreadable"})

    def test_projected_list_passes_unchanged_js_list_and_exact_target_validators(self):
        result = self.make()
        repo = Path(__file__).resolve().parents[1]
        script = """const fs=require('node:fs'), E=require('./browser-extension/entry.js');
          global.HubIcEntry=E; const M=require('./browser-extension/morning.js');
          const data=JSON.parse(fs.readFileSync(0,'utf8'));
          E.validateList(data.list,Date.parse('2026-10-11T14:00:00Z'));
          M.targets(data.list,data.targets,'https://ic.fictional.example');
          if(M.need(data.list).some(pair=>pair[1]!=='notes'))throw Error('unexpected column');
        """
        checked = subprocess.run(["node", "-e", script], input=json.dumps(result), text=True, cwd=repo, capture_output=True, timeout=5)
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_projection_fingerprint_binds_full_original_assessment_and_current_mapping(self):
        before = self.make()
        self.scored[0][1]["revision_score"] = 8
        after = self.make()
        self.assertNotEqual(before["revision"], after["revision"])
        self.assertEqual(before["list"]["rows"], after["list"]["rows"])
        altered = deepcopy(self.notes); altered[0]["label_code"] = "OTHER_IDN"
        self.assertNotEqual(after["revision"], self.make(altered)["revision"])

    def test_partial_receipt_cannot_stamp_whole_form_or_accept_an_unprojected_revision(self):
        current = self.make()
        results = [{"list_id": current["list"]["list_id"], "form_id": FORM, "class_key": "forensics", "week": "Wk 03",
            "column": t["column"], "section": t["section"], "ic_title": "Induction Deduction Notes_S", "outcome": "entered",
            "certain": True, "counts": {"filled": 1, "raised": 0, "kept": 0, "left": 0, "not_saved": 0}, "at": NOW.isoformat()}
            for t in current["targets"]]
        payload = {"run_id": RUN, "scope": {"class": "forensics", "week": "Wk 03", "form": FORM},
                   "revision": current["revision"], "list": current["list"], "targets": current["targets"], "results": results}
        morning_entry.validate(payload, current, now=NOW)
        bad = deepcopy(payload); bad["list"]["rows"][0]["revision"] = 6.7
        with self.assertRaises(morning_entry.Refused):
            morning_entry.validate(bad, current, now=NOW)
        raw = json.dumps(payload).encode()
        env = {"REQUEST_METHOD": "POST", "PATH_INFO": "/v2/api/morning/entry", "HTTP_ORIGIN": "https://fictional.example",
               "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(raw)), "wsgi.input": io.BytesIO(raw)}
        status = []
        def saved(*args, **kwargs):
            return {**args[-1], "whole_assignment_recorded": kwargs["whole_assignment"]}
        with patch.object(google, "verify_teacher"), patch.object(google, "service_token", return_value="fictional"), \
             patch.object(app, "load_agenda", return_value=self.agenda), patch.object(morning, "make_list", return_value=current), \
             patch.object(morning_entry, "datetime", wraps=datetime) as clock, \
             patch.object(completion, "submitted_readiness", side_effect=AssertionError("No whole-form claim")), \
             patch.object(morning_entry, "record", side_effect=saved) as writer:
            clock.now.return_value = NOW
            response = json.loads(b"".join(app.application(env, lambda code, headers: status.append(code),
                {"HUB_STATE_PROBE_ORIGINS": "https://fictional.example"})))
        self.assertEqual(status, ["200 OK"])
        self.assertFalse(writer.call_args.kwargs["whole_assignment"])
        self.assertFalse(response["receipt"]["whole_assignment_recorded"])
        self.assertEqual(response["receipt"]["target_count"], 2)
