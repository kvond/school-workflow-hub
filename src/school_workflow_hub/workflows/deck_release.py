"""Deterministic release checks for the final lesson deck.

This module does not call a model. It validates the actual exported PPTX text
against the teacher-reference contract and the Agenda Prompt-0 marker contract.
The same checks are safe to run repeatedly after visual edits.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import io
import re
import zipfile
import xml.etree.ElementTree as ET


_DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_SLIDE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
_MARKER = re.compile(
    r"\[\[(NOTES|DRAFT|BANK|OPTIONAL):([A-Za-z0-9_-]{1,40}):([^\]]{1,120})\]\]"
)
_INVENTORY = re.compile(r"\[\[MARKER-INVENTORY:([A-Za-z0-9_-]{1,40})\]\]")
_ASPECT_LABEL = re.compile(r"critical\s+aspect\s*:\s*([^\n\r]{1,180})", re.I)
_FIELD = re.compile(
    r"(NOTES_IDS|DRAFT_IDS|OPTIONAL_IDS|BANK_IDS)=([^=]*?)(?=\s+[A-Z_]+=|$)"
)


def fingerprint(text: str) -> str:
    """Stable SHA-256 for a current live prompt."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def pptx_slide_texts(raw: bytes) -> tuple[str, ...]:
    """Extract ordered native text from an exported Google Slides PPTX."""
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        raise ValueError("invalid_deck_bytes")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = []
            for name in archive.namelist():
                match = _SLIDE.match(name)
                if match:
                    names.append((int(match.group(1)), name))
            slides = []
            for _, name in sorted(names):
                root = ET.fromstring(archive.read(name))
                parts = [
                    node.text or ""
                    for node in root.iter("{%s}t" % _DRAWING_NS)
                ]
                slides.append("\n".join(part.strip() for part in parts if part.strip()))
    except (zipfile.BadZipFile, ET.ParseError, KeyError, OSError, ValueError):
        raise ValueError("invalid_deck_bytes") from None
    if not slides:
        raise ValueError("deck_has_no_slides")
    return tuple(slides)


def _lines(text: str) -> list[str]:
    return [" ".join(line.split()) for line in str(text or "").splitlines() if line.strip()]


def _aspect_key(value: str) -> str:
    value = re.sub(r"^\s*\d+\s*(?:[.)]|[-—–])?\s*", "", str(value or "")).strip()
    value = re.split(r"\s+[—–-]\s+|:\s+", value, maxsplit=1)[0]
    return " ".join(value.casefold().split())


_NUMBERED = re.compile(r"^\s*(\d+)\s*(?:[.)]|[-—–])\s*(.+)$")
_ASPECT_STOP = (
    "week question", "case study", "standard", "scored for completion",
    "not scored", "[[marker-inventory:",
)


def critical_aspects(text: str) -> list[dict]:
    """The CRITICAL ASPECT(S) list of a teacher reference, in listed order.

    Each entry is {"number": int | None, "name": str}, where name is the line
    after its number exactly as written. Biology numbers aspects within the
    lesson ("1. Name"); A&P numbers them within the unit ("3. Name — gloss")
    and uses the singular heading. Once numbered lines start, the first
    unnumbered line ends the list; an unnumbered list ends at the known
    headings that follow it.
    """
    # Names keep their exact spacing (saved feedback joins on them); only the
    # comparisons below use normalized text.
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    start = next(
        (i for i, line in enumerate(lines)
         if " ".join(line.split()).casefold() in {"critical aspect", "critical aspects"}),
        None,
    )
    if start is None:
        return []
    out, numbered = [], False
    for line in lines[start + 1:]:
        if any(" ".join(line.split()).casefold().startswith(word) for word in _ASPECT_STOP):
            break
        match = _NUMBERED.match(line)
        if match:
            numbered = True
            out.append({"number": int(match.group(1)), "name": match.group(2).strip()})
        elif numbered:
            break
        else:
            out.append({"number": None, "name": line.strip()})
    return out


def derive_teacher_contract(slides: tuple[str, ...]) -> dict:
    """Read the authoritative compact contract from a teacher-reference slide."""
    reference = ""
    for slide in slides:
        low = slide.casefold()
        if "teacher reference" in low and "critical aspect" in low:
            reference = slide
            break
    if not reference:
        return {}

    lines = _lines(reference)
    aspects = []
    for entry in critical_aspects(reference):
        key = _aspect_key(entry["name"])
        if key and key not in aspects:
            aspects.append(key)

    question = ""
    for i, line in enumerate(lines):
        if line.casefold() == "week question" and i + 1 < len(lines):
            question = lines[i + 1]
            break

    scored = ""
    for i, line in enumerate(lines):
        if line.casefold() == "scored for completion":
            out = []
            for item in lines[i + 1:]:
                if item.casefold() == "not scored" or item.startswith("[[MARKER-INVENTORY:"):
                    break
                out.append(item)
            scored = " ".join(out)
            break

    return {
        "critical_aspects": aspects,
        "week_question": question,
        "scored_for_completion": scored,
        "reference_text": reference,
    }


def _inventory_payload(all_text: str) -> tuple[str, str]:
    found = list(_INVENTORY.finditer(all_text))
    if len(found) != 1:
        return "", ""
    match = found[0]
    tail = all_text[match.end():match.end() + 4000]
    return match.group(1), tail


def _compact_ids(tail: str) -> dict[str, list[str]]:
    first = tail.splitlines()[0] if tail else ""
    result = {"NOTES_IDS": [], "DRAFT_IDS": [], "OPTIONAL_IDS": [], "BANK_IDS": []}
    for key, body in _FIELD.findall(first):
        result[key] = [item.strip() for item in body.split(",") if item.strip()]
    return result


def _counts(tail: str) -> dict[str, int]:
    first = tail.splitlines()[0] if tail else ""
    return {
        key: int(value)
        for key, value in re.findall(r"\b(NOTES|DRAFT|OPTIONAL|BANK_TERMS)=(\d+)\b", first)
    }


def _aspect_slides(slides: tuple[str, ...], aspect: str) -> list[str]:
    matched = []
    for slide in slides:
        labels = [_aspect_key(value) for value in _ASPECT_LABEL.findall(slide)]
        if aspect in labels:
            matched.append(slide)
    return matched


def _phase_flags(slides: list[str]) -> dict[str, bool]:
    text = "\n".join(slides)
    low = text.casefold()
    markers = [m.group(3).casefold() for m in _MARKER.finditer(text)]
    return {
        "question": ("?" in text and bool(re.search(r"\[\[NOTES:", text))),
        "contrast": any(
            cue in low for cue in (
                "compare", "same ", "different", "opposite", "case a", "case b",
                "contrast",
            )
        ) or any("contrast" in marker or "both" in marker for marker in markers),
        "rule": "finish this sentence as a rule" in low
                or any(marker.endswith("-rule") or "-rule-" in marker for marker in markers),
        "pattern_break": "pattern break" in low
                         or any("patternbreak" in marker for marker in markers),
        "mastery": "mastery" in low or "getting started" in low
                   or any("mastery" in marker for marker in markers),
    }


def validate_release(
    slides: tuple[str, ...],
    *,
    revision: str = "",
    prompt0: str = "",
    prompt2: str = "",
    prior_receipt: dict | None = None,
) -> dict:
    """Return one deterministic release result for the exact final deck revision."""
    if not isinstance(slides, tuple) or not slides:
        raise ValueError("invalid_deck_slides")
    all_text = "\n\n".join(slides)
    issues = []
    warnings = []
    if not str(revision or "").strip():
        issues.append("deck_revision_unavailable")

    contract = derive_teacher_contract(slides)
    aspects = list(contract.get("critical_aspects") or [])
    if not contract:
        issues.append("missing_teacher_reference_contract")
    elif not aspects:
        issues.append("teacher_reference_has_no_critical_aspects")
    if contract and not contract.get("week_question"):
        warnings.append("teacher_reference_has_no_week_question")

    inventory_cycle, tail = _inventory_payload(all_text)
    if not inventory_cycle:
        issues.append("marker_inventory_missing_or_duplicated")
    elif not _INVENTORY.search(slides[0]):
        issues.append("marker_inventory_not_on_first_slide")

    markers = [
        {"category": m.group(1), "cycle": m.group(2), "id": m.group(3).strip()}
        for m in _MARKER.finditer(all_text)
    ]
    marker_keys = [(m["category"], m["cycle"], m["id"]) for m in markers]
    duplicates = [key for key, count in Counter(marker_keys).items() if count > 1]
    if duplicates:
        issues.append("duplicate_feedback_marker")

    if inventory_cycle and any(m["cycle"] != inventory_cycle for m in markers):
        issues.append("feedback_marker_cycle_mismatch")

    counts = _counts(tail) if inventory_cycle else {}
    ids = _compact_ids(tail) if inventory_cycle else {}
    observed = Counter(m["category"] for m in markers)
    expected_counts = {
        "NOTES": counts.get("NOTES"),
        "DRAFT": counts.get("DRAFT"),
        "OPTIONAL": counts.get("OPTIONAL"),
        "BANK": counts.get("BANK_TERMS"),
    }
    for category, wanted in expected_counts.items():
        if wanted is None:
            if inventory_cycle:
                issues.append("marker_inventory_counts_unparsed")
                break
        elif observed.get(category, 0) != wanted:
            issues.append("marker_inventory_count_mismatch")
            break

    for field, category in (
        ("NOTES_IDS", "NOTES"), ("DRAFT_IDS", "DRAFT"),
        ("OPTIONAL_IDS", "OPTIONAL"), ("BANK_IDS", "BANK"),
    ):
        declared = ids.get(field) or []
        actual = [m["id"] for m in markers if m["category"] == category]
        if Counter(declared) != Counter(actual):
            issues.append("marker_inventory_id_mismatch")
            break

    notes = {m["id"] for m in markers if m["category"] == "NOTES"}
    drafts = {m["id"] for m in markers if m["category"] == "DRAFT"}
    if not drafts <= notes:
        issues.append("draft_without_matching_notes")

    if observed.get("BANK", 0) and "define these in your own words" not in all_text.casefold():
        issues.append("concept_bank_heading_missing")

    aspect_results = []
    for aspect in aspects:
        block = _aspect_slides(slides, aspect)
        if not block:
            issues.append("critical_aspect_block_missing:" + aspect)
            aspect_results.append({"aspect": aspect, "present": False, "phases": {}})
            continue
        phases = _phase_flags(block)
        missing = [name for name, present in phases.items() if not present]
        for phase in missing:
            issues.append("critical_aspect_phase_missing:%s:%s" % (aspect, phase))
        aspect_results.append({"aspect": aspect, "present": True, "phases": phases})

    scored = (contract.get("scored_for_completion") or "").casefold()
    if "what if" in scored and "what if?" not in all_text.casefold():
        issues.append("required_what_if_missing")

    prompt0_sha = fingerprint(prompt0) if prompt0 else ""
    prompt2_sha = fingerprint(prompt2) if prompt2 else ""
    if not prompt0 or not prompt2:
        issues.append("agenda_prompt_contract_unavailable")

    current_receipt = {
        "revision": str(revision or ""),
        "prompt0_sha256": prompt0_sha,
        "prompt2_sha256": prompt2_sha,
        "deck_sha256": fingerprint(all_text),
    }

    stale_status = ""
    if prior_receipt and not issues:
        if (
            prior_receipt.get("prompt0_sha256") != prompt0_sha
            or prior_receipt.get("prompt2_sha256") != prompt2_sha
        ):
            stale_status = "PROMPT_STALE"
        elif prior_receipt.get("deck_sha256") != current_receipt["deck_sha256"]:
            stale_status = "STALE"
        # A new Drive revision with identical contract-bearing text is a cheap
        # fast re-certification after visual-only edits. The returned receipt
        # binds READY to the new revision without another model call.

    status = "NOT_READY" if issues else (stale_status or "READY")
    return {
        "schema_version": "hub-deck-release/v1",
        "status": status,
        "revision": str(revision or ""),
        "critical_aspects": aspect_results,
        "inventory_cycle": inventory_cycle,
        "counts": dict(observed),
        "issues": issues,
        "warnings": warnings,
        "receipt": current_receipt,
        "external_writes": 0,
    }
