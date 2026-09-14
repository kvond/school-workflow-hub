"""Prepare tomorrow's offline review packet; never starts a live acceptance test."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from school_workflow_hub.core.database import require_synthetic_runtime_path
from school_workflow_hub.core.safe_logging import safe_exception_message
from school_workflow_hub.workflows.system_demo import run_system_preview, _write_once


TESTS = (
    (1, "must_test", "Calendar and IC date preview", ("todoist:synthetic:calendar-1", "todoist:synthetic:calendar-2")),
    (2, "must_test", "Schoology and IC surface mapping and links", ("todoist:synthetic:mapping-1", "todoist:synthetic:mapping-2")),
    (3, "must_test", "Class window and anonymized feedback review", ("todoist:synthetic:feedback",)),
    (4, "must_test", "Summaries, folder plans, reports and exceptions", ("todoist:synthetic:reports",)),
    (5, "must_test", "Forensics prompt inventory and replacements", ("todoist:synthetic:prompts",)),
    (6, "must_test", "Zoom micromovie", ("todoist:synthetic:micromovie",)),
    (7, "if_time", "Synthetic partial failure, retry and rollback", ("todoist:synthetic:mapping-1", "todoist:synthetic:mapping-2")),
    (8, "optional_canary", "One separately authorized reversible live UI change", ("todoist:synthetic:mapping-1", "todoist:synthetic:mapping-2")),
)


def prepare_acceptance(config_path: Path, output_directory: Path) -> dict:
    """Run the existing integrated preview twice and require exact reuse."""
    out = require_synthetic_runtime_path(output_directory)
    checklist_path = out / "acceptance-checklist.json"
    if checklist_path.is_symlink():
        raise ValueError("acceptance checklist must not be a symlink")
    first = run_system_preview(config_path, out)
    second = run_system_preview(config_path, out)
    if first != second:
        raise ValueError("identical synthetic replay did not produce the same receipt")
    if first.get("external_writes") != 0 or first.get("live_model_calls") != 0:
        raise ValueError("the acceptance packet requires zero external writes and model calls")
    packet = {
        "schema_version": "tomorrow-acceptance/v1",
        "status": "awaiting_teacher_review",
        "instructions": "docs/TOMORROW_TESTS.md",
        "preview": first["preview"],
        "summary": first["summary"],
        "reports": first["reports"],
        "preview_fingerprint": first["preview_fingerprint"],
        "ledger_counts": first["ledger_counts"],
        "identical_rerun_no_duplicates": True,
        "external_writes": 0,
        "live_model_calls": 0,
        "live_readers": "not_configured",
        "real_system_acceptance": "not_run",
        "tests": [
            {"number": number, "section": section, "title": title, "task_ids": list(task_ids),
             "decision": "pending", "result": "not_run", "notes": ""}
            for number, section, title, task_ids in TESTS
        ],
    }
    text = json.dumps(packet, indent=2, sort_keys=True) + "\n"
    if checklist_path.exists():
        if checklist_path.read_text(encoding="utf-8") != text:
            raise FileExistsError("existing checklist differs; preserve teacher decisions and choose a new output directory")
    else:
        _write_once(checklist_path, text)
    return packet


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/example.json")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        packet = prepare_acceptance(Path(args.config), Path(args.out))
    except (OSError, ValueError, KeyError) as error:
        print(json.dumps({"status": "failed", "error": safe_exception_message(error)}))
        return 2
    print(json.dumps({
        "status": packet["status"], "checklist": str(Path(args.out) / "acceptance-checklist.json"),
        "preview": packet["preview"], "identical_rerun_no_duplicates": True,
        "reports": packet["reports"],
        "external_writes": 0, "live_model_calls": 0, "real_system_acceptance": "not_run",
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
