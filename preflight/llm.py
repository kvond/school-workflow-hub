"""Optional second opinion on each lesson's VT design, from Claude on Vertex AI.

The deterministic checks find missing or misordered steps. They cannot tell whether
a contrast set still varies only one thing after someone swapped an example, or
whether the sequence still makes sense. This module asks Claude exactly that, per
deck, and turns only concrete problems into findings. It is off unless
`llm_review: true` is set in config.yaml.
"""

from __future__ import annotations

import json
import logging

from preflight.models import Deck, Finding, Severity

log = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"

SYSTEM = """You review high school science lesson decks built on Variation Theory, for the teacher who wrote them.

The design rules:
- Each critical aspect is a difference students must notice. Its block runs: a question with the difference inside it,
  a contrast set (cases that differ in ONE important way, with the rest held steady), "Finish this sentence as a rule",
  a "Pattern break" that strains the rule (not a trick), a 3-tier "Getting Started" question, and a response slide with
  "Your first answer" and "Your revised answer".
- The rule must not be shown before students build it.
- A Concept Bank ("Define these in your own words") asks students to connect terms, not just define them.
- Conflict cases, what-ifs and models appear only when the content calls for them.

Report only problems a teacher would want fixed before teaching: a contrast where more than one important thing varies,
a pattern break that does not strain the rule, a step that gives away the answer, a sequence that no longer builds
(for example a later step depending on a slide that was removed), or a slide whose science contradicts the aspect.
Do not report style, wording preferences, or anything you are unsure of. Quote slide numbers. If nothing needs fixing,
return an empty list."""

SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["FIX", "CHECK"]},
                    "slides": {"type": "string"},
                    "problem": {"type": "string"},
                    "fix": {"type": "string"},
                },
                "required": ["severity", "slides", "problem", "fix"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["findings"],
    "additionalProperties": False,
}


def _deck_prompt(deck: Deck) -> str:
    parts = [f"Deck: {deck.title}"]
    for s in deck.slides:
        if s.text.strip():
            parts.append(f"--- slide {s.number}\n{s.text.strip()}")
    return "\n".join(parts)


def review(decks: list[tuple[str, Deck]], project_id: str, region: str = "global") -> list[Finding]:
    import anthropic
    from anthropic import AnthropicVertex

    client = AnthropicVertex(project_id=project_id, region=region)
    out: list[Finding] = []
    for course, deck in decks:
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=16000,
                system=SYSTEM,
                output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
                messages=[{"role": "user", "content": _deck_prompt(deck)}],
            )
        except anthropic.APIConnectionError as err:
            log.warning("VT review skipped for %s: %s", deck.title, err)
            continue
        except anthropic.APIStatusError as err:
            log.warning("VT review failed for %s: %s %s", deck.title, err.status_code, err.message)
            continue
        if resp.stop_reason == "refusal":
            log.warning("VT review declined for %s", deck.title)
            continue
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            items = json.loads(text).get("findings", [])
        except json.JSONDecodeError:
            log.warning("VT review returned unreadable output for %s", deck.title)
            continue
        for it in items:
            out.append(Finding(
                Severity.FIX if it["severity"] == "FIX" else Severity.CHECK, course, "vt",
                f"'{deck.title}' (slides {it['slides']}): {it['problem']}", fix=it["fix"],
                where=f"https://docs.google.com/presentation/d/{deck.file_id}/edit",
            ))
    return out
