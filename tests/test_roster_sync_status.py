import unittest
from unittest.mock import patch

from school_workflow_hub.adapters import roster_sync
from school_workflow_hub.core.cloud_state import CloudState
from tests.test_cloud_state import MemoryCAS


class RosterSyncStatusTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "HUB_IAP_AUDIENCE": "/projects/123/locations/us-central1/services/test-hub",
            "HUB_ALLOWED_EMAILS": "teacher@example.com",
            "HUB_STATE_BACKEND": "firestore",
        }

    def test_status_is_authenticated_redacted_and_has_no_write_effect(self):
        with patch.object(roster_sync, "authorize"), patch.object(
                roster_sync, "configured_store", side_effect=roster_sync.StoreUnavailable("no store")):
            result = roster_sync.read_roster_sync_status("assertion", self.config)
        self.assertEqual(result["status"], "Needs you")
        self.assertFalse(result["run_enabled"])
        self.assertEqual(result["external_writes"], 0)
        self.assertNotIn("student", str(result).lower())

    def test_existing_shared_task_is_reported_without_creating_a_new_one(self):
        port = MemoryCAS()
        store = CloudState(port)
        task_id = store.create_task(roster_sync.ROSTER_SYNC_SCOPE)
        claim = store.claim(task_id, owner="worker-a", request_id="request-a", input_hash="source-a", ttl_seconds=30)
        store.finish(claim, records={"result": {"change_count": 0}})
        before = len(port.rows)
        with patch.object(roster_sync, "authorize"), patch.object(roster_sync, "configured_store", return_value=port):
            result = roster_sync.read_roster_sync_status("assertion", self.config)
        self.assertEqual(result["status"], "Needs you")
        self.assertEqual(result["last_check_at"], port.now.isoformat())
        self.assertEqual(len(port.rows), before)

    def test_study_hall_scope_is_redacted_and_does_not_enable_or_expand_grading(self):
        self.config["HUB_ROSTER_ONLY_SECTIONS"] = {"B4 Study Hall": {
            "class_key": "study_hall", "section_id": "55555", "tab_id": 99,
            "school_year": "2026-27"}}
        with patch.object(roster_sync, "authorize"), patch.object(
                roster_sync, "configured_store", side_effect=roster_sync.StoreUnavailable("no store")):
            result = roster_sync.read_roster_sync_status("assertion", self.config)
        self.assertEqual(result["roster_scope"], {"instructional_sections": 4,
            "roster_only_sections": 1, "study_hall_included": True})
        self.assertFalse(result["run_enabled"])
        self.assertNotIn("55555", str(result))
        self.assertNotIn("B4 Study Hall", str(result))
