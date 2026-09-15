from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from school_workflow_hub.adapters.real import InfiniteCampusBrowserAdapter, SchoologyBrowserAdapter
from school_workflow_hub.core.actions import (
    attempt_external_execution,
    get_action,
    propose_action,
    reconcile_interrupted_action,
    record_execution_attempt,
    validate_action,
)
from school_workflow_hub.core.config import ConfigError, load_config, parse_config
from school_workflow_hub.core.database import (
    backup_database,
    connect,
    initialize_database,
    restore_database,
)
from school_workflow_hub.core.permissions import PermissionDenied, PermissionPolicy
from school_workflow_hub.core.reports import render_teacher_summary
from school_workflow_hub.core.runs import create_or_get_run, finish_run, get_run, record_exception
from school_workflow_hub.core.safe_logging import safe_json
from school_workflow_hub.core.schedule import evaluate_cutoff, school_time
from school_workflow_hub.core.sources import record_source_revision
from school_workflow_hub.cli import main as cli_main
from school_workflow_hub.workflows.demo import run_synthetic_demo


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FoundationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.db_path = Path(self.tempdir.name) / "state.sqlite3"
        self.connection = initialize_database(self.db_path)
        self.addCleanup(self.connection.close)

    def new_run(self):
        return create_or_get_run(self.connection, "test_workflow", {"fixture": "one"})

    def new_action(self):
        run = self.new_run()
        source = record_source_revision(
            self.connection,
            "agenda",
            "biology-a4:week-01",
            {"due": "2026-09-16"},
            "synthetic://agenda",
            run.id,
        )
        action = propose_action(
            self.connection,
            run_id=run.id,
            action_type="update_due_date",
            target_system="infinite_campus",
            target_stable_id="ic:assignment-001",
            source_stable_id="biology-a4:week-01",
            source_revision_id=source.id,
            expected_state={"due": "2026-09-15"},
            proposed_state={"due": "2026-09-16"},
            evidence={"source": source.id},
            required_permission="external.update",
        )
        return run, source, action

    def test_config_failure_and_valid_config(self) -> None:
        with self.assertRaises(ConfigError):
            parse_config({})
        config = load_config(PROJECT_ROOT / "config" / "example.json")
        self.assertEqual(config.timezone, "America/New_York")
        self.assertEqual(config.classes[0].stable_id, "biology-a4")

    def test_migration_repeatability_and_foreign_keys(self) -> None:
        self.connection.close()
        again = initialize_database(self.db_path)
        self.connection = again
        versions = again.execute("SELECT version FROM schema_migrations").fetchall()
        self.assertEqual(
            [row["version"] for row in versions],
            ["001_initial", "002_action_dependencies", "003_automation_spine", "004_bind_approvals_to_runs", "005_learning_observations"],
        )
        with self.assertRaises(sqlite3.IntegrityError):
            again.execute(
                "INSERT INTO execution_attempts "
                "(id, action_id, attempt_number, status, started_at, result_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("bad-attempt", "missing-action", 1, "blocked", "2026-09-13T00:00:00+00:00", "{}"),
            )

    def test_default_deny_and_external_write_denial(self) -> None:
        _, _, action = self.new_action()
        policy = PermissionPolicy.from_allowed(["local.read", "local.create", "external.update"])
        self.assertFalse(policy.allows("external.update"))
        validate_action(self.connection, action.id)
        with self.assertRaises(PermissionDenied):
            attempt_external_execution(self.connection, action.id, policy)
        self.assertEqual(get_action(self.connection, action.id)["state"], "proposed")

    def test_safe_logging_redacts_sensitive_values(self) -> None:
        payload = safe_json(
            {
                "password": "never-log-this",
                "nested": {"api_token": "also-never-log-this"},
                "event": "synthetic only",
            }
        )
        self.assertNotIn("never-log-this", payload)
        self.assertNotIn("also-never-log-this", payload)
        self.assertIn("[redacted]", payload)
        self.assertIn(
            "[redacted sensitive-looking text]",
            safe_json({"event": "connection failed with token=synthetic-secret-value"}),
        )

    def test_duplicate_run_behavior(self) -> None:
        first = self.new_run()
        second = create_or_get_run(self.connection, "test_workflow", {"fixture": "one"})
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.id, second.id)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) AS total FROM runs").fetchone()["total"], 1
        )

    def test_source_change_invalidates_stale_proposal(self) -> None:
        _, first_source, action = self.new_action()
        changed = record_source_revision(
            self.connection,
            "agenda",
            "biology-a4:week-01",
            {"due": "2026-09-18"},
            "synthetic://agenda",
        )
        self.assertTrue(changed.created)
        self.assertNotEqual(first_source.id, changed.id)
        self.assertEqual(get_action(self.connection, action.id)["state"], "superseded")

    def test_source_invalidation_is_namespaced_by_source_type(self) -> None:
        run = self.new_run()
        agenda = record_source_revision(
            self.connection, "agenda", "same-stable-id", {"due": "2026-09-16"}, "synthetic://agenda", run.id
        )
        action = propose_action(
            self.connection,
            run_id=run.id,
            action_type="update_due_date",
            target_system="infinite_campus",
            target_stable_id="ic:one",
            source_stable_id="same-stable-id",
            source_revision_id=agenda.id,
            expected_state={"due": "2026-09-15"},
            proposed_state={"due": "2026-09-16"},
            evidence={},
            required_permission="external.update",
        )
        record_source_revision(
            self.connection,
            "assignment_master",
            "same-stable-id",
            {"mapping": "v2"},
            "synthetic://assignment-master",
            run.id,
        )
        self.assertEqual(get_action(self.connection, action.id)["state"], "proposed")
        record_source_revision(
            self.connection,
            "agenda",
            "same-stable-id",
            {"due": "2026-09-18"},
            "synthetic://agenda",
            run.id,
        )
        self.assertEqual(get_action(self.connection, action.id)["state"], "superseded")

    def test_secondary_source_dependency_invalidates_a_plan(self) -> None:
        run = self.new_run()
        agenda = record_source_revision(
            self.connection, "agenda", "biology-a4:week-dependency", {"due": "2026-09-16"}, "synthetic://agenda", run.id
        )
        assignment_master = record_source_revision(
            self.connection,
            "assignment_master",
            "biology-a4:energy-intro",
            {"deck": "synthetic-deck-v1"},
            "synthetic://assignment-master",
            run.id,
        )
        action = propose_action(
            self.connection,
            run_id=run.id,
            action_type="update_due_date",
            target_system="infinite_campus",
            target_stable_id="ic:dependency",
            source_stable_id="biology-a4:week-dependency",
            source_revision_id=agenda.id,
            source_dependencies={"assignment_master": assignment_master.id},
            expected_state={"due": "2026-09-15"},
            proposed_state={"due": "2026-09-16"},
            evidence={"assignment_master_revision_id": assignment_master.id},
            required_permission="external.update",
        )
        record_source_revision(
            self.connection,
            "assignment_master",
            "biology-a4:energy-intro",
            {"deck": "synthetic-deck-v2"},
            "synthetic://assignment-master",
            run.id,
        )
        self.assertEqual(get_action(self.connection, action.id)["state"], "superseded")

    def test_action_identity_binds_expected_state_evidence_permission_and_expiry(self) -> None:
        run, source, original = self.new_action()
        replacement = propose_action(
            self.connection,
            run_id=run.id,
            action_type="update_due_date",
            target_system="infinite_campus",
            target_stable_id="ic:assignment-001",
            source_stable_id="biology-a4:week-01",
            source_revision_id=source.id,
            expected_state={"due": "2026-09-14"},
            proposed_state={"due": "2026-09-16"},
            evidence={"source": source.id, "readback": "synthetic-v2"},
            required_permission="external.update",
            expires_at="2099-09-16T12:00:00+00:00",
        )
        self.assertTrue(replacement.created)
        self.assertNotEqual(original.id, replacement.id)
        self.assertEqual(get_action(self.connection, original.id)["state"], "superseded")
        self.assertEqual(get_action(self.connection, replacement.id)["state"], "proposed")

    def test_expired_plan_cannot_validate_or_execute(self) -> None:
        run, source, _ = self.new_action()
        expired = propose_action(
            self.connection,
            run_id=run.id,
            action_type="update_due_date",
            target_system="infinite_campus",
            target_stable_id="ic:expired",
            source_stable_id="biology-a4:week-01",
            source_revision_id=source.id,
            expected_state={"due": "2026-09-15"},
            proposed_state={"due": "2026-09-16"},
            evidence={},
            required_permission="external.update",
            expires_at="2000-01-01T00:00:00+00:00",
        )
        with self.assertRaises(ValueError):
            validate_action(self.connection, expired.id)
        self.assertEqual(get_action(self.connection, expired.id)["state"], "expired")

    def test_proposal_rejects_inactive_source_revision(self) -> None:
        run, first_source, _ = self.new_action()
        record_source_revision(
            self.connection,
            "agenda",
            "biology-a4:week-01",
            {"due": "2026-09-18"},
            "synthetic://agenda",
            run.id,
        )
        with self.assertRaises(ValueError):
            propose_action(
                self.connection,
                run_id=run.id,
                action_type="update_due_date",
                target_system="infinite_campus",
                target_stable_id="ic:stale",
                source_stable_id="biology-a4:week-01",
                source_revision_id=first_source.id,
                expected_state={},
                proposed_state={"due": "2026-09-16"},
                evidence={},
                required_permission="external.update",
            )

    def test_reverted_source_content_creates_a_fresh_proposal_after_supersession(self) -> None:
        run = self.new_run()
        first_source = record_source_revision(
            self.connection,
            "agenda",
            "biology-a4:week-revert",
            {"due": "2026-09-16"},
            "synthetic://agenda",
            run.id,
        )
        first_action = propose_action(
            self.connection,
            run_id=run.id,
            action_type="update_due_date",
            target_system="infinite_campus",
            target_stable_id="ic:revert",
            source_stable_id="biology-a4:week-revert",
            source_revision_id=first_source.id,
            expected_state={"due": "2026-09-15"},
            proposed_state={"due": "2026-09-16"},
            evidence={},
            required_permission="external.update",
        )
        record_source_revision(
            self.connection,
            "agenda",
            "biology-a4:week-revert",
            {"due": "2026-09-18"},
            "synthetic://agenda",
            run.id,
        )
        reverted_source = record_source_revision(
            self.connection,
            "agenda",
            "biology-a4:week-revert",
            {"due": "2026-09-16"},
            "synthetic://agenda",
            run.id,
        )
        fresh_action = propose_action(
            self.connection,
            run_id=run.id,
            action_type="update_due_date",
            target_system="infinite_campus",
            target_stable_id="ic:revert",
            source_stable_id="biology-a4:week-revert",
            source_revision_id=reverted_source.id,
            expected_state={"due": "2026-09-15"},
            proposed_state={"due": "2026-09-16"},
            evidence={},
            required_permission="external.update",
        )
        self.assertNotEqual(first_action.id, fresh_action.id)
        self.assertTrue(fresh_action.created)
        self.assertEqual(get_action(self.connection, first_action.id)["state"], "superseded")
        self.assertEqual(get_action(self.connection, fresh_action.id)["state"], "proposed")

    def test_interrupted_attempt_requires_reconciliation(self) -> None:
        _, _, action = self.new_action()
        record_execution_attempt(self.connection, action.id, "interrupted", error_code="connection_lost")
        self.assertEqual(get_action(self.connection, action.id)["state"], "needs_reconciliation")
        reconcile_interrupted_action(self.connection, action.id, {"remote_state": "unknown"})
        reconciled = get_action(self.connection, action.id)
        self.assertEqual(reconciled["state"], "needs_review")
        self.assertEqual(reconciled["review_status"], "needs_review")

    def test_success_like_execution_records_are_refused(self) -> None:
        _, _, action = self.new_action()
        with self.assertRaises(ValueError):
            record_execution_attempt(self.connection, action.id, "succeeded")

    def test_exceptions_are_recorded(self) -> None:
        run = self.new_run()
        record_exception(self.connection, run.id, RuntimeError("synthetic failure"))
        recorded = get_run(self.connection, run.id)
        self.assertEqual(recorded["status"], "failed")
        self.assertEqual(recorded["error_type"], "RuntimeError")
        self.assertNotIn("synthetic failure", recorded["error_message"])
        self.assertIn("details withheld", recorded["error_message"])

    def test_timezone_and_cutoff_dependency(self) -> None:
        job = school_time(__import__("datetime").date(2026, 9, 14), "06:00", "America/New_York")
        before = school_time(__import__("datetime").date(2026, 9, 14), "05:45", "America/New_York")
        after = school_time(__import__("datetime").date(2026, 9, 14), "07:00", "America/New_York")
        self.assertEqual(evaluate_cutoff(job_time=job, dependency_cutoff=before, dependency_required=True).status, "ready")
        self.assertEqual(evaluate_cutoff(job_time=job, dependency_cutoff=after, dependency_required=True).status, "blocked")
        self.assertEqual(str(job.tzinfo), "America/New_York")

    def test_sqlite_backup_restore_round_trip(self) -> None:
        run = self.new_run()
        backup = Path(self.tempdir.name) / "backup.sqlite3"
        restored = Path(self.tempdir.name) / "restored.sqlite3"
        backup_database(self.db_path, backup)
        restore_database(backup, restored)
        restored_connection = initialize_database(restored)
        self.addCleanup(restored_connection.close)
        row = restored_connection.execute("SELECT id FROM runs WHERE id = ?", (run.id,)).fetchone()
        self.assertIsNotNone(row)

    def test_backup_restore_refuse_to_overwrite_existing_files(self) -> None:
        self.new_run()
        backup = Path(self.tempdir.name) / "backup.sqlite3"
        backup_database(self.db_path, backup)
        with self.assertRaises(FileExistsError):
            backup_database(self.db_path, backup)
        existing_restore_target = Path(self.tempdir.name) / "existing-restore.sqlite3"
        existing_restore_target.write_text("not a database", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            restore_database(backup, existing_restore_target)

    def test_report_refuses_to_overwrite_unrelated_existing_file(self) -> None:
        run = self.new_run()
        finish_run(self.connection, run.id)
        report_path = Path(self.tempdir.name) / "existing.md"
        report_path.write_text("teacher-created content", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            render_teacher_summary(self.connection, run.id, report_path)

    def test_real_adapters_are_explicitly_not_configured(self) -> None:
        self.assertEqual(SchoologyBrowserAdapter().inspect_target("course:1").status, "not_configured")
        self.assertEqual(InfiniteCampusBrowserAdapter().execute_authorized_action("action:1").status, "not_configured")

    def test_synthetic_demo_is_idempotent_and_zero_write(self) -> None:
        reports = Path(self.tempdir.name) / "reports"
        first = run_synthetic_demo(self.db_path, reports)
        second = run_synthetic_demo(self.db_path, reports)
        self.assertTrue(first["run_created"])
        self.assertFalse(second["run_created"])
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual(first["due_date_action_id"], second["due_date_action_id"])
        self.assertEqual(first["external_writes"], 0)
        self.assertEqual(first["live_model_calls"], 0)
        text = Path(first["report_path"]).read_text(encoding="utf-8")
        self.assertIn("External writes: 0", text)
        self.assertIn("Live model calls: 0", text)
        action_count = self.connection.execute("SELECT COUNT(*) AS total FROM proposed_actions").fetchone()["total"]
        self.assertEqual(action_count, 1)

    def test_synthetic_demo_source_change_supersedes_old_action(self) -> None:
        reports = Path(self.tempdir.name) / "reports"
        first = run_synthetic_demo(self.db_path, reports, "2026-09-16")
        changed = run_synthetic_demo(self.db_path, reports, "2026-09-18")
        self.assertNotEqual(first["run_id"], changed["run_id"])
        self.assertEqual(get_action(self.connection, first["due_date_action_id"])["state"], "superseded")
        self.assertEqual(get_action(self.connection, changed["due_date_action_id"])["state"], "proposed")

    def test_cli_rejects_repository_runtime_target(self) -> None:
        unsafe_path = PROJECT_ROOT / "runtime.sqlite3"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli_main(["init-db", "--db", str(unsafe_path)]), 2)
