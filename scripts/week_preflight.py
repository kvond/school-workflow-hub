"""Run the weekly teaching preflight over one normalized week snapshot file.

Reads a local JSON snapshot (see docs/WEEK_PREFLIGHT.md), prints only the
actionable exceptions plus the print packet, supply list and prepared digital
alternatives. Reads no school system, writes nothing and makes no model call.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from school_workflow_hub.workflows.week_preflight import (
    PreflightInputError, render_text, run_preflight,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", help="Path to a week-preflight-input/v1 JSON file")
    parser.add_argument("--json", action="store_true", help="Print the full result as JSON")
    args = parser.parse_args(argv)
    try:
        with open(args.snapshot, encoding="utf-8") as handle:
            snapshot = json.load(handle)
        result = run_preflight(snapshot)
    except (OSError, json.JSONDecodeError):
        print(json.dumps({"status": "blocked", "code": "snapshot_unreadable", "external_writes": 0}))
        return 2
    except PreflightInputError as error:
        print(json.dumps({"status": "blocked", "code": str(error), "external_writes": 0}))
        return 2
    if args.json:
        print(json.dumps(result, indent=1, sort_keys=True))
    else:
        titles = {lesson["lesson_id"]: lesson.get("title") or lesson["lesson_id"]
                  for lesson in snapshot["lessons"]}
        sys.stdout.write(render_text(result, titles))
    return 0 if result["status"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
