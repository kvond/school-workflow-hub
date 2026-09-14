from contextlib import redirect_stdout
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import cloud_validation
from school_workflow_hub.core import database

ROOT = Path(__file__).resolve().parents[1]


class CloudValidationTests(unittest.TestCase):
    def test_linux_has_one_fixed_legacy_alias_without_broadening_root(self):
        with patch.object(database, "SYNTHETIC_RUNTIME_ROOT", Path("/tmp/school-workflow-hub")):
            self.assertEqual(database.require_synthetic_runtime_path("/private/tmp/school-workflow-hub/demo/file.json"),
                             Path("/tmp/school-workflow-hub/demo/file.json").resolve())
            for invalid in ("/tmp/other/file.json", "/private/tmp/other/file.json", "/private/tmp/school-workflow-hub/../../outside", "relative/file.json"):
                with self.assertRaises(ValueError):
                    database.require_synthetic_runtime_path(invalid)

    def test_cloud_job_reuses_full_suite_and_replay_without_exporting_records(self):
        suite = subprocess.CompletedProcess([], 0, stdout="Synthetic suite: tests=215, failures=0, errors=0, skipped=0.\n", stderr="")
        receipt = {"external_writes": 0, "live_model_calls": 0, "fixture_private_marker": "never-export-this"}
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(cloud_validation, "SYNTHETIC_RUNTIME_ROOT", Path(temp)), \
                 patch.object(cloud_validation.subprocess, "run", return_value=suite) as run, \
                 patch.object(cloud_validation, "run_system_preview", return_value=receipt) as replay:
                result = cloud_validation.run_validation()
            self.assertEqual(list(Path(temp).iterdir()), [])
        self.assertEqual(replay.call_count, 2)
        self.assertEqual(run.call_args.kwargs["timeout"], 180)
        self.assertNotIn("never-export-this", str(result))
        self.assertEqual(result["private_inputs_loaded"], 0)

    def test_failed_skipped_or_incomplete_suite_blocks_preview(self):
        for suite in (subprocess.CompletedProcess([], 1, "", "sensitive-error"),
                      subprocess.CompletedProcess([], 0, "Synthetic suite: tests=215, failures=0, errors=0, skipped=1.\n", ""),
                      subprocess.CompletedProcess([], 0, "Synthetic suite: tests=1, failures=0, errors=0, skipped=0.\n", "")):
            with patch.object(cloud_validation.subprocess, "run", return_value=suite), \
                 patch.object(cloud_validation, "run_system_preview") as preview:
                with self.assertRaises(ValueError):
                    cloud_validation.run_validation()
                preview.assert_not_called()

    def test_changed_replay_or_nonzero_writes_fail(self):
        suite = subprocess.CompletedProcess([], 0, "Synthetic suite: tests=215, failures=0, errors=0, skipped=0.\n", "")
        for receipts in (({"external_writes": 0, "live_model_calls": 0}, {"external_writes": 0, "live_model_calls": 0, "changed": True}),
                         ({"external_writes": 1, "live_model_calls": 0},) * 2):
            with tempfile.TemporaryDirectory() as temp:
                with patch.object(cloud_validation, "SYNTHETIC_RUNTIME_ROOT", Path(temp)), \
                     patch.object(cloud_validation.subprocess, "run", return_value=suite), \
                     patch.object(cloud_validation, "run_system_preview", side_effect=receipts):
                    with self.assertRaises(ValueError):
                        cloud_validation.run_validation()

    def test_summary_suppresses_exception_payload(self):
        output = io.StringIO()
        with patch.object(cloud_validation, "run_validation", side_effect=ValueError("private-marker")), redirect_stdout(output):
            self.assertEqual(cloud_validation.main(), 2)
        self.assertNotIn("private-marker", output.getvalue())
        self.assertIn("Failed", output.getvalue())

    def test_workflow_is_manual_private_default_branch_and_disabled_by_default(self):
        text = (ROOT / ".github/workflows/hub-cloud-validation.yml").read_text()
        for required in ("workflow_dispatch:", "github.event.repository.private == true", "github.event.repository.default_branch",
                         "vars.HUB_CLOUD_VALIDATION_ENABLED == 'true'", "runs-on: ubuntu-24.04", "timeout-minutes: 10",
                         "contents: read", "persist-credentials: false", "fetch-depth: 1", "cancel-in-progress: false",
                         "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"):
            self.assertIn(required, text)
        for forbidden in ("schedule:", "pull_request:", "push:", "secrets.", "self-hosted", "upload-artifact@", "preview-private-assignment", "contents: write", "id-token:"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
