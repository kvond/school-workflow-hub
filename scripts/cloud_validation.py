"""Bounded cloud smoke test: fixtures only, no private input or live connectors.

Uses the runner's installed Python standard library; no pip install or paid API.
Only a fixed-format aggregate summary is printed for GitHub's job summary.
"""

from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from school_workflow_hub.core.database import SYNTHETIC_RUNTIME_ROOT
from school_workflow_hub.workflows.system_demo import run_system_preview


# The signature tests skip unless requirements-hosting.txt is installed, which the
# bare runner and a standard-library local check both lack. Any other skip is a gap.
ALLOWED_SKIP_REASONS = frozenset({"Install requirements-hosting.txt for signature tests"})


def _skips_are_allowed(output):
    """True only when every reported skip reason is one this gate expects."""
    reasons = re.search(r"^Skip reasons: (.+)\.$", output, re.M)
    if not reasons:
        return False
    return set(part.strip() for part in reasons.group(1).split(";")) <= ALLOWED_SKIP_REASONS


def run_validation():
    suite = subprocess.run(
        [sys.executable, str(ROOT / "scripts/run_all_tests.py")], cwd=ROOT,
        capture_output=True, text=True, timeout=180, check=False,
    )
    match = re.search(r"^Synthetic suite: tests=(\d+), failures=0, errors=0, skipped=(\d+)\.$", suite.stdout, re.M)
    if suite.returncode != 0 or not match or int(match.group(1)) < 215:
        raise ValueError("test_suite_failed_or_incomplete")
    if int(match.group(2)) and not _skips_are_allowed(suite.stdout):
        raise ValueError("test_suite_failed_or_incomplete")
    SYNTHETIC_RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cloud-validation-", dir=SYNTHETIC_RUNTIME_ROOT) as temporary:
        first = run_system_preview(ROOT / "config/example.json", Path(temporary))
        second = run_system_preview(ROOT / "config/example.json", Path(temporary))
        if first != second:
            raise ValueError("synthetic_replay_changed")
        if first.get("external_writes") != 0 or first.get("live_model_calls") != 0:
            raise ValueError("live_execution_forbidden")
    # No databases, generated reports, private links, source IDs or input values
    # escape this process. TemporaryDirectory removes only its own fixture run.
    return {"tests_passed": int(match.group(1)), "identical_replay": True,
            "external_writes": 0, "live_model_calls": 0,
            "school_source_reads": 0, "private_inputs_loaded": 0}


def main():
    print("# School Workflow Hub — cloud validation\n")
    try:
        result = run_validation()
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        print("**Failed.** Reproduce with `python3 scripts/run_all_tests.py`; no live school job was enabled.")
        return 2
    print("**Passed:** {} tests and identical synthetic replay.\n".format(result["tests_passed"]))
    print("School source reads: 0. Private inputs: 0. External writes: 0. Live model calls: 0.\n")
    print("This verifies code execution only—not live Agenda, lesson, Schoology or IC processing.\n")
    print("No schedule is enabled. No persistent school database or report archive was created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
