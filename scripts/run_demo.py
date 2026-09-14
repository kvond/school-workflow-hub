"""Run the local synthetic demonstration without installing a package."""

from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from school_workflow_hub.workflows.demo import run_synthetic_demo  # noqa: E402


if __name__ == "__main__":
    result = run_synthetic_demo(
        Path("/private/tmp/school-workflow-hub") / "demo.sqlite3",
        Path("/private/tmp/school-workflow-hub") / "reports",
    )
    print(result)
