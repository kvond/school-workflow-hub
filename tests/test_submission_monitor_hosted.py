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

    def __enter__(self):
        self.patches = [
            patch.object(hosted, "configured_store", lambda config=None: self.port),
            patch.object(hosted, "_drive_scan",
                         lambda assertion, day, class_key, config: dict(self.scan)),
            patch.object(hosted, "_mappings",
                         lambda assertion, class_key, config: dict(self.maps)),
            patch.object(hosted, "_lesson",
                         lambda assertion, day, class_key, config: (None, None, FOLDER)),
            patch.object(hosted, "_now", lambda: self.port.now.isoformat()),
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
