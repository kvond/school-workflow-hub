"""Remember each deck's VT skeleton between runs so an edit that drops a step is caught.

The skeleton is the list of labels the design depends on (aspect names, Build a
Rule, Pattern break, Getting Started, What if?, Concept Bank, response IDs). A step
that was there last week and is gone now is reported even if the deck still
"looks" complete.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from preflight.checks.markers import MARKER_RE
from preflight.checks.vt import BANK, EXACT_ASPECT, RULE, TIER, WHATIF
from preflight.models import Deck, Finding, Severity


def skeleton(deck: Deck) -> dict[str, int]:
    text = deck.text
    sig: Counter = Counter()
    for m in EXACT_ASPECT.finditer(text):
        sig[f"aspect:{m.group(1).strip().lower()}"] += 1
    for label in (RULE, TIER, WHATIF, BANK, "Pattern break", "Conflict case"):
        sig[label] = text.count(label)
    for kind, _code, qid in MARKER_RE.findall(text):
        sig[f"{kind}:{qid.strip()}"] += 1
    return {k: v for k, v in sig.items() if v}


class Baseline:
    """JSON file of {deck_id: {"title":..., "skeleton": {...}}}; local path or gs://bucket/key."""

    def __init__(self, location: str):
        self.location = location
        self.data: dict = {}
        raw = self._read()
        if raw:
            self.data = json.loads(raw)

    def _read(self) -> str | None:
        if self.location.startswith("gs://"):
            from google.cloud import storage

            bucket, _, key = self.location[5:].partition("/")
            blob = storage.Client().bucket(bucket).blob(key)
            return blob.download_as_text() if blob.exists() else None
        p = Path(self.location)
        return p.read_text() if p.exists() else None

    def save(self) -> None:
        raw = json.dumps(self.data, indent=1, sort_keys=True)
        if self.location.startswith("gs://"):
            from google.cloud import storage

            bucket, _, key = self.location[5:].partition("/")
            storage.Client().bucket(bucket).blob(key).upload_from_string(raw, content_type="application/json")
        else:
            Path(self.location).parent.mkdir(parents=True, exist_ok=True)
            Path(self.location).write_text(raw)

    def compare(self, course: str, deck: Deck) -> list[Finding]:
        now = skeleton(deck)
        before = (self.data.get(deck.file_id) or {}).get("skeleton")
        self.data[deck.file_id] = {"title": deck.title, "skeleton": now}
        if not before:
            return []
        lost = [k for k, v in before.items() if now.get(k, 0) < v]
        if not lost:
            return []
        pretty = [re.sub(r"^aspect:", "Critical aspect: ", k) for k in lost]
        return [Finding(Severity.FIX, course, "vt",
                        f"'{deck.title}' lost since the last preflight: {', '.join(pretty[:6])}"
                        f"{'…' if len(pretty) > 6 else ''}.",
                        fix="Check the last edit did not delete a VT step or response box (Version history).",
                        where=f"https://docs.google.com/presentation/d/{deck.file_id}/edit")]
