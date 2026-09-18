"""Draft assessment packets from the configured Anthropic runtime provider.

The provider only judges and writes feedback: it receives the teacher's own
packaged prompts, the fresh server-side capture and the distributed manifest,
and returns per-item judgments with verbatim evidence excerpts plus the student
report. Identity, source binding and every completion score stay in Python —
a model can never alter who is assessed, which version, or the arithmetic.
Drafting never writes anything; the reviewed packet is saved through the same
/api/assessment-save path with full re-validation against a fresh capture.
The API key comes only from the runtime environment (Secret Manager binding),
is never logged, and no provider response body ever crosses a diagnostic.
"""
import json
import re

from . import assessment_save
from .hosted_agenda import (ConnectionError, _identity_configuration, authorize)
from ..workflows import assessment_records

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-5"
MAX_OUTPUT_TOKENS = 8000
REQUEST_KEYS = {"file_id", "class_key", "section_id", "section_name",
                "assignment_key", "stage", "stage_reference", "identity",
                "manifest", "ic_assignment_id", "comparison_status"}

SYSTEM = """You are the assessment assistant inside a teacher's private grading
workflow. You will receive the teacher's own assessment instructions, one
student's complete submission text (a plain-text export of their deck), and the
distributed marker inventory listing every required item.

Judge each required item and each attempted optional item from the student's
actual writing. Vocabulary is closed:
- judgment: "meaningful_attempt" (a nonblank attempt in the student's own
  words), "blank" (no response), or "printed_copy" (copied printed material,
  pasted AI output, or otherwise not the student's own words).
- conceptual_level: "Developing", "Almost there", "Proficient",
  "Evidence unavailable".
- explanation_level: "Names an idea", "Connects ideas", "Explains how or why",
  "Evidence unavailable".
Blank or printed_copy items use "Evidence unavailable" for both levels.

Rules that are not negotiable:
- response_excerpt MUST be copied character-for-character from the submission
  text (it is verified as a literal substring). Empty string for blank items.
- Never invent quotes. Never quote for a blank item.
- Copying or pasted-AI concerns go in evidence_note (teacher-only), never in
  student-facing feedback text.
- Cover every required item exactly once; include an optional item only if the
  student attempted it.

Return ONLY one JSON object, no prose, of this shape:
{"responses": [{"question_id": str, "category": "NOTES"|"BANK"|"DRAFT",
  "judgment": str, "response_excerpt": str, "evidence_note": str (<=400 chars,
  single line), "conceptual_level": str, "explanation_level": str,
  "conceptual_feedback": str (<=1200 chars), "prompt": "", "prompt_basis": "",
  "slide_number": "", "optional": bool}],
 "feedback": str (the student report, following the teacher's feedback prompt)}
"""


def configured(config):
    return bool(re.fullmatch(r"sk-ant-[A-Za-z0-9_-]{20,200}",
                             config.get("HUB_ANTHROPIC_API_KEY", "")))


def model_name(config):
    name = config.get("HUB_ASSESSMENT_MODEL", "") or DEFAULT_MODEL
    if not re.fullmatch(r"[a-z0-9.-]{5,60}", name):
        raise ConnectionError("provider_model_invalid")
    return name


def status(assertion, config):
    audience, allowed = _identity_configuration(config)
    authorize(assertion, audience, allowed)
    return {"schema_version": "hub-assessment-provider/v1",
            "configured": configured(config),
            "model": model_name(config) if configured(config) else None,
            "external_writes": 0}


def _teacher_prompts():
    try:
        from ..web import ASSET_ROOT
        data = json.loads((ASSET_ROOT / "teacher-prompts.json").read_text("utf-8"))
        return data["prompts"]["completion"], data["prompts"]["feedback"]
    except Exception:
        raise ConnectionError("provider_prompts_unavailable") from None


def _call(config, system, user):
    import requests as http
    body = {"model": model_name(config), "max_tokens": MAX_OUTPUT_TOKENS,
            "temperature": 0, "system": system,
            "messages": [{"role": "user", "content": user}]}
    try:
        with http.Session() as session:
            session.trust_env = False
            response = session.post(API_URL, json=body, timeout=(5, 240),
                                    allow_redirects=False, headers={
                                        "x-api-key": config["HUB_ANTHROPIC_API_KEY"],
                                        "anthropic-version": API_VERSION})
    except Exception:
        raise ConnectionError("provider_unavailable") from None
    if response.status_code == 401:
        raise ConnectionError("provider_auth_failed")
    if response.status_code == 429:
        raise ConnectionError("provider_rate_limited")
    if response.status_code == 400:
        # A low credit balance surfaces as a 400; never echo the body.
        raise ConnectionError("provider_request_refused")
    if response.status_code != 200:
        raise ConnectionError("provider_unavailable")
    try:
        data = response.json()
        text = "".join(block.get("text", "") for block in data.get("content", [])
                       if isinstance(block, dict))
        usage = data.get("usage", {}) if isinstance(data.get("usage"), dict) else {}
        return text, {"input_tokens": usage.get("input_tokens"),
                      "output_tokens": usage.get("output_tokens"),
                      "model": data.get("model")}
    except Exception:
        raise ConnectionError("provider_response_invalid") from None


def _extract_json(text):
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ConnectionError("provider_draft_invalid")
    try:
        result = json.loads(text[start:end + 1])
    except ValueError:
        raise ConnectionError("provider_draft_invalid") from None
    if (not isinstance(result, dict) or not isinstance(result.get("responses"), list)
            or not isinstance(result.get("feedback"), str)):
        raise ConnectionError("provider_draft_invalid")
    return result


def draft(assertion, request, config):
    """One drafted packet for review; no storage, no school writes."""
    audience, allowed = _identity_configuration(config)
    authorize(assertion, audience, allowed)
    if not configured(config):
        raise ConnectionError("provider_not_configured")
    if not isinstance(request, dict) or set(request) != REQUEST_KEYS:
        raise ValueError("invalid_provider_request")
    capture = assessment_save.capture(assertion, request["file_id"], config)
    completion_prompt, feedback_prompt = _teacher_prompts()
    manifest = request["manifest"]
    user = ("TEACHER COMPLETION INSTRUCTIONS:\n" + completion_prompt
            + "\n\nTEACHER FEEDBACK INSTRUCTIONS:\n" + feedback_prompt
            + "\n\nDISTRIBUTED MANIFEST (required items):\n"
            + json.dumps(manifest, sort_keys=True)
            + "\n\nASSESSMENT STAGE: " + str(request["stage"])
            + "\n\nSTUDENT SUBMISSION TEXT (verbatim export):\n" + capture["text"])
    text, usage = _call(config, SYSTEM, user)
    result = _extract_json(text)
    packet = {"schema_version": assessment_records.PACKET_VERSION,
              "class_key": request["class_key"], "section_id": request["section_id"],
              "section_name": request["section_name"],
              "assignment_key": request["assignment_key"], "stage": request["stage"],
              "stage_reference": request["stage_reference"],
              "identity": request["identity"],
              "source": {"file_id": capture["file"]["id"],
                         "url": "https://docs.google.com/presentation/d/"
                                + capture["file"]["id"] + "/edit",
                         "modified_at": capture["file"]["modified_at"],
                         "version": capture["file"]["version"],
                         "sha256": capture["sha256"]},
              "manifest": manifest, "responses": result["responses"],
              "feedback": result["feedback"],
              "comparison_status": request["comparison_status"],
              "ic_assignment_id": request["ic_assignment_id"]}
    # The same validator the save path uses; a draft that fails here is
    # reported, never repaired silently and never stored.
    assessment_records.validate_packet(packet, capture["text"])
    scores = assessment_records.completion(packet)
    return {"schema_version": "hub-assessment-provider/v1", "status": "draft_ready",
            "packet": packet, "scores": scores["scores"], "counts": scores["counts"],
            "record_id": assessment_records.record_id(packet),
            "source": {"file_id": capture["file"]["id"],
                       "modified_at": capture["file"]["modified_at"],
                       "version": capture["file"]["version"],
                       "sha256": capture["sha256"]},
            "usage": usage, "external_writes": 0}
