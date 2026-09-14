from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from school_workflow_hub.adapters.private_input import PrivateInputError, preview_private_assignment
from school_workflow_hub.cli import main
from school_workflow_hub.core.workflow import assert_safe_preview
from school_workflow_hub.workflows.schoology_gradebook import reconcile_platform_assignments
from tests.test_schoology_gradebook import desired_one, observations_for


NOW = datetime(2026, 9, 14, 18, tzinfo=timezone.utc)


def packet():
    # Deliberately fictional identifiers; no school data belongs in this repo.
    agenda = dict(class_id="fixture-class", assignment_id="fixture-assignment", revision="fixture-agenda-r2",
                  observed_at=NOW.isoformat(), due_date="2026-09-23",
                  deck_url="https://example.invalid/slides/fixture-deck/copy", schoology_id="fixture-schoology", ic_id="fixture-ic")
    base = dict(class_id=agenda["class_id"], assignment_id=agenda["assignment_id"], revision="fixture-observed-r1",
                observed_at=NOW.isoformat(), due_date="2026-09-18")
    return dict(schema_version="private-assignment-input/v1", privacy_salt="a1" * 32,
                course="forensics", week="W03", phase="notes", agenda=agenda,
                observations=[dict(base, system="schoology", target_id=agenda["schoology_id"],
                                   deck_url="https://example.invalid/slides/fixture-deck/edit"),
                              dict(base, system="infinite_campus", target_id=agenda["ic_id"])])


class PrivateInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.root.chmod(0o700)
        self.source = self.root / "snapshot.json"

    def write(self, data=None):
        self.source.write_text(json.dumps(packet() if data is None else data))
        self.source.chmod(0o600)

    def review(self):
        return preview_private_assignment(self.root, self.source, now=NOW)

    def reject(self, data):
        self.write(data)
        with self.assertRaises(PrivateInputError):
            self.review()

    def test_reuses_comparator_only_in_scope_and_preserves_raw_file(self):
        self.write()
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        with patch("school_workflow_hub.adapters.private_input.reconcile_platform_assignments", wraps=reconcile_platform_assignments) as compare:
            result = self.review()
        self.assertEqual(compare.call_count, 1)
        fields = {(r["target_system"], r["field"]) for r in result["preview"]["changes"]}
        self.assertEqual(fields, {("schoology", "due_date"), ("schoology", "deck_reference"), ("infinite_campus", "due_date")})
        self.assertEqual(before, hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(set(p.name for p in self.root.iterdir()), {"snapshot.json"})
        self.assertEqual(result["network_calls"], 0)
        self.assertEqual(result["preview"]["external_writes"], 0)
        self.assertEqual(result["student_access"], "UNVERIFIED")
        self.assertEqual(result["source_status"], "saved_snapshot_not_live")
        self.assertTrue(result["private_resolution_required"])

    def test_public_projection_contains_no_raw_identifiers_urls_salt_or_path(self):
        self.write()
        result = self.review()
        encoded = json.dumps(result)
        for forbidden in ("fixture-class", "fixture-assignment", "fixture-deck", "fixture-schoology", "fixture-ic", "fixture-agenda-r2", "a1" * 32, str(self.root)):
            self.assertNotIn(forbidden, encoded)
        assert_safe_preview(result)
        for change in result["preview"]["changes"]:
            self.assertEqual(change["approval_status"], "pending")
            self.assertTrue(all(e["reference"].startswith("local://private-input/") for e in change["evidence"]))
            self.assertTrue(all(e["source_type"].startswith("saved_") for e in change["evidence"]))

    def test_non_fixture_host_is_masked_before_synthetic_validator_without_network(self):
        data = packet()
        url = "https://docs.google.com/presentation/d/fictional-not-a-real-file/copy"
        data["agenda"]["deck_url"] = url
        self.write(data)
        with patch("socket.socket", side_effect=AssertionError("network forbidden")):
            result = self.review()
        encoded = json.dumps(result)
        self.assertNotIn("docs.google.com", encoded)
        self.assertNotIn("fictional-not-a-real-file", encoded)
        assert_safe_preview(result)

    def test_equivalent_tracking_links_noop_but_copy_edit_matters(self):
        data = packet()
        for row in data["observations"]:
            row["due_date"] = data["agenda"]["due_date"]
        data["observations"][0]["deck_url"] = data["agenda"]["deck_url"] + "?utm_source=fixture"
        self.write(data)
        self.assertFalse(self.review()["preview"]["changes"])
        data["observations"][0]["deck_url"] = data["agenda"]["deck_url"].replace("/copy", "/edit")
        self.write(data)
        self.assertEqual([c["field"] for c in self.review()["preview"]["changes"]], ["deck_reference"])

    def test_same_input_is_idempotent_and_new_observation_invalidates_receipt(self):
        self.write()
        first = self.review()
        self.assertEqual(first, self.review())
        data = packet()
        data["agenda"]["observed_at"] = "2026-09-14T17:59:00+00:00"
        self.write(data)
        second = self.review()
        self.assertNotEqual(first["receipt"], second["receipt"])
        self.assertNotEqual(first["preview"]["changes"][0]["stable_id"], second["preview"]["changes"][0]["stable_id"])

    def test_unknown_targets_and_missing_platforms_are_not_invented(self):
        data = packet()
        data["agenda"]["schoology_id"] = "UNVERIFIED"
        data["observations"] = []
        self.write(data)
        result = self.review()["preview"]
        self.assertFalse(result["changes"])
        codes = {i["code"] for i in result["issues"]}
        self.assertIn("unverified_schoology_target", codes)
        self.assertIn("missing_infinite_campus_target", codes)

    def test_unknown_old_fields_do_not_become_known_empty_values(self):
        data = packet()
        for row in data["observations"]:
            row["due_date"] = "UNVERIFIED"
        data["observations"][0]["deck_url"] = "UNVERIFIED"
        self.write(data)
        result = self.review()["preview"]
        self.assertFalse(result["changes"])
        self.assertEqual(len(result["issues"]), 3)

    def test_unknown_agenda_dates_do_not_propose_removal(self):
        data = packet()
        data["agenda"]["due_date"] = None
        self.write(data)
        self.assertTrue(all(c["field"] != "due_date" for c in self.review()["preview"]["changes"]))

    def test_duplicate_wrong_course_and_wrong_target_do_not_get_matched_by_title(self):
        for field in ("class_id", "assignment_id", "target_id"):
            data = packet()
            data["observations"][0][field] = "fixture-wrong"
            self.write(data)
            result = self.review()["preview"]
            self.assertTrue(all(c["target_system"] != "schoology" for c in result["changes"]))
            self.assertTrue(result["issues"])
        data = packet()
        data["observations"][1] = deepcopy(data["observations"][0])
        self.write(data)
        self.assertFalse(self.review()["preview"]["changes"])

    def test_extra_fields_and_student_data_are_rejected(self):
        for key in ("student_name", "grades", "token", "instructions", "approval_status"):
            data = packet()
            data["agenda"][key] = "fixture-private-text"
            self.reject(data)

    def test_unsafe_urls_rejected_without_echo(self):
        for url in ("https://" + "answerableteaching.com" + "/x", "https://example.invalid/?token=fixture-secret", "https://example.invalid/?next=https://example.invalid", "http://example.invalid", "#slide=id.p1", "https://127.0.0.1/x"):
            data = packet()
            data["agenda"]["deck_url"] = url
            self.write(data)
            try:
                self.review()
                self.fail("unsafe URL accepted")
            except PrivateInputError as error:
                self.assertNotIn(url, str(error))
                self.assertIsNone(error.__context__)

    def test_bad_dates_timestamps_and_unknown_revisions(self):
        for value in ("2026-02-30", "September 23", "2026-9-23", True):
            data = packet()
            data["agenda"]["due_date"] = value
            self.reject(data)
        for value in ("2026-09-12T12:00:00Z", "2026-09-15T18:00:00Z", "2026-09-14T18:00:00", 1):
            data = packet()
            data["agenda"]["observed_at"] = value
            self.reject(data)
        data = packet()
        data["agenda"]["revision"] = "UNVERIFIED"
        self.write(data)
        self.assertFalse(self.review()["preview"]["changes"])

    def test_file_and_root_permissions_enforced(self):
        self.write()
        self.source.chmod(0o644)
        with self.assertRaises(PrivateInputError):
            self.review()
        self.source.chmod(0o600)
        self.root.chmod(0o755)
        with self.assertRaises(PrivateInputError):
            self.review()

    def test_symlink_file_root_and_hardlinks_rejected(self):
        self.write()
        link = self.root / "link.json"
        link.symlink_to(self.source)
        with self.assertRaises(PrivateInputError):
            preview_private_assignment(self.root, link, now=NOW)
        os.link(self.source, self.root / "hard.json")
        with self.assertRaises(PrivateInputError):
            self.review()
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(PrivateInputError):
            preview_private_assignment(alias, alias / "snapshot.json", now=NOW)

    def test_fifo_rejected_without_blocking(self):
        os.mkfifo(self.source, 0o600)
        with self.assertRaises(PrivateInputError):
            self.review()

    def test_repo_sync_traversal_and_outside_paths_rejected(self):
        self.write()
        for root, source in ((self.root, self.root.parent / "other.json"), (Path("relative"), Path("relative/x.json")), (self.root / ".." / self.root.name, self.root / ".." / self.root.name / "snapshot.json")):
            with self.assertRaises(PrivateInputError):
                preview_private_assignment(root, source, now=NOW)
        (self.root / ".git").mkdir()
        with self.assertRaises(PrivateInputError):
            self.review()
        sync = self.root / "CloudStorage" / "private"
        with self.assertRaises(PrivateInputError):
            preview_private_assignment(sync, sync / "snapshot.json", now=NOW)

    def test_invalid_json_duplicate_keys_and_oversize_are_sanitized(self):
        for raw in ('{"private-secret":', '{"x":1,"x":2}', '[' * 5000, 'x' * 262145):
            self.source.write_text(raw)
            self.source.chmod(0o600)
            with self.assertRaises(PrivateInputError) as caught:
                self.review()
            self.assertNotIn("private-secret", str(caught.exception))

    def test_changed_file_rejected(self):
        self.write()
        original_read = os.read
        first = True
        def mutate(fd, count):
            nonlocal first
            block = original_read(fd, count)
            if first:
                first = False
                self.source.write_text("{}")
            return block
        with patch("school_workflow_hub.adapters.private_input.os.read", side_effect=mutate):
            with self.assertRaises(PrivateInputError):
                self.review()

    def test_cli_outputs_only_sanitized_report_or_error(self):
        data = packet()
        stamp = datetime.now(timezone.utc).isoformat()
        data["agenda"]["observed_at"] = stamp
        for row in data["observations"]:
            row["observed_at"] = stamp
        self.write(data)
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(["preview-private-assignment", "--private-root", str(self.root), "--input", str(self.source)])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue())["network_calls"], 0)
        self.assertNotIn("fixture-schoology", output.getvalue())
        self.source.write_text('{"password":"fixture-secret",')
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(["preview-private-assignment", "--private-root", str(self.root), "--input", str(self.source)])
        self.assertEqual(status, 2)
        self.assertNotIn("fixture-secret", output.getvalue())
        self.assertNotIn(str(self.source), output.getvalue())

    def test_comparator_scope_cannot_invent_fields(self):
        with self.assertRaises(ValueError):
            reconcile_platform_assignments((desired_one(),), observations_for(desired_one()), fields_by_system={"schoology": ("grades",), "infinite_campus": ("due_date",)})


if __name__ == "__main__":
    unittest.main()
