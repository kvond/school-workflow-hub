"""Run the input-to-report fixture twice; requires no account or network."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from school_workflow_hub.workflows.input_demo import run_input_demo


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        first = run_input_demo(args.out)
        if run_input_demo(args.out) != first:
            raise ValueError("input_demo_replay_mismatch")
    except (ValueError, OSError):
        print(json.dumps({"status": "failed", "code": "input_demo_failed", "external_writes": 0}))
        return 2
    print(json.dumps(first, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
