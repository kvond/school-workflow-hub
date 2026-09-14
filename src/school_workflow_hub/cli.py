"""Local fixture workflows and a separate read-only private snapshot review."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from typing import Sequence

from school_workflow_hub.core.config import ConfigError, load_config
from school_workflow_hub.core.database import (
    backup_database,
    initialize_database,
    require_synthetic_runtime_path,
    restore_database,
)
from school_workflow_hub.core.reports import render_teacher_summary
from school_workflow_hub.core.schedule import evaluate_cutoff, school_time
from school_workflow_hub.workflows.demo import run_synthetic_demo


def _print(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="school-workflow-hub",
        description="Local school workflow previews; no live platform execution.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate-config", help="validate safe local configuration")
    validate.add_argument("--config", required=True)

    initialize = subparsers.add_parser("init-db", help="initialize or migrate a local SQLite database")
    initialize.add_argument("--db", required=True)

    demo = subparsers.add_parser("demo", help="run the wholly synthetic, no-write demonstration")
    demo.add_argument("--db", required=True)
    demo.add_argument("--reports", required=True)
    demo.add_argument("--agenda-due-date", default="2026-09-16")

    preview_system = subparsers.add_parser("preview-system", help="build the complete offline synthetic acceptance preview")
    preview_system.add_argument("--config", required=True)
    preview_system.add_argument("--out", required=True)

    private_review = subparsers.add_parser("preview-private-assignment", help="read one approved private snapshot; print only opaque date/link proposals")
    private_review.add_argument("--private-root", required=True)
    private_review.add_argument("--input", required=True)

    report = subparsers.add_parser("report", help="render aggregate local teacher-summary preview")
    report.add_argument("--db", required=True)
    report.add_argument("--run-id", required=True)
    report.add_argument("--out", required=True)

    backup = subparsers.add_parser("backup", help="create a local SQLite backup")
    backup.add_argument("--db", required=True)
    backup.add_argument("--to", required=True)

    restore = subparsers.add_parser("restore", help="restore a backup to an explicit local target")
    restore.add_argument("--from", dest="source", required=True)
    restore.add_argument("--to", required=True)

    schedule = subparsers.add_parser(
        "evaluate-schedule", help="evaluate one fake-clock job/cutoff without scheduling anything"
    )
    schedule.add_argument("--config", required=True)
    schedule.add_argument("--job", required=True)
    schedule.add_argument("--date", required=True, help="fake local date in YYYY-MM-DD")
    schedule.add_argument("--dependency-required", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "preview-private-assignment":
            from school_workflow_hub.adapters.private_input import preview_private_assignment
            _print(preview_private_assignment(args.private_root, args.input))
        elif args.command == "validate-config":
            config = load_config(args.config)
            _print(
                {
                    "status": "valid",
                    "school_year": config.school_year,
                    "timezone": config.timezone,
                    "classes": [item.stable_id for item in config.classes],
                    "external_writes_hard_denied": True,
                    "live_models_disabled": True,
                }
            )
        elif args.command == "init-db":
            database_path = require_synthetic_runtime_path(args.db)
            connection = initialize_database(database_path)
            try:
                applied = connection.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
                _print({"status": "initialized", "db": str(database_path), "migrations": [row["version"] for row in applied]})
            finally:
                connection.close()
        elif args.command == "demo":
            _print(
                run_synthetic_demo(
                    require_synthetic_runtime_path(args.db),
                    require_synthetic_runtime_path(args.reports),
                    args.agenda_due_date,
                )
            )
        elif args.command == "preview-system":
            from school_workflow_hub.workflows.system_demo import run_system_preview
            _print(run_system_preview(Path(args.config), Path(args.out)))
        elif args.command == "report":
            connection = initialize_database(require_synthetic_runtime_path(args.db))
            try:
                path = render_teacher_summary(
                    connection, args.run_id, require_synthetic_runtime_path(args.out)
                )
                _print({"status": "rendered", "report": str(path)})
            finally:
                connection.close()
        elif args.command == "backup":
            _print(
                {
                    "status": "backed_up",
                    "backup": str(
                        backup_database(
                            require_synthetic_runtime_path(args.db),
                            require_synthetic_runtime_path(args.to),
                        )
                    ),
                }
            )
        elif args.command == "restore":
            _print(
                {
                    "status": "restored",
                    "database": str(
                        restore_database(
                            require_synthetic_runtime_path(args.source),
                            require_synthetic_runtime_path(args.to),
                        )
                    ),
                }
            )
        elif args.command == "evaluate-schedule":
            config = load_config(args.config)
            schedule = config.schedules.get(args.job)
            if not isinstance(schedule, dict):
                raise ConfigError(f"unknown schedule: {args.job}")
            fake_day = date.fromisoformat(args.date)
            job_time = school_time(fake_day, str(schedule["local_time"]), config.timezone)
            cutoff = school_time(fake_day, str(schedule["cutoff"]), config.timezone)
            decision = evaluate_cutoff(
                job_time=job_time,
                dependency_cutoff=cutoff,
                dependency_required=args.dependency_required,
            )
            _print(
                {
                    "status": decision.status,
                    "reason": decision.reason,
                    "local_time": decision.local_time,
                    "scheduled": False,
                    "fake_clock": True,
                }
            )
    except (ConfigError, OSError, ValueError, KeyError) as error:
        print(f"error: {error}")
        return 2
    return 0
