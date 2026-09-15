"""Prompt 2 reports from revision-bound, reviewed assessments; no model or IO.

Adapters must verify assessment/source provenance before constructing these
objects. This module checks references and exact quotations, not scientific
correctness or the truth of supplied reviewer attestations. Outputs remain
private proposals for teacher review, never filed or printed receipts.
"""
from __future__ import annotations

from __future__ import annotations

from dataclasses import dataclass
import re

from .completion_policy import ReadinessAssessment, assess_completion, check_readiness, _key

HEADINGS = (
    "Where my thinking started", "How my thinking changed",
    "Evidence from my final answer", "Current understanding", "My next step",
    "An idea worth chasing", "What I understand now that I didn't before",
)
LEVELS = ("Developing", "Almost there", "Proficient")


@dataclass(frozen=True)
class ResponseEvidence:
    reference: str
    submission_revision: str
    question_id: str
    category: str
    slide_title: str
    text: str
    timing: str = "unknown"  # earlier/current/unknown; verified by source adapter
    timing_reference: str | None = None
    printed_text: str | None = None


@dataclass(frozen=True)
class QuestionFeedback:
    question_id: str
    question_label: str  # Student-facing question, not a manual/standards code.
    current_reference: str
    quote: str
    level: str
    current_understanding: str  # Reviewed, student-voice prose grounded in quote.
    next_step: str  # Reviewed forward move, never repair already submitted work.
    unit_activity: str  # Specific named activity in this unit.
    idea: str
    assessment_reference: str
    assessment_revision: str
    reviewed: bool = False
    earlier_reference: str | None = None
    change_description: str | None = None
    misconception_key: str | None = None
    misconception_description: str | None = None
    misconception_reference: str | None = None
    misconception_quote: str | None = None


@dataclass(frozen=True)
class FeedbackCase:
    class_key: str
    assignment_key: str
    learner_key: str
    display_name: str  # Verified filename stem: first name + initial only.
    submission_revision: str
    prompt_version: str
    stage: str
    readiness: ReadinessAssessment
    responses: tuple  # Existing AssessedResponse instances for completion.
    evidence: tuple
    questions: tuple
    filename_identity_verified: bool = False


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 20000


def _identity(case):
    if not isinstance(case, FeedbackCase):
        raise ValueError("invalid_feedback_case")
    for value in (case.class_key, case.assignment_key, case.learner_key,
                  case.submission_revision, case.prompt_version):
        if not _key(value):
            raise ValueError("invalid_feedback_identity")
    # Unicode letters and common name punctuation; no full surnames/names inferred.
    if (not isinstance(case.display_name, str) or not re.fullmatch(
            r"[^\W\d_]+(?:[-'’][^\W\d_]+)* [^\W\d_]\.?", case.display_name)):
        raise ValueError("filename_first_name_and_initial_required")
    if case.stage not in {"initial", "final"}:
        raise ValueError("invalid_assessment_stage")


def build_student_feedback(case, *, batch_key):
    _identity(case)
    if not _key(batch_key):
        raise ValueError("invalid_batch_key")
    issues = []
    if case.filename_identity_verified is not True:
        issues.append("filename_identity_unverified")
    gate = check_readiness(case.readiness)
    if gate["status"] != "READY":
        issues.extend(gate["issues"])
    completion = assess_completion(case.readiness, case.responses, stage=case.stage)
    if completion["status"] != "proposed":
        issues.extend(completion["issues"])
    if not isinstance(case.evidence, tuple) or not isinstance(case.questions, tuple):
        raise ValueError("invalid_feedback_collection")
    if len(case.evidence) > 6000 or not 1 <= len(case.questions) <= 3000:
        raise ValueError("invalid_feedback_size")
    sources = {}
    for evidence in case.evidence:
        if (not isinstance(evidence, ResponseEvidence) or not _key(evidence.reference)
                or not _key(evidence.submission_revision) or not _text(evidence.question_id)
                or evidence.category not in {"NOTES", "DRAFT", "BANK"}
                or not _text(evidence.slide_title) or not isinstance(evidence.text, str)
                or len(evidence.text) > 20000 or evidence.timing not in {"earlier", "current", "unknown"}):
            raise ValueError("invalid_feedback_evidence")
        if evidence.reference in sources:
            raise ValueError("duplicate_feedback_evidence")
        sources[evidence.reference] = evidence
    paragraphs = [[] for _ in HEADINGS]
    understanding, misconceptions, comparisons, flags = [], [], [], []
    seen = set()
    for q in case.questions:
        if not isinstance(q, QuestionFeedback) or q.question_id in seen:
            raise ValueError("invalid_or_duplicate_feedback_question")
        seen.add(q.question_id)
        if gate["status"] != "READY":
            continue
        valid = (q.reviewed is True and _key(q.assessment_reference)
                 and q.assessment_revision == case.submission_revision
                 and q.level in LEVELS and all(_text(x) for x in (
                     q.question_label, q.quote, q.current_understanding,
                     q.next_step, q.unit_activity, q.idea)))
        current = sources.get(q.current_reference)
        if (not valid or current is None or current.question_id != q.question_id
                or current.submission_revision != case.submission_revision
                or current.timing == "earlier" or q.quote not in current.text
                or '\n' in q.quote or '\r' in q.quote):
            issues.append("unverified_question_assessment_or_quote")
            continue
        required = case.readiness.required_ids
        if q.question_id not in required.get(current.category, ()):
            issues.append("question_not_in_verified_inventory")
            continue
        # Prefer the revised answer when an assessed DRAFT exists for the same id.
        if (q.question_id in required.get("DRAFT", ()) and current.category != "DRAFT"
                and any(e.category == "DRAFT" and e.question_id == q.question_id
                        and e.submission_revision == case.submission_revision and e.text.strip()
                        for e in sources.values())):
            issues.append("available_revision_not_used")
            continue
        earlier = sources.get(q.earlier_reference) if q.earlier_reference else None
        if q.earlier_reference and (earlier is None or earlier.question_id != q.question_id
                or earlier.timing != "earlier" or not _key(earlier.timing_reference)
                or earlier.category not in {"NOTES", current.category}):
            issues.append("earlier_timing_unverified")
            continue
        basis, change = "unknown_timing", "not_determined"
        if earlier is None:
            start = "My earlier answer or its timing is unavailable."
            changed = q.current_understanding
        elif not earlier.text.strip():
            start = "No earlier answer was recorded."
            changed = "I completed this response later. " + q.current_understanding
            basis, change = "known_later_completion", "completed_later"
        else:
            start = earlier.text
            basis = "genuine_earlier_answer"
            if earlier.text == current.text:
                change = "unchanged"
                changed = "My answer stayed the same. " + q.current_understanding
            else:
                change = "revised"
                if not _text(q.change_description):
                    issues.append("documented_change_description_required")
                    continue
                changed = q.change_description
            if earlier.printed_text is not None and earlier.text == earlier.printed_text:
                flags.append("Teacher only: check the first answer for " + q.question_label
                             + "; it repeats the printed slide text word for word.")
        # A current NOTES first-answer copy is checkable even with unknown timing.
        if (earlier is None and current.category == "NOTES" and current.printed_text is not None
                and current.text == current.printed_text):
            flags.append("Teacher only: check the first answer for " + q.question_label
                         + "; it repeats the printed slide text word for word.")
        paragraphs[0].append(q.question_label + ": " + start)
        paragraphs[1].append(q.question_label + ": " + changed)
        label = {"DRAFT": "revised response", "NOTES": "current NOTES response",
                 "BANK": "current Concept Bank response"}[current.category]
        # Quotes occupy their own line, attribution stays separate.
        paragraphs[2].append(current.slide_title + " — " + q.question_label + " (" + label + ")\n" + q.quote)
        paragraphs[3].append(q.question_label + ": " + q.level)
        paragraphs[4].append(q.question_label + ": " + q.next_step)
        paragraphs[5].append(current.slide_title + ": " + q.idea + "\n" + q.unit_activity)
        understanding.append({"question_id": q.question_id, "level": q.level})
        comparisons.append({"question_id": q.question_id, "basis": basis, "change": change,
                            "evidence_references": [current.reference] + ([earlier.reference] if earlier else [])})
        if any(x is not None for x in (q.misconception_key, q.misconception_description,
                                       q.misconception_reference, q.misconception_quote)):
            m = sources.get(q.misconception_reference)
            if (m is None or m.reference not in {current.reference, q.earlier_reference}
                    or not _key(q.misconception_key) or not _text(q.misconception_description)
                    or not _text(q.misconception_quote) or q.misconception_quote not in m.text):
                issues.append("misconception_evidence_unverified")
            else:
                misconceptions.append({"idea_key": q.misconception_key,
                    "description": q.misconception_description, "quote": q.misconception_quote,
                    "source_text": m.text, "evidence_reference": m.reference,
                    "question_id": q.question_id,
                    "timing": "earlier" if m is earlier else "current"})
    expected_questions = ({qid for ids in case.readiness.required_ids.values() for qid in ids}
                          if gate["status"] == "READY" else seen)
    if seen != expected_questions:
        issues.append("feedback_question_coverage_incomplete")
    paragraphs[6] = ["Write this yourself and hand the page back.\n____________________________\n____________________________\n____________________________"]
    sections = [{"heading": h, "text": "\n\n".join(p)} for h, p in zip(HEADINGS, paragraphs)]
    issues = sorted(set(issues))
    status = "needs_review" if issues else "prepared_for_review"
    base = {k: getattr(case, k) for k in ("class_key", "assignment_key", "learner_key",
                                          "display_name", "submission_revision", "prompt_version", "stage")}
    # Failed assessments do not leak partial reports or apparently valid levels.
    report = dict(base, readiness=gate,
                  evidence_references=sorted(sources),
                  assessment_references=[q.assessment_reference for q in case.questions],
                  status=status, review_reasons=issues, teacher_only_flags=flags,
                  sections=[] if issues else sections, text="" if issues else "\n\n".join(
                      x["heading"] + "\n" + x["text"] for x in sections))
    timing = ("documented_revision" if any(c["change"] == "revised" for c in comparisons)
              else "known_later_completion" if any(c["change"] == "completed_later" for c in comparisons)
              else "genuine_earlier_answer" if any(c["basis"] == "genuine_earlier_answer" for c in comparisons)
              else "unknown")
    compiled = dict(base, schema_version="student-feedback-compiled/v1", batch_key=batch_key,
                    understanding=[] if issues else understanding,
                    completion=completion.get("counts") if completion["status"] == "proposed" else None,
                    timing_evidence="unknown" if issues else timing,
                    comparisons=[] if issues else comparisons,
                    misconceptions=[] if issues else misconceptions, review_reasons=issues)
    return {"report": report, "compiled_record": compiled, "external_writes": 0}


def compile_feedback_line(record):
    """Portable one-student line; JSON compiled_records preserve exact structure."""
    levels = ", ".join(x["question_id"] + "=" + x["level"] for x in record["understanding"]) or "unavailable"
    counts = record["completion"]
    completion = ", ".join(c + "=" + str(counts[c]["completed"]) + "/" + str(counts[c]["required"])
                           + (" (not yet due)" if not counts[c]["due"] else "")
                           for c in ("NOTES", "BANK", "DRAFT")) if counts else "unavailable"
    misconceptions = "; ".join(m["description"] + ': "' + m["quote"].replace("\n", " ")
                                 + '" (' + m["timing"] + ")" for m in record["misconceptions"])
    return (record["display_name"] + "; levels: " + levels + "; misconception: "
            + (misconceptions or "unavailable") + "; stage: " + record["stage"]
            + "; completion: " + completion + "; comparison: " + record["timing_evidence"]
            + ("; review: " + ", ".join(record["review_reasons"]) if record["review_reasons"] else ""))


def build_feedback_batch(cases, *, batch_key):
    if not isinstance(cases, (list, tuple)) or not 1 <= len(cases) <= 20:
        raise ValueError("run_parts_1_and_2_in_batches_of_twenty")
    for case in cases:
        _identity(case)
    if len({(c.class_key, c.assignment_key) for c in cases}) != 1:
        raise ValueError("feedback_batch_must_be_one_lesson")
    if len({c.learner_key for c in cases}) != len(cases):
        raise ValueError("duplicate_student_in_feedback_batch")
    results = [build_student_feedback(c, batch_key=batch_key) for c in cases]
    return {"schema_version": "student-feedback-batch/v1", "batch_key": batch_key,
            "status": "needs_review" if any(r["report"]["review_reasons"] for r in results) else "prepared_for_review",
            "reports": [r["report"] for r in results],
            "compiled_records": [r["compiled_record"] for r in results],
            "compiled_lines": [compile_feedback_line(r["compiled_record"]) for r in results],
            "execution_supported": False, "external_writes": 0}
