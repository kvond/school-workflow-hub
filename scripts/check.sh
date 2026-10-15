#!/usr/bin/env bash
# One command every agent runs before marking a PR ready (docs/PARALLEL_AGENTS.md).
# Synthetic tests only; no network, credentials or school data.
set -u
cd "$(dirname "$0")/.."

status=0

echo "== Python unittest =="
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -q || status=1

echo "== Node UI tests =="
for f in tests/*.test.mjs; do
  if node --test "$f" >/dev/null 2>&1; then
    echo "ok   $f"
  else
    echo "FAIL $f"; status=1
  fi
done

if [ "$status" -eq 0 ]; then echo "== check.sh: PASS =="; else echo "== check.sh: FAIL =="; fi
exit "$status"
