"""Command line and Cloud Run entry point.

    python -m preflight.cli run                  # next teaching week, live sources, print the report
    python -m preflight.cli run --week 8         # a specific week
    python -m preflight.cli run --publish        # also write the Doc + print packet to the output folder
    python -m preflight.cli run --snapshot DIR   # offline: DIR/agenda.xlsx, DIR/drive/, DIR/schoology.json
    python -m preflight.cli authorize client_secret.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path

from preflight import report
from preflight.baseline import Baseline
from preflight.config import load_config
from preflight.context import build_context
from preflight.runner import run
from preflight.sources import agenda as ag


def next_week(weeks: dict, today: dt.date) -> int:
    """The week to prepare: the one starting next Monday (or this week, if run over the weekend before it)."""
    monday = today + dt.timedelta(days=(7 - today.weekday()) % 7 or 7)
    for n, w in weeks.items():
        if w.monday == monday:
            return n
    found = ag.week_for_date(weeks, monday)
    if found is None:
        raise SystemExit(f"No Agenda week starts {monday}")
    return found


def cmd_run(args) -> int:
    cfg = load_config(args.config)
    creds = None
    if args.snapshot:
        from preflight.sources.gdrive import SnapshotDrive
        from preflight.sources.schoology import SnapshotSchoology

        root = Path(args.snapshot)
        workbook = ag.load_xlsx(root / "agenda.xlsx")
        drive = SnapshotDrive(root / "drive")
        schoology = SnapshotSchoology(root / "schoology.json")
    else:
        from preflight.auth import google_credentials, schoology_keys
        from preflight.sources.gdrive import GoogleDrive
        from preflight.sources.schoology import SchoologyAPI

        creds = google_credentials()
        tabs = [c.tab for c in cfg.courses] + [cfg.weeks_tab, cfg.worksheets_tab, cfg.turnin_tab]
        workbook = ag.load_sheets(creds, cfg.agenda_id, tabs)
        drive = GoogleDrive(creds)
        keys = schoology_keys()
        schoology = SchoologyAPI(*keys) if keys else None

    weeks = ag.parse_weeks(workbook.tab(cfg.weeks_tab))
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    week_no = args.week or next_week(weeks, today)

    ctx = build_context(cfg, workbook, drive, schoology, week_no)
    baseline = None
    location = args.baseline or (f"gs://{cfg.gcs_bucket}/vt-baseline.json" if cfg.gcs_bucket else "")
    if location:
        baseline = Baseline(location)
    result = run(ctx, baseline)

    text = report.markdown(result)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "preflight.md").write_text(text)
        (out / "preflight.json").write_text(json.dumps(report.to_json(result), indent=1, default=str))
    print(text)

    if args.publish:
        if creds is None or not cfg.output_folder_id:
            print("--publish needs live credentials and output_folder_id in config.yaml", file=sys.stderr)
            return 2
        from preflight.output.publish import publish

        links = publish(result, creds, cfg.output_folder_id)
        print("\n".join(f"{k}: {v}" for k, v in links.items()))
    if baseline is not None and not args.no_save_baseline:
        baseline.save()
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "WARNING"))
    p = argparse.ArgumentParser(prog="preflight")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the weekly preflight")
    r.add_argument("--config", default=os.environ.get("PREFLIGHT_CONFIG", "config.yaml"))
    r.add_argument("--week", type=int, help="Agenda week number (default: next week)")
    r.add_argument("--today", help="pretend today is YYYY-MM-DD (for testing)")
    r.add_argument("--snapshot", help="offline snapshot directory instead of live sources")
    r.add_argument("--out", help="also write preflight.md and preflight.json here")
    r.add_argument("--baseline", help="VT baseline file (local path or gs://...)")
    r.add_argument("--no-save-baseline", action="store_true")
    r.add_argument("--publish", action="store_true", help="write the Doc and print packet to the output folder")
    r.set_defaults(func=cmd_run)
    a = sub.add_parser("authorize", help="one-time Google sign-in; saves secrets/token.json")
    a.add_argument("client_secrets")
    a.set_defaults(func=lambda args: __import__("preflight.auth", fromlist=["x"]).authorize_interactively(
        args.client_secrets) or 0)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
