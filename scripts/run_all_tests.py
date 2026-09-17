"""One offline command for all unit, integration, and synthetic end-to-end tests."""

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py", top_level_dir=str(ROOT))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print("Synthetic suite: tests={}, failures={}, errors={}, skipped={}.".format(
        result.testsRun, len(result.failures), len(result.errors), len(result.skipped)))
    # Distinct reasons only; the gate allows a fixed set and blocks any other skip.
    print("Skip reasons: {}.".format(
        "; ".join(sorted({reason for _, reason in result.skipped})) or "none"))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
