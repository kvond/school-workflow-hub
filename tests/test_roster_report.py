"""Synthetic records only. Native CSV provenance never implies a live writer."""
import csv
from copy import deepcopy
from datetime import timedelta
import hashlib
import io
import unittest

from school_workflow_hub.adapters import roster_report as rr, roster_worker as rw
from school_workflow_hub.adapters.hosted_agenda import ConnectionError
from school_workflow_hub.core.cloud_state import instant
from tests.test_roster_worker import target, cells, Mirror
from tests.test_cloud_state import MemoryCAS


def raw_csv(rows=None, trailing=True):
    rows = rows if rows is not None else [["03000", "SYN-1 Synthetic Biology", "100", "M1; M2", "4 A",
        "Fictional", "Ada", "Example", "Jr.", "Ace", "", "10", "09/01/2026", "", "00123"]]
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(rr.HEADER)
    writer.writerows([r + ([""] if trailing else []) for r in rows])
    return output.getvalue().encode()


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.port = MemoryCAS()
        self.target = target()
        self.target["identity_scheme"] = rw.REPORT_SCHEME
        self.targets = [self.target]
        self.context = {"origin": "https://campus.example.invalid", "account": "teacher@example.invalid",
            "school_id": "school1", "calendar_id": "calendar1", "structure_id": "structure1",
            "school_year": "2026-27", "term": "M1", "term_start": "2026-08-24", "term_end": "2026-11-01"}
        self.binding = {"state_code": "03000", "course": "SYN-1 Synthetic Biology", "room": "100",
                        "terms": "M1; M2", "period": "4 A"}
        self.bindings = {"42": self.binding}
        self.raw = raw_csv()
        self.observation = self.observe("capture-one")

    def observe(self, capture_id):
        return {"capture_id": capture_id,
            "captured_at": (self.port.now-timedelta(seconds=4)).isoformat(),
            "observed_at": (self.port.now-timedelta(seconds=2)).isoformat(),
            "csv_sha256": hashlib.sha256(self.raw).hexdigest(), "context": deepcopy(self.context),
            "effective_date": self.port.now.date().isoformat(), "options": deepcopy(rr.OPTIONS),
            "sections": [{"section_id": "42", "scope": deepcopy(self.binding), "complete": True,
                "filtered": False, "next_page": None, "counts": {"active": 1, "incoming": 0, "withdrawn": 0},
                "students": [{"student_number": "00123", "status": "active"}]}]}

    def prepare(self):
        return rr.prepare_capture(self.raw, self.observation, expected_context=self.context,
            bindings=self.bindings, targets=self.targets, now=self.port.now)

    def test_exact_native_csv_with_empty_trailing_field_and_leading_zero_id(self):
        result = self.prepare()
        row = result["sections"][0]["rows"][0]
        self.assertEqual(row["student_id"], "00123")
        self.assertEqual(row["first"], "Ada Example (Ace)")
        self.assertEqual(row["last"], "Fictional Jr.")
        self.assertEqual(row["start_date"], "2026-09-01")
        self.assertTrue(row["membership_id"].startswith(rw.REPORT_MEMBER_PREFIX))
        self.assertEqual(rw.owned_headers(self.target)["membership_id"], "IC membership key")
        self.assertEqual(rw.owned_headers(target())["membership_id"], "IC enrollment ID")

    def test_status_is_never_inferred_from_empty_end_or_dates(self):
        self.observation["sections"][0]["students"][0]["status"] = "withdrawn"
        self.observation["sections"][0]["counts"] = {"active": 0, "incoming": 0, "withdrawn": 1}
        self.assertEqual(self.prepare()["sections"][0]["rows"][0]["status"], "withdrawn")
        self.observation["sections"][0]["students"] = []
        with self.assertRaisesRegex(ConnectionError, "count_mismatch"):
            self.prepare()

    def test_observed_native_gender_cells_preserve_raw_proof_but_not_canonical_identity(self):
        original = self.raw
        row = next(csv.reader(io.StringIO(original.decode()).read().splitlines()[1:]))[:15]
        baseline = self.prepare()["sections"]
        hashes = set()
        for value in ("", "M", "F"):
            row[10] = value
            self.raw = raw_csv([row])
            self.observation = self.observe("native-" + (value or "empty"))
            result = self.prepare()
            self.assertEqual(result["sections"], baseline)
            self.assertEqual(result["report_proof"]["csv_sha256"], hashlib.sha256(self.raw).hexdigest())
            self.assertNotIn("gender", result["sections"][0]["rows"][0])
            hashes.add(result["report_proof"]["csv_sha256"])
        self.assertEqual(len(hashes), 3)
        for value in ("X", "unknown", "m"):
            row[10] = value
            with self.assertRaisesRegex(ConnectionError, "identity_or_scope_invalid"):
                rr.parse_csv(raw_csv([row]))

    def test_unknown_status_and_partial_filtered_paginated_reads_hold(self):
        for field, value in [("complete", False), ("filtered", True), ("next_page", "more")]:
            with self.subTest(field=field):
                self.observation = self.observe("one")
                self.observation["sections"][0][field] = value
                with self.assertRaises(ConnectionError): self.prepare()
        self.observation = self.observe("one")
        self.observation["sections"][0]["students"][0]["status"] = "unknown"
        with self.assertRaises(ConnectionError): self.prepare()

    def test_native_count_identity_section_and_context_conflicts_hold(self):
        variants = [lambda o: o["sections"][0]["counts"].update(active=2),
            lambda o: o["sections"][0]["students"][0].update(student_number="999"),
            lambda o: o["sections"][0].update(section_id="wrong"),
            lambda o: o["context"].update(calendar_id="wrong"),
            lambda o: o["options"].update(incoming=False),
            lambda o: o.update(csv_sha256="0"*64),
            lambda o: o["sections"][0]["scope"].update(period="foreign")]
        for mutate in variants:
            self.observation = self.observe("one")
            mutate(self.observation)
            with self.assertRaises(ConnectionError): self.prepare()

    def test_bad_header_nonempty_extra_or_duplicate_rows_hold(self):
        for raw in [self.raw.replace(b'Student Number', b'Person ID'),
                    self.raw.replace(b'00123,', b'00123,unexpected'),
                    self.raw + self.raw.splitlines(keepends=True)[1], b'<html>login</html>']:
            with self.assertRaises(ConnectionError): rr.parse_csv(raw)

    def test_empty_export_requires_independent_zero_section_evidence(self):
        self.raw = raw_csv([])
        self.observation = self.observe("empty")
        with self.assertRaises(ConnectionError): self.prepare()
        section = self.observation["sections"][0]
        section["students"] = []
        section["counts"] = dict.fromkeys(rw.SOURCE_STATUSES, 0)
        self.assertEqual(self.prepare()["sections"][0]["rows"], [])

    def test_original_generation_time_not_download_time_controls_freshness(self):
        self.observation["captured_at"] = (self.port.now-timedelta(minutes=16)).isoformat()
        with self.assertRaisesRegex(ConnectionError, "stale"): self.prepare()

    def test_future_capture_wrong_effective_date_or_invalid_term_holds(self):
        for mutate in [lambda o:o.update(observed_at=(self.port.now+timedelta(seconds=1)).isoformat()),
                       lambda o:o.update(effective_date="2026-09-13")]:
            self.observation = self.observe("one"); mutate(self.observation)
            with self.assertRaises(ConnectionError): self.prepare()

    def test_source_membership_is_relationship_not_native_episode_or_date_guess(self):
        result = self.prepare(); first = result["sections"][0]["rows"][0]
        self.raw = self.raw.replace(b'09/01/2026', b'09/10/2026')
        self.observation = self.observe("two")
        second = self.prepare()["sections"][0]["rows"][0]
        self.assertEqual(first["membership_id"], second["membership_id"])
        self.assertNotEqual(first["start_date"], second["start_date"])
        self.assertNotEqual(rw.source_fingerprint(result), rw.source_fingerprint(self.prepare()))

    def test_report_cannot_silently_enter_native_id_target_or_forge_relationship_key(self):
        result = self.prepare()
        with self.assertRaises(ConnectionError): rw.validate_capture(result, [target()], self.port.now)
        result["sections"][0]["rows"][0]["membership_id"] = rw.REPORT_MEMBER_PREFIX + "0"*64
        with self.assertRaisesRegex(ConnectionError, "report_identity"):
            rw.validate_capture(result, self.targets, self.port.now)

    def test_worker_rejects_same_artifact_as_both_native_bookends(self):
        capture = self.prepare()
        mirror = Mirror({"7": cells(self.target, capture["sections"][0]["rows"])})
        reader = rr.NativeRosterReportReader(lambda:(self.raw, self.observation),
            expected_context=self.context, bindings=self.bindings, clock=lambda:self.port.now)
        worker = rw.RosterWorker(self.port, reader, mirror, self.targets, before_write=lambda **kw:None)
        with self.assertRaisesRegex(ConnectionError, "independent_native_report"):
            worker.run(owner="test", request_id="same-file")
        self.assertEqual(mirror.writes, 0)

    def test_worker_accepts_distinct_fresh_native_bookends_and_records_both(self):
        initial = self.prepare()
        mirror = Mirror({"7": cells(self.target, initial["sections"][0]["rows"])})
        calls = []
        def capture():
            if calls: self.port.now += timedelta(seconds=10)
            calls.append(True)
            return self.raw, self.observe("native-"+str(len(calls)))
        reader = rr.NativeRosterReportReader(capture, expected_context=self.context,
            bindings=self.bindings, clock=lambda:self.port.now)
        result = rw.RosterWorker(self.port, reader, mirror, self.targets,
            before_write=lambda **kw:None).run(owner="test", request_id="two-captures")
        self.assertEqual(result["status"], "verified")
        receipts = [doc.value for k,doc in self.port.rows.items() if k.startswith("record_")]
        payload = receipts[0]["payload"]
        self.assertEqual(payload["source"]["report_proof"]["capture_id"], "native-1")
        self.assertEqual(payload["source_bookend"]["report_proof"]["capture_id"], "native-2")

    def test_no_legacy_row_crosswalk_from_matching_name(self):
        capture = self.prepare()
        baseline = {"7": cells(self.target, capture["sections"][0]["rows"])}
        baseline["7"][1][self.target["columns"]["student_id"]-1] = {}
        with self.assertRaisesRegex(ConnectionError, "crosswalk"):
            rw.plan_mirror(capture, self.targets, baseline)

    def test_scheme_header_and_old_native_identity_remain_separate(self):
        capture = self.prepare()
        baseline = {"7": cells(self.target, capture["sections"][0]["rows"])}
        baseline["7"][1][self.target["columns"]["membership_id"]-1] = {"stringValue":"native-enrollment"}
        with self.assertRaisesRegex(ConnectionError, "scheme_mismatch"):
            rw.plan_mirror(capture, self.targets, baseline)

if __name__ == '__main__': unittest.main()
