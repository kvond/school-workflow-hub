"""Write one lesson's class growth summary from saved assessment evidence.

Teacher decision, September 23, 2026: the "Write class summary" button sends
the lesson's saved answers for one class section (never the decks) with the
live Agenda Prompt 3 to the configured model. It returns a draft for the
teacher to read and edit; nothing is saved here. Saving goes through the
existing /api/assessment-save summary path. Counts, the understanding table,
the per-student table and the teacher checks on the summary page are built
from saved records in the browser, not by the model.
"""
import json
import re

from . import assessment_provider
from .hosted_agenda import ConnectionError, _identity_configuration, authorize

REQUEST_KEYS = {"class_key", "section_id", "assignment_key"}
WORDS_LIMIT = 700
HEADINGS = ("An idea worth chasing", "Recurring ideas in students' words",
            "A possible next instructional contrast", "Check these yourself")
SYSTEM = """You are writing a teacher-only class growth summary for one lesson and
one class section. You receive the teacher's own class summary instructions and
the lesson's saved assessment evidence as JSON: for each student, a first name
and initial, completion, and each saved answer with its critical aspect, rating,
answer type and the student's exact words.

Use only the supplied evidence. Quote students' exact words from it in quotation
marks with the student's first name and initial. Never invent a quote, a name,
a count or a timing. Give counts with denominators taken from the evidence.

Return plain text only, under these headings, each on its own line and in this
order: An idea worth chasing / Recurring ideas in students' words / A possible
next instructional contrast / Check these yourself. Omit a heading only when the
evidence gives nothing for it. No other headings, no tables, no markdown, no JSON.
"""


def _answer_type(stage):
    return {"NOTES": "first answer", "DRAFT": "revised answer", "BANK": "Concept Bank"}.get(stage, "answer")


def build_evidence(records, *, class_key, section_id, assignment_key):
    """The compact, lesson- and section-scoped evidence the model may read."""
    rows = [r for r in records.get("submissions", [])
            if r.get("class_key") == class_key and r.get("section_id") == section_id
            and r.get("assignment_key") == assignment_key and r.get("included_in_counts")
            and not r.get("discovery_only")]
    students, seen = [], set()
    for row in sorted(rows, key=lambda r: (r.get("display_name") or "", r.get("record_id") or "")):
        key = row.get("count_key") or row.get("record_id")
        if key in seen:
            continue
        seen.add(key)
        completion = {k: row.get(k) for k in ("notes_completed", "notes_required", "bank_completed",
                                              "bank_required", "drafts_completed", "drafts_required")
                      if row.get(k) not in (None, "")}
        answers = [{"question_id": a.get("question_id", ""), "answer_type": _answer_type(a.get("stage")),
                    "critical_aspect": a.get("critical_aspect", ""),
                    "rating": a.get("conceptual_level", ""), "slide": a.get("slide_number", ""),
                    "words": str(a.get("response") or "")[:WORDS_LIMIT]}
                   for a in row.get("answers", [])]
        students.append({"student": row.get("display_name") or "Name unavailable",
                         "checkpoint": row.get("assessment_stage", ""), "status": row.get("status", ""),
                         "completion": completion, "answers": answers})
    lesson = next((a.get("assignment_title") for a in records.get("assignments", [])
                   if a.get("assignment_key") == assignment_key), "")
    section = next((r.get("section_name") for r in rows if r.get("section_name")), "")
    return {"lesson": lesson, "section": section, "students": students,
            "student_count": len(students)}


def draft(assertion, request, config):
    from . import grading_records
    from .agenda_prompts import read_prompts
    audience, allowed = _identity_configuration(config)
    authorize(assertion, audience, allowed)
    if not assessment_provider.configured(config):
        raise ConnectionError("provider_not_configured")
    if (not isinstance(request, dict) or set(request) != REQUEST_KEYS
            or not all(isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9:_.-]{1,80}", v)
                       for v in request.values())):
        raise ValueError("invalid_summary_request")
    evidence = build_evidence(grading_records.read_records(assertion, config), **request)
    if not evidence["students"]:
        raise ValueError("no_saved_work_for_summary")
    try:
        prompt = read_prompts(assertion, config)["prompts"]["summary"]
    except ConnectionError:
        raise
    except Exception:
        raise ConnectionError("provider_prompts_unavailable") from None
    user = ("TEACHER CLASS SUMMARY INSTRUCTIONS:\n" + prompt
            + "\n\nSAVED LESSON EVIDENCE (JSON):\n" + json.dumps(evidence, ensure_ascii=False, sort_keys=True))
    text, usage = assessment_provider._call(config, SYSTEM, user)
    text = (text or "").strip()
    if not text or len(text) > 12000 or not any(h in text for h in HEADINGS):
        raise ConnectionError("summary_draft_invalid")
    return {"schema_version": "hub-class-summary-draft/v1", "status": "drafted", "draft": text,
            "students": evidence["student_count"],
            "coverage": f"{evidence['student_count']} students with saved, matched work in this lesson and section",
            "usage": usage, "external_writes": 0}
