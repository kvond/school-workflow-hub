from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from school_workflow_hub.core.config import ConfigError, load_config, parse_config
from school_workflow_hub.core.database import initialize_database
from school_workflow_hub.core.permissions import PermissionDenied, PermissionPolicy
from school_workflow_hub.core.runs import create_or_get_run, get_run
from school_workflow_hub.core.spine import (
    AuthorizationDenied,
    AuthorizationRequest,
    UnsafePayloadError,
    add_work_item_dependency,
    authorize_and_consume,
    create_or_get_work_item,
    execute_dry_run,
    get_work_item,
    issue_one_time_approval,
    register_account_binding,
    revoke_one_time_approval,
    run_mock_end_to_end,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class AutomationSpineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.connection = initialize_database(Path(self.tempdir.name) / "spine.sqlite3")
        self.addCleanup(self.connection.close)

    def new_run(self, fixture: str = "one"):
        return create_or_get_run(self.connection, "automation_spine_test", {"fixture": fixture})

    def new_work_item(self, fixture: str = "one", scope: str = "local.create"):
        run = self.new_run(fixture)
        return run, create_or_get_work_item(
            self.connection,
            run_id=run.id,
            kind="render_preview",
            target_system="local_report",
            target_stable_id=f"synthetic:preview-{fixture}",
            required_permission=scope,
            payload={"fixture": fixture, "output": "preview-only"},
        )

    @staticmethod
    def request_for(work_item, fixture: str = "one", scope: str = "local.create") -> AuthorizationRequest:
        return AuthorizationRequest(
            actor_id="operator:synthetic",
            scope=scope,
            target_system="local_report",
            target_stable_id=f"synthetic:preview-{fixture}",
            plan_hash=work_item.plan_hash,
        )

    def test_schema_has_all_shared_ledger_tables_and_immutable_plans(self) -> None:
        expected = {
            "account_bindings",
            "work_items",
            "work_item_dependencies",
            "run_work_items",
            "run_steps",
            "artifacts",
            "approval_grants",
            "audit_events",
        }
        tables = {
            row["name"]
            for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        self.assertTrue(expected.issubset(tables))
        run, work_item = self.new_work_item()
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "UPDATE work_items SET plan_json = ? WHERE id = ?", ("{}", work_item.id)
            )

    def test_idempotent_work_items_track_runs_and_reject_sensitive_payloads(self) -> None:
        first_run, first = self.new_work_item()
        same = create_or_get_work_item(
            self.connection,
            run_id=first_run.id,
            kind="render_preview",
            target_system="local_report",
            target_stable_id="synthetic:preview-one",
            required_permission="local.create",
            payload={"fixture": "one", "output": "preview-only"},
        )
        self.assertFalse(same.created)
        self.assertEqual(first.id, same.id)
        changed_run, changed = self.new_work_item("two")
        self.assertNotEqual(first.plan_hash, changed.plan_hash)
        linked = self.connection.execute(
            "SELECT role FROM run_work_items WHERE run_id = ? AND work_item_id = ?",
            (changed_run.id, changed.id),
        ).fetchone()
        self.assertEqual(linked["role"], "planned")
        with self.assertRaises(UnsafePayloadError):
            create_or_get_work_item(
                self.connection,
                run_id=first_run.id,
                kind="render_preview",
                target_system="local_report",
                target_stable_id="synthetic:unsafe",
                required_permission="local.create",
                payload={"api_token": "never-store"},
            )
        with self.assertRaises(UnsafePayloadError):
            create_or_get_work_item(
                self.connection,
                run_id=first_run.id,
                kind="render_preview",
                target_system="local_report",
                target_stable_id="synthetic:personal-data",
                required_permission="local.create",
                payload={"learner_stable_id": "learner:real-person-001"},
            )
        with self.assertRaises(UnsafePayloadError):
            create_or_get_work_item(
                self.connection,
                run_id=first_run.id,
                kind="render_preview",
                target_system="local_report",
                target_stable_id="synthetic:email-data",
                required_permission="local.create",
                payload={"contact": "person@example.org"},
            )

    def test_dependencies_are_recorded_and_cycles_are_refused(self) -> None:
        _, first = self.new_work_item("one")
        _, second = self.new_work_item("two")
        add_work_item_dependency(
            self.connection,
            work_item_id=second.id,
            depends_on_work_item_id=first.id,
        )
        with self.assertRaises(ValueError):
            add_work_item_dependency(
                self.connection,
                work_item_id=first.id,
                depends_on_work_item_id=second.id,
            )

    def test_non_secret_account_bindings_are_idempotent_and_reject_email_or_secret_metadata(self) -> None:
        first = register_account_binding(
            self.connection,
            provider="synthetic",
            account_alias="school-account",
            binding_reference="school-browser-profile",
            metadata={"owner": "teacher"},
        )
        same = register_account_binding(
            self.connection,
            provider="synthetic",
            account_alias="school-account",
            binding_reference="school-browser-profile",
        )
        self.assertEqual(first, same)
        with self.assertRaises(ValueError):
            register_account_binding(
                self.connection,
                provider="synthetic",
                account_alias="email-reference",
                binding_reference="teacher@example.org",
            )
        with self.assertRaises(ValueError):
            register_account_binding(
                self.connection,
                provider="synthetic",
                account_alias="teacher@example.org",
                binding_reference="safe-alias",
            )
        with self.assertRaises(UnsafePayloadError):
            register_account_binding(
                self.connection,
                provider="synthetic",
                account_alias="unsafe-metadata",
                binding_reference="safe-alias",
                metadata={"credential": "never-store"},
            )

    def test_approval_is_identity_scope_target_and_plan_bound_then_single_use(self) -> None:
        run, work_item = self.new_work_item()
        request = self.request_for(work_item)
        grant = issue_one_time_approval(
            self.connection,
            run_id=run.id,
            request=request,
            grant_nonce="grant-one",
            expires_at="2099-01-01T00:00:00+00:00",
        )
        mismatched = AuthorizationRequest(
            actor_id=request.actor_id,
            scope=request.scope,
            target_system=request.target_system,
            target_stable_id="synthetic:other-target",
            plan_hash=request.plan_hash,
        )
        policy = PermissionPolicy.from_allowed(["local.create"])
        with self.assertRaises(AuthorizationDenied):
            authorize_and_consume(
                self.connection, run_id=run.id, grant_id=grant.id, request=mismatched, policy=policy
            )
        self.assertEqual(
            self.connection.execute("SELECT status FROM approval_grants WHERE id = ?", (grant.id,)).fetchone()["status"],
            "active",
        )
        authorize_and_consume(self.connection, run_id=run.id, grant_id=grant.id, request=request, policy=policy)
        self.assertEqual(
            self.connection.execute("SELECT status FROM approval_grants WHERE id = ?", (grant.id,)).fetchone()["status"],
            "consumed",
        )
        with self.assertRaises(AuthorizationDenied):
            authorize_and_consume(self.connection, run_id=run.id, grant_id=grant.id, request=request, policy=policy)
        events = self.connection.execute(
            "SELECT event_type FROM audit_events WHERE entity_id = ? ORDER BY created_at", (grant.id,)
        ).fetchall()
        self.assertIn("authorization_denied", [row["event_type"] for row in events])
        self.assertIn("consumed", [row["event_type"] for row in events])

    def test_expired_revoked_and_external_scope_grants_do_not_broaden_policy(self) -> None:
        run, work_item = self.new_work_item()
        request = self.request_for(work_item)
        expired = issue_one_time_approval(
            self.connection,
            run_id=run.id,
            request=request,
            grant_nonce="grant-expired",
            expires_at="2099-01-01T00:00:00+00:00",
        )
        self.connection.execute(
            "UPDATE approval_grants SET expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
            (expired.id,),
        )
        with self.assertRaises(AuthorizationDenied):
            authorize_and_consume(
                self.connection,
                run_id=run.id,
                grant_id=expired.id,
                request=request,
                policy=PermissionPolicy.from_allowed(["local.create"]),
            )
        self.assertEqual(
            self.connection.execute("SELECT status FROM approval_grants WHERE id = ?", (expired.id,)).fetchone()["status"],
            "expired",
        )

        revoked = issue_one_time_approval(
            self.connection,
            run_id=run.id,
            request=request,
            grant_nonce="grant-revoked",
            expires_at="2099-01-01T00:00:00+00:00",
        )
        revoke_one_time_approval(self.connection, grant_id=revoked.id, actor_id=request.actor_id)
        with self.assertRaises(AuthorizationDenied):
            authorize_and_consume(
                self.connection,
                run_id=run.id,
                grant_id=revoked.id,
                request=request,
                policy=PermissionPolicy.from_allowed(["local.create"]),
            )

        external_run, external_item = self.new_work_item("external", scope="external.update")
        external_request = self.request_for(external_item, fixture="external", scope="external.update")
        external = issue_one_time_approval(
            self.connection,
            run_id=external_run.id,
            request=external_request,
            grant_nonce="grant-external",
            expires_at="2099-01-01T00:00:00+00:00",
        )
        with self.assertRaises(AuthorizationDenied):
            authorize_and_consume(
                self.connection,
                run_id=external_run.id,
                grant_id=external.id,
                request=external_request,
                policy=PermissionPolicy.from_allowed(["external.update"]),
            )
        self.assertEqual(
            self.connection.execute("SELECT status FROM approval_grants WHERE id = ?", (external.id,)).fetchone()["status"],
            "active",
        )

    def test_write_mode_is_hard_denied_before_an_approval_is_consumed(self) -> None:
        run, work_item = self.new_work_item()
        request = self.request_for(work_item)
        grant = issue_one_time_approval(
            self.connection,
            run_id=run.id,
            request=request,
            grant_nonce="grant-write-mode",
            expires_at="2099-01-01T00:00:00+00:00",
        )
        with self.assertRaises(PermissionDenied):
            execute_dry_run(
                self.connection,
                run_id=run.id,
                work_item_id=work_item.id,
                grant_id=grant.id,
                request=request,
                policy=PermissionPolicy.from_allowed(["local.create"]),
                mode="write",
            )
        self.assertEqual(
            self.connection.execute("SELECT status FROM approval_grants WHERE id = ?", (grant.id,)).fetchone()["status"],
            "active",
        )
        blocked = self.connection.execute(
            "SELECT status FROM run_steps WHERE run_id = ? ORDER BY step_index", (run.id,)
        ).fetchall()
        self.assertEqual([row["status"] for row in blocked], ["blocked"])

    def test_consumed_local_approval_can_reconcile_only_the_missing_preview_artifact(self) -> None:
        run, work_item = self.new_work_item("reconcile")
        request = self.request_for(work_item, fixture="reconcile")
        grant = issue_one_time_approval(
            self.connection,
            run_id=run.id,
            request=request,
            grant_nonce="grant-reconcile",
            expires_at="2099-01-01T00:00:00+00:00",
        )
        policy = PermissionPolicy.from_allowed(["local.create"])
        # Simulate an interruption after the authorization transaction commits
        # but before the local preview artifact is written.
        authorize_and_consume(
            self.connection, run_id=run.id, grant_id=grant.id, request=request, policy=policy
        )
        result = execute_dry_run(
            self.connection,
            run_id=run.id,
            work_item_id=work_item.id,
            grant_id=grant.id,
            request=request,
            policy=policy,
        )
        self.assertIsNotNone(result.artifact_id)
        events = self.connection.execute(
            "SELECT event_type FROM audit_events WHERE entity_id = ?", (grant.id,)
        ).fetchall()
        self.assertIn("dry_run_reconciled", [row["event_type"] for row in events])

    def test_consumed_grant_cannot_reconcile_a_different_run(self) -> None:
        first_run, work_item = self.new_work_item("cross-run")
        request = self.request_for(work_item, fixture="cross-run")
        grant = issue_one_time_approval(
            self.connection,
            run_id=first_run.id,
            request=request,
            grant_nonce="grant-cross-run",
            expires_at="2099-01-01T00:00:00+00:00",
        )
        policy = PermissionPolicy.from_allowed(["local.create"])
        authorize_and_consume(
            self.connection,
            run_id=first_run.id,
            grant_id=grant.id,
            request=request,
            policy=policy,
        )
        second_run = self.new_run("cross-run-second")
        same_item = create_or_get_work_item(
            self.connection,
            run_id=second_run.id,
            kind="render_preview",
            target_system="local_report",
            target_stable_id="synthetic:preview-cross-run",
            required_permission="local.create",
            payload={"fixture": "cross-run", "output": "preview-only"},
        )
        with self.assertRaises(AuthorizationDenied):
            execute_dry_run(
                self.connection,
                run_id=second_run.id,
                work_item_id=same_item.id,
                grant_id=grant.id,
                request=request,
                policy=policy,
            )
        self.assertIsNone(
            self.connection.execute(
                "SELECT id FROM artifacts WHERE run_id = ? AND work_item_id = ?",
                (second_run.id, same_item.id),
            ).fetchone()
        )

    def test_mocked_end_to_end_persists_audits_steps_and_zero_external_work(self) -> None:
        first = run_mock_end_to_end(self.connection)
        second = run_mock_end_to_end(self.connection)
        self.assertEqual(first, second)
        run = get_run(self.connection, first.run_id)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["external_writes"], 0)
        self.assertEqual(run["live_model_calls"], 0)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) AS total FROM artifacts WHERE id = ?", (first.artifact_id,)).fetchone()["total"],
            1,
        )
        self.assertGreater(
            self.connection.execute("SELECT COUNT(*) AS total FROM run_steps WHERE run_id = ?", (first.run_id,)).fetchone()["total"],
            0,
        )
        self.assertGreater(
            self.connection.execute("SELECT COUNT(*) AS total FROM audit_events").fetchone()["total"],
            0,
        )

    def test_config_refuses_write_mode(self) -> None:
        config = load_config(PROJECT_ROOT / "config" / "example.json")
        self.assertEqual(config.operation_mode, "dry_run")
        with self.assertRaises(ConfigError):
            parse_config(
                {
                    "school_year": "2026-2027",
                    "timezone": "America/New_York",
                    "classes": [{"id": "synthetic", "name": "Synthetic", "calendar_id": "synthetic"}],
                    "approved_destinations": {},
                    "class_meeting_calendar": {},
                    "schedules": {},
                    "feature_flags": {"external_writes": False, "live_models": False},
                    "model_provider": {},
                    "runtime": {
                        "database_path": "/private/tmp/school-workflow-hub/test.sqlite3",
                        "reports_directory": "/private/tmp/school-workflow-hub/reports",
                    },
                    "permission_policy": {"default": "deny", "allowed": []},
                    "operation_mode": "write",
                }
            )
