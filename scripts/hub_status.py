"""Agent-readable build inventory; does not probe accounts or start workflows."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def inventory(root=ROOT):
    manifest = json.loads((root / "config/capabilities.json").read_text())
    if manifest.get("schema_version") != "hub-capabilities/v1":
        raise ValueError("invalid_capability_schema")
    seen = set()
    for item in manifest["capabilities"]:
        if item["id"] in seen:
            raise ValueError("duplicate_capability")
        seen.add(item["id"])
        for name in item["evidence_files"]:
            path = (root / name).resolve()
            if root.resolve() not in path.parents or not path.is_file():
                raise ValueError("missing_capability_evidence")
    # Declared readiness is not an authorization, login check or hosted receipt.
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = inventory()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("School Workflow Hub — implementation inventory (not live verification)")
        for item in result["capabilities"]:
            print("{}: {}".format(item["id"], item["state"]))
            print("  Next: " + "; ".join(item["remaining"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
