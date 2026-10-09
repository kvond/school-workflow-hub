"""Current contract filing on fictional complete sources and disposable Drive/CAS."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from hub_v2 import google, missing_work_contract_filing as filing, missing_work_contracts as policy
from hub_v2 import student_archive as archive
from school_workflow_hub.core.runs import content_hash
from tests import test_student_archive as archive_fixtures

ROOT, SOURCE = archive_fixtures.ROOT, archive_fixtures.SOURCE

STAMP = "2026-10-09T15:00:00Z"
NOW = "2026-10-09T15:05:00Z"
ASSIGNMENT = "biology:fictional-current-ecology"


class CurrentContractFilingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = archive_fixtures.StudentArchiveTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.drive, self.port, self.config = self.fixture.drive, self.fixture.port, self.fixture.config
        self.assertEqual(self.fixture.archive()["status"], "verified")
        self.owner = next(d.value for d in self.port.rows.values() if d.value.get("student_key") and d.value.get("items"))
        self.owner["identity_status"] = "identity_unverified"
        self.owner.update(printed=True, handed=False, existing_score=8)
        person = {k: self.owner[k] for k in ("first", "last", "section")}
        roster = {"source": "infinite_campus", "class_key": "biology", "complete": True,
            "expected_active_count": 1, "reference": "fictional-exact-current-IC", "checked_at": STAMP,
            "students": [{**person, "active": True, "start_date": "2026-09-01", "assignment_states": {
                ASSIGNMENT: {"assigned": True, "exempt": False}}}]}
        found = {"class_key": "biology", "week": "Wk 02", "problems": [], "folder_reviews": [],
            "other_files": [], "lessons": [{"row": 7, "topic": "Fictional ecology", "forms": [{
                "form_id": "FictionalEcologyForm001", "label": "Fictional ecology — Turn in", "problem": None,
                "students": [], "late": [], "missing": [person], "excused": [], "elsewhere": []}]}]}
        source = {"reference": "fictional-current-complete-source", "checked_at": STAMP,
            "complete": True, "found_sha256": content_hash(found), "checks": {k: True for k in policy.SOURCE_CHECKS}}
        due = {7: {"source": "infinite_campus", "reference": "fictional-original-due-and-flag-proof", "revision": "1",
            "checked_at": STAMP, "verified": True, "binding_verified": True, "row": 7,
            "assignment_key": ASSIGNMENT, "title": "Cycle 02 — Fictional ecology", "form_ids": ["FictionalEcologyForm001"],
            "due_date": "2026-09-16", "opened_date": "2026-09-01"}}
        private = {"class_key": "biology", "checked_at": STAMP, "reference": "fictional-existing-private-folder",
            "students": [{**person, "student_key": self.owner["student_key"], "student_folder_id": self.owner["drive_file_id"],
                "verified": True, "identity_status": "identity_unverified", "current_ic_name_match_verified": True,
                "private_folder_verified": True}]}
        self.input = {"found": found, "roster_proof": roster, "source_proof": source,
                      "due_proofs": due, "archive_proof": private, "checked_at": STAMP}
        self.plan = self.make_plan([self.input])
        self.contracts = self.plan["plans"][0]["contracts"]

    def make_plan(self, inputs):
        plans = [policy.derive_plan(i["found"], **{k: v for k, v in i.items() if k != "found"}) for i in inputs]
        self.assertTrue(all(not p["coverage_holds"] for p in plans))
        return {"inputs": deepcopy(inputs), "plans": plans}

    def file(self, contracts=None, plan=None, **options):
        return filing.file_current_contract_bundle("read", self.config, self.contracts if contracts is None else contracts,
            verified_plan=self.plan if plan is None else plan, run_id="fictional-host-run", write_token="write", now=NOW, **options)

    def test_fresh_print_only_bundle_is_idempotent_and_does_not_promote_identity_or_delivery(self):
        original = deepcopy(self.drive.items[SOURCE]); identity = deepcopy(self.owner)
        first = self.file()
        self.assertEqual(first["status"], "verified", first)
        second = self.file()
        self.assertEqual(first["feedback_id"], second["feedback_id"])
        self.assertEqual(first["drive_file_id"], second["drive_file_id"])
        self.assertEqual(len([f for f in self.drive.created if f["mimeType"] == archive.PDF]), 1)
        self.assertEqual(first["report_type"], "missing_work_contract")
        self.assertEqual(first["contract_status"], "open")
        self.assertEqual(first["original_due_date"], ["2026-09-16"])
        self.assertEqual(first["student_folder_id"], identity["drive_file_id"])
        current = self.port.rows[identity["student_key"]].value
        self.assertEqual({k: v for k, v in current.items() if k != "items"}, {k: v for k, v in identity.items() if k != "items"})
        self.assertEqual(self.drive.items[SOURCE], original)
        self.assertEqual(first["identity_status"], "identity_unverified")
        self.assertIn("plan_sha256", first["source_proof"])
        self.assertEqual(len(archive.work_index_rows("read", self.config)["rows"]), 2)

    def test_mutated_incomplete_or_stale_source_inputs_hold_before_pdf_upload(self):
        before = len(self.drive.created)
        mutations = [lambda p: p["inputs"][0]["source_proof"]["checks"].update(unsorted=False),
            lambda p: p["inputs"][0]["source_proof"].update(checked_at="2026-10-09T13:59:00Z"),
            lambda p: p["inputs"][0]["due_proofs"][7].update(checked_at="2026-10-08T15:00:00Z"),
            lambda p: p["inputs"][0]["roster_proof"].update(complete=False),
            lambda p: p["inputs"][0]["archive_proof"]["students"][0].update(private_folder_verified=False),
            lambda p: p["plans"][0]["contracts"][0].update(note="Mutated teacher instructions")]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                bad = deepcopy(self.plan); mutate(bad)
                self.assertEqual(self.file(plan=bad)["status"], "held")
                self.assertEqual(len(self.drive.created), before)
        changed = deepcopy(self.contracts); changed[0]["original_due_date"] = "2026-10-01"
        self.assertEqual(self.file(contracts=changed)["status"], "held")
        tomorrow = filing.file_current_contract_bundle("read", self.config, self.contracts,
            verified_plan=self.plan, run_id="fictional-next-day", now="2026-10-10T15:05:00Z")
        self.assertEqual(tomorrow["status"], "held")

    def test_current_bundle_must_include_all_current_assignments_for_the_student(self):
        other = deepcopy(self.input)
        next_assignment = ASSIGNMENT + ":second"
        other["found"]["lessons"][0]["row"] = 8
        other["found"]["lessons"][0]["forms"][0]["form_id"] = "FictionalSecondForm001"
        other["roster_proof"]["students"][0]["assignment_states"][next_assignment] = {"assigned": True, "exempt": False}
        due = other["due_proofs"].pop(7)
        due.update(row=8, assignment_key=next_assignment, form_ids=["FictionalSecondForm001"], title="Cycle 03 — Fictional evidence")
        other["due_proofs"][8] = due
        other["source_proof"]["found_sha256"] = content_hash(other["found"])
        plan = self.make_plan([self.input, other])
        before = len(self.drive.created)
        self.assertEqual(self.file(plan=plan)["status"], "held")
        self.assertEqual(len(self.drive.created), before)
        complete = [c for p in plan["plans"] for c in p["contracts"]]
        self.assertEqual(self.file(contracts=complete, plan=plan)["contract_count"], 2)

    def test_privacy_wrong_parent_ownership_and_wrong_pdf_hold_without_upload(self):
        folder = self.drive.items[self.owner["drive_file_id"]]
        original = deepcopy(folder)
        for mutate in (lambda f: f["permissions"].append({"type": "anyone", "role": "reader"}),
                       lambda f: f.update(parents=[ROOT]),
                       lambda f: f["capabilities"].update(canAddChildren=False)):
            with self.subTest(mutate=mutate):
                folder.clear(); folder.update(deepcopy(original)); mutate(folder)
                self.assertEqual(self.file()["status"], "held")
        folder.clear(); folder.update(original)
        self.assertEqual(self.file(pdf_bytes=b"%PDF Wrong student")["status"], "held")
        self.assertEqual(len([f for f in self.drive.created if f["mimeType"] == archive.PDF]), 0)

    def test_uncertain_upload_is_not_repeated_but_known_reserved_pdf_can_be_recovered(self):
        self.drive.fail_after_create = True
        first = self.file()
        self.assertEqual(first["status"], "verified")
        self.assertEqual(self.file()["drive_file_id"], first["drive_file_id"])
        self.assertEqual(len([f for f in self.drive.created if f["mimeType"] == archive.PDF]), 1)

    def test_creating_404_never_reposts_a_pdf(self):
        self.drive.fail_create = True
        self.assertEqual(self.file()["status"], "held")
        self.drive.fail_create = False
        self.assertEqual(self.file()["status"], "held")
        self.assertEqual(len([f for f in self.drive.created if f["mimeType"] == archive.PDF]), 1)

    def test_publish_requires_complete_bound_private_files_and_preserves_previous_pointer_on_failure(self):
        first = self.file(); self.assertEqual(first["status"], "verified")
        initial = filing.publish_current_plan("read", self.config, self.plan, [first], now=NOW)
        self.assertEqual(initial["status"], "verified", initial)
        self.assertEqual(initial["feedback_ids"], [first["feedback_id"]])
        self.assertEqual(initial["contracts"], 1)
        self.assertEqual(initial["expires_at"], "2026-10-09T16:00:00+00:00")
        pointer_key = initial["record_key"]
        before = deepcopy(self.port.rows[pointer_key].value)
        self.assertEqual(filing.publish_current_plan("read", self.config, self.plan, [], now=NOW)["status"], "held")
        self.drive.items[first["drive_file_id"]]["md5Checksum"] = "ChangedPDF"
        self.assertEqual(filing.publish_current_plan("read", self.config, self.plan, [first], now=NOW)["status"], "held")
        self.assertEqual(self.port.rows[pointer_key].value, before)

    def test_fresh_located_work_closes_the_pointer_without_deleting_historical_pdf(self):
        first = self.file()
        self.assertEqual(filing.publish_current_plan("read", self.config, self.plan, [first], now=NOW)["status"], "verified")
        closed = deepcopy(self.input)
        form = closed["found"]["lessons"][0]["forms"][0]
        named = form["missing"].pop()
        form["students"] = [{**named, "matched": True, "files": [{"id": SOURCE, "trashed": False}], "earlier": []}]
        closed["source_proof"]["found_sha256"] = content_hash(closed["found"])
        plan = self.make_plan([closed])
        before = deepcopy(self.drive.items[first["drive_file_id"]])
        pointer = filing.publish_current_plan("read", self.config, plan, [], now=NOW)
        self.assertEqual(pointer["status"], "verified", pointer)
        self.assertEqual(pointer["feedback_ids"], [])
        self.assertEqual(pointer["contract_keys"], [])
        self.assertEqual(self.drive.items[first["drive_file_id"]], before)

    def test_clock_expiry_after_file_readback_cannot_publish(self):
        first = self.file()
        with patch.object(filing, "_now", side_effect=[policy._instant(NOW), policy._instant("2026-10-09T16:01:00Z")]):
            result = filing.publish_current_plan("read", self.config, self.plan, [first])
        self.assertEqual(result["status"], "held")
        self.assertFalse(any(k.startswith("currentcontractplan_") for k in self.port.rows))
