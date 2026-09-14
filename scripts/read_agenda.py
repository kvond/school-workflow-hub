"""Read approved Sheets scope and print ONLY an opaque observation.

No live credential is provided by Codex's Drive connector. Runtime must supply
an authorized short-lived token and stable private fingerprint key externally.
"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from school_workflow_hub.adapters.google_sheets import SheetsReader, SheetsReadError, fingerprint_snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--range", action="append", default=[], dest="ranges",
                        help="Explicit quoted A1 rectangle; at most four, 5000 cells total")
    args = parser.parse_args()
    try:
        token = os.environ.get("HUB_GOOGLE_ACCESS_TOKEN", "")
        source = os.environ.get("HUB_AGENDA_SPREADSHEET_ID", "")
        raw_key = os.environ.get("HUB_OBSERVATION_KEY_HEX", "")
        if len(raw_key) != 64:
            raise SheetsReadError("invalid_fingerprint_key")
        try:
            key = bytes.fromhex(raw_key)
        except ValueError:
            raise SheetsReadError("invalid_fingerprint_key") from None
        ranges = tuple(args.ranges)
        data = SheetsReader(token).read(source, ranges)
        result = fingerprint_snapshot(data, key, ranges)
    except SheetsReadError as error:
        print(json.dumps({"status": "blocked", "code": str(error), "external_writes": 0}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
