"""Draft assessment packets from the configured runtime AI provider.

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

from . import assessment_save, assessment_target
from .hosted_agenda import (ConnectionError, _identity_configuration, authorize)
from ..workflows import assessment_records

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
OPENAI_API_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODELS = {"anthropic": "claude-sonnet-5", "openai": "gpt-6-luna"}
MAX_OUTPUT_TOKENS = 8000
REQUEST_KEYS = {"file_id", "class_key", "stage", "stage_reference",
                "assignment_key", "section_id", "section_name", "identity"}

SYSTEM = """You are the assessment assistant inside a teacher's private grading
workflow. You will receive the teacher's own assessment instructions, one
student's complete submission text (a plain-text export of their deck), and the
distributed marker inventory listing every required item.

Judge each required item and each attempted optional item from the student's
actual writing. Vocabulary is closed:
- judgment: "meaningful_attempt" (a nonblank attempt in the student's own
  words), "blank" (no response), or "printed_copy" (copied printed material,
  pasted AI output, or otherwise not the student's own words).
- conceptual_level: how well this answer explains or uses the lesson's
  critical aspect, on one scale that already includes how the mechanism is
  explained:
  "Beginning" (names an idea; the relationship is missing or inaccurate),
  "Developing" (connects some ideas; the mechanism is partly explained),
  "Proficient" (explains the relationship using a scientifically sound
  mechanism), "Extending" (uses the mechanism to explain a new case and its
  limits), or "Evidence unavailable". A clear explanation of an inaccurate
  relationship is "Beginning". Never label a blank answer "Beginning".
- explanation_level: "" (do not rate explanation separately).
- rubric_version: "critical-aspect-4/v1" for every response, including blanks.
- critical_aspect: the name of the critical aspect this item answers, copied
  exactly from the deck's own label (a response slide titled "Your answer -
  Critical Aspect N · <name>", or "Critical aspect: <name>" on the aspect's
  slides). Use "What if" for the What-if response. Use "" when the item is not
  tied to a named aspect, such as a Concept Bank term. Never invent or rename
  an aspect.
Blank or printed_copy items use "Evidence unavailable" for conceptual_level
and "" for explanation_level.

Rules that are not negotiable:
- response_excerpt MUST be copied character-for-character from the submission
  text (it is verified as a literal substring). Empty string for blank items.
- Never invent quotes. Never quote for a blank item.
- Copying or pasted-AI concerns go in evidence_note (teacher-only), never in
  student-facing feedback text.
- Cover every required item exactly once; include an optional item only if the
  student attempted it.

Initial and revised answers:
- Where a question has both an Initial and a Revision box, they are separate
  labeled fields for the same question id: the NOTES item is the initial box
  and the DRAFT item is the revised box. Pair them by the question id and the
  labels near the boxes (for example "01 / Your First Answer", "02 / Your
  Revised Answer", "Your revised answer"), never by the order text happens to
  appear in the export.
- Bind every quotation to the correct labeled field. A quote that exists
  somewhere else in the document is not evidence for this field.
- A blank Initial box never cancels credit for a meaningful Revision, and a
  written Revision never back-fills a blank Initial.
- Compare the two answers only as written; never invent timing or improvement.

Judgment evidence standards:
- Never infer AI authorship or copying from writing style, vocabulary or
  polish. printed_copy requires concrete evidence in the text itself: a
  near-verbatim match of provided course material, a citation artifact, or
  pasted coaching/template output sitting where the student's own words
  belong.
- Ambiguous or unreadable evidence is the "review" judgment, never a guessed
  blank or meaningful_attempt.

Return ONLY one JSON object, no prose, of this shape:
{"responses": [{"question_id": str, "category": "NOTES"|"BANK"|"DRAFT",
  "judgment": str, "response_excerpt": str, "evidence_note": str (<=400 chars,
  single line), "conceptual_level": str, "explanation_level": str,
  "critical_aspect": str (<=200 chars, single line),
  "rubric_version": "critical-aspect-4/v1",
  "conceptual_feedback": str (<=1200 chars), "prompt": "", "prompt_basis": "",
  "slide_number": "", "optional": bool}],
 "feedback": str (the student report, following the teacher's feedback prompt:
  written to the student as "you", quoting the student's exact words, never
  written as if the student were speaking)}
"""


def provider_name(config):
    """Runtime provider; omitted keeps the currently deployed Anthropic path."""
    name = str(config.get("HUB_AI_PROVIDER", "") or "anthropic").strip().lower()
    if name not in DEFAULT_MODELS:
        raise ConnectionError("provider_invalid")
    return name


def _key(config):
    # Console-pasted secrets often carry a trailing newline; API keys never
    # contain whitespace, so stripping cannot corrupt a real one.
    provider = provider_name(config)
    variable = "HUB_OPENAI_API_KEY" if provider == "openai" else "HUB_ANTHROPIC_API_KEY"
    return (config.get(variable) or "").strip()


def configured(config):
    key = _key(config)
    if provider_name(config) == "openai":
        # OpenAI project/service-account keys are intentionally validated only
        # for a secret-key prefix and bounded ASCII shape. Exact prefixes may
        # evolve; authentication is still verified by the provider.
        return bool(re.fullmatch(r"sk-[A-Za-z0-9_-]{20,240}", key))
    return bool(re.fullmatch(r"sk-ant-[A-Za-z0-9_-]{20,200}", key))


def model_name(config):
    provider = provider_name(config)
    name = config.get("HUB_ASSESSMENT_MODEL", "") or DEFAULT_MODELS[provider]
    if not re.fullmatch(r"[a-z0-9.-]{3,80}", name):
        raise ConnectionError("provider_model_invalid")
    return name


def status(assertion, config):
    audience, allowed = _identity_configuration(config)
    authorize(assertion, audience, allowed)
    provider = provider_name(config)
    ready = configured(config)
    # A fixed dependency code, never the key or its length: absent means the
    # secret binding did not reach the runtime; malformed means it did but the
    # stored value is not an API key.
    dependency = None if ready else (
        "runtime_key_missing" if not _key(config) else "runtime_key_malformed")
    return {"schema_version": "hub-assessment-provider/v1",
            "configured": ready, "dependency": dependency,
            "provider": provider,
            "model": model_name(config) if ready else None,
            "external_writes": 0}


def _teacher_prompts(assertion, config):
    """The CURRENT authoritative Agenda instructions, read live at draft time.

    The live read also compares against the packaged copy; a mismatch is fine
    (the live text wins and its hashes travel with the draft), but a failed
    live read refuses the draft rather than silently grading on a stale copy.
    """
    from .agenda_prompts import read_prompts
    try:
        live = read_prompts(assertion, config)
    except ConnectionError:
        raise
    except Exception:
        raise ConnectionError("provider_prompts_unavailable") from None
    return (live["prompts"]["completion"], live["prompts"]["feedback"],
            {"version_label": live.get("version_label", ""),
             "sha256": live.get("sha256", {}),
             "bundled_matches": live.get("bundled_matches", {})})


OPENAI_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "responses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question_id": {"type": "string"},
                    "category": {"type": "string", "enum": ["NOTES", "BANK", "DRAFT"]},
                    "judgment": {"type": "string", "enum": [
                        "meaningful_attempt", "blank", "printed_copy", "review"
                    ]},
                    "response_excerpt": {"type": "string"},
                    "evidence_note": {"type": "string", "maxLength": 400},
                    "conceptual_level": {"type": "string", "enum": [
                        "Beginning", "Developing", "Proficient", "Extending",
                        "Evidence unavailable"
                    ]},
                    "explanation_level": {"type": "string"},
                    "critical_aspect": {"type": "string", "maxLength": 200},
                    "rubric_version": {"type": "string", "enum": ["critical-aspect-4/v1"]},
                    "conceptual_feedback": {"type": "string", "maxLength": 1200},
                    "prompt": {"type": "string"},
                    "prompt_basis": {"type": "string"},
                    "slide_number": {"type": "string"},
                    "optional": {"type": "boolean"},
                },
                "required": [
                    "question_id", "category", "judgment", "response_excerpt",
                    "evidence_note", "conceptual_level", "explanation_level",
                    "critical_aspect", "rubric_version", "conceptual_feedback",
                    "prompt", "prompt_basis", "slide_number", "optional",
                ],
                "additionalProperties": False,
            },
        },
        "feedback": {"type": "string"},
    },
    "required": ["responses", "feedback"],
    "additionalProperties": False,
}


def _request(http, url, *, body, headers):
    try:
        with http.Session() as session:
            session.trust_env = False
            return session.post(url, json=body, timeout=(5, 240),
                                allow_redirects=False, headers=headers)
    except Exception:
        raise ConnectionError("provider_unavailable") from None


def _provider_error(response):
    if response.status_code == 401:
        raise ConnectionError("provider_auth_failed")
    if response.status_code == 429:
        raise ConnectionError("provider_rate_limited")
    if response.status_code in (400, 402, 403):
        # Billing, account limits and rejected requests are intentionally
        # collapsed to a fixed code; never echo a provider response body.
        raise ConnectionError("provider_request_refused")
    if response.status_code != 200:
        raise ConnectionError("provider_unavailable")


def _call_anthropic(config, system, user):
    import requests as http
    body = {"model": model_name(config), "max_tokens": MAX_OUTPUT_TOKENS,
            "temperature": 0, "system": system,
            "messages": [{"role": "user", "content": user}]}
    response = _request(
        http, ANTHROPIC_API_URL, body=body,
        headers={"x-api-key": _key(config), "anthropic-version": ANTHROPIC_API_VERSION},
    )
    _provider_error(response)
    try:
        data = response.json()
        text = "".join(block.get("text", "") for block in data.get("content", [])
                       if isinstance(block, dict))
        usage = data.get("usage", {}) if isinstance(data.get("usage"), dict) else {}
        return text, {"input_tokens": usage.get("input_tokens"),
                      "cached_input_tokens": usage.get("cache_read_input_tokens"),
                      "cache_write_tokens": usage.get("cache_creation_input_tokens"),
                      "output_tokens": usage.get("output_tokens"),
                      "provider": "anthropic", "model": data.get("model")}
    except Exception:
        raise ConnectionError("provider_response_invalid") from None


def _call_openai(config, system, user, *, schema=None):
    import requests as http
    # Stable teacher instructions precede per-student evidence and get an
    # explicit cache breakpoint. Responses storage is disabled for school work.
    body = {
        "model": model_name(config),
        "store": False,
        "input": [
            {"role": "developer", "content": [{
                "type": "input_text", "text": system,
                "prompt_cache_breakpoint": {"mode": "explicit"},
            }]},
            {"role": "user", "content": user},
        ],
        "prompt_cache_options": {"mode": "explicit", "ttl": "30m"},
        "text": {"format": {
            "type": "json_schema", "name": "assessment_packet",
            "strict": True, "schema": schema or OPENAI_RESPONSE_SCHEMA,
        }},
    }
    response = _request(
        http, OPENAI_API_URL, body=body,
        headers={"Authorization": "Bearer " + _key(config),
                 "Content-Type": "application/json"},
    )
    _provider_error(response)
    try:
        data = response.json()
        parts = []
        for item in data.get("output", []) or []:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for block in item.get("content", []) or []:
                if isinstance(block, dict) and block.get("type") == "output_text":
                    parts.append(block.get("text", ""))
        text = "".join(parts)
        usage = data.get("usage", {}) if isinstance(data.get("usage"), dict) else {}
        details = (usage.get("input_tokens_details", {})
                   if isinstance(usage.get("input_tokens_details"), dict) else {})
        return text, {
            "input_tokens": usage.get("input_tokens"),
            "cached_input_tokens": details.get("cached_tokens"),
            "cache_write_tokens": usage.get("cache_write_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "provider": "openai", "model": data.get("model"),
        }
    except Exception:
        raise ConnectionError("provider_response_invalid") from None


def _call(config, system, user, *, schema=None):
    if provider_name(config) == "openai":
        return _call_openai(config, system, user, schema=schema)
    return _call_anthropic(config, system, user)


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


def _filled_identity(request_identity, prior, *, class_key, section_id):
    """Teacher-entered fields win; blanks fill from the file's newest workbook
    row. A teacher-confirmed name with no IC number gets a derived roster
    learner key here on the server — the teacher never types seeds or keys,
    and the basis records that the key is a bookkeeping identity, not proof."""
    import hashlib
    identity = {key: str(request_identity.get(key) or "")
                for key in ("student_id", "student_id_kind", "learner_key",
                            "display_name", "identity_basis")}
    if prior:
        for key in identity:
            identity[key] = identity[key] or str(prior.get(key) or "")
    if not identity["student_id_kind"]:
        identity["student_id_kind"] = ("local_student_number"
                                       if identity["student_id"] else "roster_match")
    if (not identity["student_id"] and not identity["learner_key"]
            and identity["display_name"]):
        seed = ("roster|" + class_key + "|" + section_id + "|"
                + " ".join(identity["display_name"].lower().split()))
        identity["learner_key"] = "roster:" + hashlib.sha256(
            seed.encode("utf-8")).hexdigest()
        note = ("Teacher-confirmed name match recorded from the dashboard; "
                "learner key derived from class, section and confirmed name — "
                "a bookkeeping identity, not an IC-verified number.")
        identity["identity_basis"] = ((identity["identity_basis"] + " ")
                                      if identity["identity_basis"] else "") + note
    return identity


def _comparison(prior, sha):
    if prior is None:
        return "First recorded assessment for this student; no earlier snapshot to compare."
    sameness = ("same source version" if prior.get("source_sha256") == sha
                else "different source version")
    return ("Compared with " + (prior.get("checked_date") or "earlier") + " "
            + (prior.get("assessment_stage") or "recorded") + " assessment (exact "
            + (prior.get("content_score_exact") or "n/a") + "); " + sameness + ".")[:300]


DRAFT_TARGET_FIELDS = ("class_key", "assignment_key", "section_id", "stage")


def _draft_target(request):
    return {key: request[key] for key in DRAFT_TARGET_FIELDS}


def _draft_doc_id(file_id, sha, target=None):
    from ..core.cloud_state import key
    content = {"file": file_id, "sha": sha}
    if target is not None:
        content["target"] = _draft_target(target)
    return key("draftpkt", content)


def _stored_draft(store, file_id, sha, target=None):
    ids = [_draft_doc_id(file_id, sha, target)]
    if target is not None:
        # Read old immutable packets without renaming or overwriting history.
        ids.append(_draft_doc_id(file_id, sha))
    documents = store.port.read(ids).documents
    for doc_id in ids:
        doc = documents[doc_id]
        if doc and (target is None or all(doc.value.get("packet", {}).get(key) == target[key]
                                         for key in DRAFT_TARGET_FIELDS)):
            return dict(doc.value)
    return None


def _store_draft(store, file_id, sha, payload, target=None):
    """Best-effort CAS persist so a paid draft survives page refreshes; a
    concurrent identical draft keeps whichever landed first."""
    from ..core.cloud_state import Conflict
    doc_id = _draft_doc_id(file_id, sha, target)
    try:
        store.port.commit({doc_id: (payload, None)})
    except Conflict:
        pass
    except Exception:
        pass


def _draft_reply(stored, *, cached):
    packet = stored["packet"]
    scores = assessment_records.completion(packet)
    return {"schema_version": "hub-assessment-provider/v1", "status": "draft_ready",
            "cached": cached, "packet": packet, "scores": scores["scores"],
            "counts": scores["counts"],
            "record_id": assessment_records.record_id(packet),
            "source": dict(packet["source"]),
            "prompts": stored.get("prompts") or {},
            "usage": stored.get("usage") or {}, "drafted_at": stored.get("drafted_at"),
            "external_writes": 0}


def stored_draft_for(assertion, file_id, config, *, target=None):
    """Recover the persisted draft for the file's CURRENT content, if any."""
    from .firestore_state import configured_store
    from ..core.cloud_state import CloudState
    if target is not None:
        if set(target) != set(DRAFT_TARGET_FIELDS) or target.get("stage") not in assessment_records.STAGES:
            raise ValueError("invalid_provider_request")
        assessment_target.read_target(assertion, {**target, "file_id": file_id, "identity": {}}, config)
    capture = assessment_save.capture(assertion, file_id, config)
    store = CloudState(configured_store(config))
    stored = _stored_draft(store, file_id, capture["sha256"], target)
    if stored is None:
        return {"schema_version": "hub-assessment-provider/v1", "status": "no_draft",
                "source": {"file_id": file_id, "sha256": capture["sha256"],
                           "modified_at": capture["file"]["modified_at"]},
                "external_writes": 0}
    # Legacy reads still need the current registry/section and saved identity;
    # a historical cache entry alone cannot authorize its old target.
    assessment_target.read_target(assertion, stored["packet"], config)
    return _draft_reply(stored, cached=True)


def draft(assertion, request, config):
    """One drafted packet for review; nothing is graded, posted or saved.

    Billing guards: an assessed row for this exact content and checkpoint returns
    already_assessed with no model call; a persisted draft for the same content,
    assignment, section and checkpoint is reused. A scoped claim stops concurrent
    duplicate calls for that assessment target.
    """
    from .firestore_state import configured_store
    from ..core.cloud_state import Busy, CloudState
    audience, allowed = _identity_configuration(config)
    authorize(assertion, audience, allowed)
    if not configured(config):
        raise ConnectionError("provider_not_configured")
    if (not isinstance(request, dict) or set(request) != REQUEST_KEYS
            or not isinstance(request.get("identity"), dict)):
        raise ValueError("invalid_provider_request")
    if request.get("stage") not in assessment_records.STAGES:
        raise ValueError("invalid_assessment_stage")
    prior = assessment_target.read_target(assertion, request, config)
    identity = _filled_identity(request["identity"], prior,
                                class_key=request["class_key"],
                                section_id=request["section_id"])
    if prior:
        assessment_target.check_identity(identity, prior)
    capture = assessment_save.capture(assertion, request["file_id"], config)
    sha = capture["sha256"]
    # The distributed requirement comes from the deck's own manifest header,
    # never from the browser; a headerless deck stays a manual case.
    manifest = assessment_records.derive_manifest(capture["text"])
    store = CloudState(configured_store(config))
    stored = _stored_draft(store, request["file_id"], sha, request)
    if stored is not None:
        assessment_target.check_cached(stored["packet"], request, identity)
        return _draft_reply(stored, cached=True)
    if (prior and prior.get("status") == "assessed"
            and prior.get("source_sha256") == sha
            and prior.get("assessment_stage") == request["stage"]):
        return {"schema_version": "hub-assessment-provider/v1",
                "status": "already_assessed", "record_id": prior.get("record_id"),
                "checked_at": prior.get("checked_at"),
                "source": {"file_id": request["file_id"], "sha256": sha,
                           "modified_at": capture["file"]["modified_at"]},
                "external_writes": 0}
    completion_prompt, feedback_prompt, prompt_meta = _teacher_prompts(assertion, config)
    from ..core.runs import content_hash
    input_hash = content_hash({"file": request["file_id"], "sha": sha,
                               "target": _draft_target(request)})
    task = store.create_task({"workflow": "assessment-draft",
                              "class_key": request["class_key"],
                              "assignment_key": input_hash[:40]})
    claim = store.claim(task, owner="draft", request_id="draft-" + input_hash[:32],
                        input_hash=input_hash,
                        ttl_seconds=240)
    if claim.completed:
        stored = _stored_draft(store, request["file_id"], sha, request)
        if stored is not None:
            assessment_target.check_cached(stored["packet"], request, identity)
            return _draft_reply(stored, cached=True)
    # Keep the long, stable teacher instructions before per-student evidence
    # so provider prompt caching can reuse them across a class batch.
    stable_instructions = (
        SYSTEM
        + "\n\nTEACHER COMPLETION INSTRUCTIONS:\n" + completion_prompt
        + "\n\nTEACHER FEEDBACK INSTRUCTIONS:\n" + feedback_prompt
    )
    user = ("DISTRIBUTED MANIFEST (required items):\n"
            + json.dumps(manifest, sort_keys=True)
            + "\n\nASSESSMENT STAGE: " + str(request["stage"])
            + "\n\nSTUDENT SUBMISSION TEXT (verbatim export):\n" + capture["text"])
    try:
        text, usage = _call(config, stable_instructions, user)
        result = _extract_json(text)
        packet = {"schema_version": assessment_records.PACKET_VERSION,
                  "class_key": request["class_key"], "section_id": request["section_id"],
                  "section_name": request["section_name"],
                  "assignment_key": request["assignment_key"], "stage": request["stage"],
                  "stage_reference": request["stage_reference"],
                  "identity": identity,
                  "source": {"file_id": capture["file"]["id"],
                             "url": "https://docs.google.com/presentation/d/"
                                    + capture["file"]["id"] + "/edit",
                             "modified_at": capture["file"]["modified_at"],
                             "version": capture["file"]["version"],
                             "sha256": sha},
                  "manifest": manifest, "responses": result["responses"],
                  "feedback": result["feedback"],
                  "comparison_status": _comparison(prior, sha),
                  "ic_assignment_id": str((prior or {}).get("ic_assignment_id") or "")}
        # The same validator the save path uses; a draft that fails here is
        # reported, never repaired silently and never stored.
        assessment_records.validate_packet(packet, capture["text"])
        stored = {"schema_version": "hub-assessment-draft/v1", "packet": packet,
                  "usage": usage, "prompts": prompt_meta,
                  "drafted_at": assessment_save._now()}
        _store_draft(store, request["file_id"], sha, stored, request)
        store.finish(claim, records={"receipt": {"file": request["file_id"],
                                                 "sha256": sha,
                                                 "drafted_at": stored["drafted_at"]}})
        return _draft_reply(stored, cached=False)
    except Busy:
        raise
    except Exception:
        try:
            store.release_for_review(claim, "draft_failed")
        except Exception:
            pass
        raise
