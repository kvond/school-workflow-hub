"""Hosted monitoring persistence: synthetic sources, in-memory CAS store."""
import json
import threading
import unittest
from unittest.mock import patch

from school_workflow_hub.adapters import submission_monitor as hosted
from school_workflow_hub.adapters.hosted_agenda import ConnectionError
from school_workflow_hub.core.cloud_state import Busy, CloudState, key
from school_workflow_hub.workflows.submission_monitor import stream_scope
from tests.test_cloud_state import MemoryCAS
from school_workflow_hub.web import application

FOLDER = "f" * 24
FILE_A, FILE_B = "a" * 24, "b" * 24
CONFIG = {"HUB_STATE_PROBE_ORIGINS": "https://hub.example"}


def drive_scan(files, observed="2026-09-15T11:00:00+00:00"):
    return {"class_key": "biology", "folder_id": FOLDER,
            "folder_name": "Biology — Wk 01 — Fictional", "assignment_label": "B01",
            "date": "2026-09-14", "observed_at": observed,
            "coverage": {"complete": True, "folders_checked": 1, "forms_found": 0},
            "files": files}


def entry(file_id, modified="2026-09-14T15:00:00+00:00", version="2"):
    return {"id": file_id, "name": "Fictional " + file_id[:4], "modified_at": modified,
            "version": version}


def mappings(by_file=None):
    return {"source": "grading_workbook", "checked_at": "2026-09-15T12:00:00+00:00",
            "by_file": by_file or {}}


class Harness:
    def __init__(self):
        self.port = MemoryCAS()
        self.scan = drive_scan([entry(FILE_A)])
        self.maps = mappings()
        # A synthetic Weeks calendar covering the fixture dates.
        from school_workflow_hub.workflows.topic_deadline import parse_week_calendar
        from datetime import date as _date
        self.calendar = {"2026-09-14": parse_week_calendar(
            _date(2026, 9, 14), "Sep 14 – Sep 18, 2026\nFri 09/18 = A Friday → Bio + Forensics")}

    def __enter__(self):
        self.patches = [
            patch.object(hosted, "configured_store", lambda config=None: self.port),
            patch.object(hosted, "_drive_scan",
                         lambda assertion, day, class_key, config: dict(self.scan)),
            patch.object(hosted, "_mappings",
                         lambda assertion, class_key, config: dict(self.maps)),
            patch.object(hosted, "_lesson",
                         lambda assertion, day, class_key, config: (None, None, {}, FOLDER)),
            patch.object(hosted, "_now", lambda: self.port.now.isoformat()),
            patch.object(hosted.topic_starts, "read_week_calendar",
                         lambda assertion, config: dict(self.calendar)),
        ]
        for item in self.patches:
            item.start()
        return self

    def __exit__(self, *args):
        for item in self.patches:
            item.stop()


class HostedScanTests(unittest.TestCase):
    def test_first_scan_persists_and_survives_a_fresh_store_client(self):
        with Harness() as h:
            first = hosted.scan_and_persist(None, "2026-09-14", "biology", CONFIG)
            self.assertFalse(first["unchanged_rerun"])
            self.assertEqual(first["counts"]["present"], 1)
            self.assertEqual(first["scan_count"], 1)
            # A brand new client over the same durable rows reads the result back.
            status = hosted.read_status(None, "2026-09-14", "biology", CONFIG)
            self.assertEqual(status["counts"]["present"], 1)
            self.assertEqual(status["last_attempt"]["outcome"], "complete")
            self.assertFalse(status["automation"]["active"])

    def test_unchanged_rerun_creates_no_duplicate_run_or_events(self):
        with Harness() as h:
            hosted.scan_and_persist(None, "2026-09-14", "biology", CONFIG)
            rows_after_first = set(h.port.rows)
            h.port.advance(120)
            h.scan = drive_scan([entry(FILE_A)], observed="2026-09-15T11:02:00+00:00")
            rerun = hosted.scan_and_persist(None, "2026-09-14", "biology", CONFIG)
            self.assertTrue(rerun["unchanged_rerun"])
            self.assertEqual(rerun["scan_count"], 1)
            self.assertEqual({r for r in h.port.rows if r.startswith(("run_", "record_",
                                                                      "completed_"))},
                             {r for r in rows_after_first if r.startswith(("run_", "record_",
                                                                           "completed_"))})

    def test_changed_scan_appends_events_and_preserves_history(self):
        with Harness() as h:
            hosted.scan_and_persist(None, "2026-09-14", "biology", CONFIG)
            h.port.advance(120)
            h.scan = drive_scan([entry(FILE_A), entry(FILE_B)],
                                observed="2026-09-15T11:02:00+00:00")
            second = hosted.scan_and_persist(None, "2026-09-14", "biology", CONFIG)
            self.assertFalse(second["unchanged_rerun"])
            self.assertEqual(second["scan_count"], 2)
            kinds = {e["kind"] for e in second["events_last_scan"]}
            self.assertEqual(kinds, {"appeared"})
            first_seen = [f for f in second["files"] if f["file_id"] == FILE_A][0]
            self.assertEqual(first_seen["first_observed_at"], "2026-09-15T11:00:00+00:00")

    def test_failed_source_records_attempt_without_touching_baseline(self):
        with Harness() as h:
            hosted.scan_and_persist(None, "2026-09-14", "biology", CONFIG)
            h.port.advance(120)
            def broken(assertion, day, class_key, config):
                raise ConnectionError("submission_folder_access_required")
            with patch.object(hosted, "_drive_scan", broken):
                with self.assertRaises(ConnectionError):
                    hosted.scan_and_persist(None, "2026-09-14", "biology", CONFIG)
            status = hosted.read_status(None, "2026-09-14", "biology", CONFIG)
            self.assertEqual(status["last_attempt"]["outcome"], "failed")
            self.assertEqual(status["last_attempt"]["reason"],
                             "submission_folder_access_required")
            self.assertEqual(status["counts"]["present"], 1)  # baseline intact

    def test_concurrent_worker_cannot_commit_a_conflicting_result(self):
        with Harness() as h:
            store = CloudState(h.port)
            scope = stream_scope("biology", FOLDER)
            task_id = store.create_task(scope)
            store.claim(task_id, owner="other-worker", request_id="held",
                        input_hash="h" * 16, ttl_seconds=120)
            with self.assertRaises(Busy):
                hosted.scan_and_persist(None, "2026-09-14", "biology", CONFIG)

    def test_status_before_any_scan_is_explicit(self):
        with Harness():
            status = hosted.read_status(None, "2026-09-14", "biology", CONFIG)
            self.assertEqual(status["status"], "no_complete_scan_recorded")


class DeadlineIntegrationTests(unittest.TestCase):
    def test_start_note_seen_by_a_scan_sets_the_third_meeting_deadline(self):
        with Harness() as h:
            h.scan = drive_scan([entry(FILE_A)])
            h.scan["day_plan"] = "Started Energy today."
            result = hosted.scan_and_persist(None, "2026-09-14", "biology", CONFIG)
            self.assertEqual(result["topic_declaration"]["recorded"], "start")
            self.assertEqual(result["deadline"]["meetings"],
                             ["2026-09-14", "2026-09-16", "2026-09-18"])
            self.assertEqual(result["due_date"], "2026-09-18")
            self.assertEqual(result["due_basis"], "third_meeting_rule")
            self.assertEqual(result["deadline"]["ic_alignment"],
                             "calculated_not_verified_in_ic")

    def test_repeated_saves_and_scans_never_duplicate_the_start(self):
        with Harness() as h:
            h.scan["day_plan"] = "Started Energy today."
            hosted.scan_and_persist(None, "2026-09-14", "biology", CONFIG)
            h.port.advance(120)
            h.scan = drive_scan([entry(FILE_A)], observed="2026-09-15T11:02:00+00:00")
            h.scan["day_plan"] = "Started Energy today."
            rerun = hosted.scan_and_persist(None, "2026-09-14", "biology", CONFIG)
            self.assertTrue(rerun["unchanged_rerun"])
            self.assertEqual(rerun["topic_declaration"]["recorded"],
                             "start_already_recorded")

    def test_contradictory_start_is_flagged_not_guessed(self):
        with Harness() as h:
            h.scan["day_plan"] = "Started Energy today."
            hosted.scan_and_persist(None, "2026-09-14", "biology", CONFIG)
            h.port.advance(120)
            h.scan = drive_scan([entry(FILE_A)], observed="2026-09-15T11:02:00+00:00")
            h.scan["day_plan"] = "Started Energy today."
            second = hosted.scan_and_persist(None, "2026-09-16", "biology", CONFIG)
            self.assertTrue(second["topic_declaration"]["conflict"])
            self.assertEqual(second["deadline"]["status"], "start_conflict_needs_review")

    def test_the_navigation_date_is_never_the_deadline(self):
        with Harness() as h:
            h.scan["day_plan"] = "Started Energy today."
            hosted.scan_and_persist(None, "2026-09-14", "biology", CONFIG)
            h.port.advance(120)
            h.scan = drive_scan([entry(FILE_A)], observed="2026-09-15T11:02:00+00:00")
            h.scan["date"] = "2026-09-16"
            later = hosted.scan_and_persist(None, "2026-09-16", "biology", CONFIG)
            self.assertEqual(later["due_date"], "2026-09-18")
            self.assertEqual(later["due_basis"], "third_meeting_rule")

    def test_teacher_exception_overrides_the_third_meeting(self):
        with Harness() as h:
            h.scan["day_plan"] = "Started Energy today. Deadline exception: due 9/25."
            result = hosted.scan_and_persist(None, "2026-09-14", "biology", CONFIG)
            self.assertEqual(result["due_date"], "2026-09-25")
            self.assertEqual(result["due_basis"], "teacher_exception")

    def test_old_derivation_state_is_recomputed_without_losing_history(self):
        with Harness() as h:
            import school_workflow_hub.workflows.submission_monitor as core
            with patch.object(core, "DERIVATION_VERSION", 1), \
                 patch.object(hosted, "DERIVATION_VERSION", 1):
                first = hosted.scan_and_persist(None, "2026-09-14", "biology", CONFIG)
            self.assertEqual(first["scan_count"], 1)
            h.port.advance(120)
            h.scan = drive_scan([entry(FILE_A)], observed="2026-09-15T11:02:00+00:00")
            recomputed = hosted.scan_and_persist(None, "2026-09-14", "biology", CONFIG)
            self.assertFalse(recomputed["unchanged_rerun"])
            self.assertEqual(recomputed["scan_count"], 2)
            self.assertEqual(recomputed["counts"]["events_this_scan"]
                             if "events_this_scan" in (recomputed["counts"] or {}) else
                             len(recomputed["events_last_scan"]), 0)
            kept = [f for f in recomputed["files"] if f["file_id"] == FILE_A][0]
            self.assertEqual(kept["first_observed_at"], "2026-09-15T11:00:00+00:00")
            h.port.advance(120)
            h.scan = drive_scan([entry(FILE_A)], observed="2026-09-15T11:04:00+00:00")
            settled = hosted.scan_and_persist(None, "2026-09-14", "biology", CONFIG)
            self.assertTrue(settled["unchanged_rerun"])


class EndpointTests(unittest.TestCase):
    def call(self, method="GET", query="date=2026-09-14&class_key=biology", body=None,
             origin="https://hub.example", content_type="application/json"):
        result = {}
        def start(status, headers):
            result.update(status=status)
        environ = {"PATH_INFO": "/api/submission-monitor", "REQUEST_METHOD": method,
                   "QUERY_STRING": query}
        if body is not None:
            raw = json.dumps(body).encode()
            import io
            environ.update({"HTTP_ORIGIN": origin, "CONTENT_TYPE": content_type,
                            "CONTENT_LENGTH": str(len(raw)), "QUERY_STRING": "",
                            "wsgi.input": io.BytesIO(raw)})
        result["body"] = json.loads(b"".join(application(environ, start)) or b"{}")
        return result

    def test_get_reads_status_and_post_requires_origin(self):
        with Harness(), patch.dict("os.environ", CONFIG):
            self.assertEqual(self.call()["body"]["status"], "no_complete_scan_recorded")
            refused = self.call("POST", body={"action": "scan", "date": "2026-09-14",
                                              "class_key": "biology"}, origin="https://evil.example")
            self.assertEqual(refused["status"], "403 Forbidden")
            done = self.call("POST", body={"action": "scan", "date": "2026-09-14",
                                           "class_key": "biology"})
            self.assertEqual(done["status"], "200 OK")
            self.assertEqual(done["body"]["counts"]["present"], 1)

    def test_a_status_refresh_never_starts_a_scan(self):
        with Harness() as h, patch.dict("os.environ", CONFIG):
            self.call()
            self.assertFalse(any(r.startswith("run_") for r in h.port.rows))


if __name__ == "__main__":
    unittest.main()


class MappingIdentityTests(unittest.TestCase):
    def test_roster_learner_rows_map_files_and_held_rows_do_not(self):
        rows = [
            {"class_key": "biology", "source_file_id": FILE_A, "student_id": "90522",
             "learner_key": "roster:" + "a" * 64, "status": "assessed",
             "section_id": "26716", "identity_basis": "ic", "checked_at": "2026-09-15T12:00:00+00:00",
             "ic_posting_status": "unposted"},
            {"class_key": "biology", "source_file_id": FILE_B, "student_id": "",
             "learner_key": "roster:" + "b" * 64, "status": "assessed",
             "section_id": "26716", "identity_basis": "roster", "checked_at": "2026-09-15T12:00:00+00:00",
             "ic_posting_status": "unposted"},
            {"class_key": "biology", "source_file_id": "c" * 24, "student_id": "",
             "learner_key": "roster:" + "c" * 64, "status": "identity_unverified",
             "section_id": "", "identity_basis": "held", "checked_at": "",
             "ic_posting_status": ""},
        ]
        with patch.object(hosted, "read_records", lambda assertion, config: {
                "submissions": rows, "checked_at": "2026-09-15T12:00:00+00:00"}):
            result = hosted._mappings(None, "biology", {})
        self.assertEqual(result["by_file"][FILE_A]["student_id"], "90522")
        roster = result["by_file"][FILE_B]
        self.assertEqual(roster["student_id"], "roster:" + "b" * 16)
        self.assertEqual(roster["marker_precision"], "timestamp")
        self.assertNotIn("c" * 24, result["by_file"])
        # The stable fingerprint sees the roster identity, so a newly assessed
        # roster student changes the scan input and triggers a recompute.
        from school_workflow_hub.adapters.submission_monitor import _stable_mappings
        self.assertIn("roster:" + "b" * 16,
                      json.dumps(_stable_mappings(result)))
