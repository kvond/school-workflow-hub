"""Explicit read-only submission bindings. Published IDs are not API IDs."""
import json
import re
from urllib.parse import parse_qs, urlsplit

from .hosted_agenda import ConnectionError

ID = r"[A-Za-z0-9_-]{20,128}"
QUESTION_ID = r"[A-Za-z0-9_-]{1,128}"


def parse_form(value):
    """Return kind/id plus prefill answers; never guess an editor ID."""
    if not isinstance(value, str) or len(value) > 8000:
        raise ConnectionError("submission_form_link_invalid")
    if re.fullmatch(ID, value):
        return {"kind": "published" if value.startswith("1FAIpQL") else "api",
                "id": value, "prefill": {}}
    try:
        url = urlsplit(value)
        if (url.scheme != "https" or url.netloc != "docs.google.com"
                or re.search(r"[\s\x00-\x1f]", value)):
            raise ValueError()
        match = re.fullmatch(r"/forms/(?:u/\d+/)?d/(e/)?(" + ID + r")/(edit|viewform)/?", url.path)
        if not match or (match[1] and match[3] != "viewform"):
            raise ValueError()
        query = parse_qs(url.query, keep_blank_values=True, max_num_fields=100)
        return {"kind": "published" if match[1] else "api", "id": match[2],
                "prefill": {k: v for k, v in query.items() if k.startswith("entry.")}}
    except ValueError:
        raise ConnectionError("submission_form_link_invalid") from None


def form_settings(config):
    try:
        raw = config.get("HUB_SUBMISSION_FORMS", "{}")
        if not isinstance(raw, str) or len(raw) > 64000:
            raise ValueError()
        result = json.loads(raw)
        if not isinstance(result, dict) or len(result) > 100:
            raise ValueError()
        return result
    except (ValueError, TypeError):
        raise ConnectionError("submission_form_mapping_required") from None


def resolve_source(lesson, config):
    """A Form always wins over a folder; a broken Form never broadens the read."""
    form = lesson.get("turnin_form")
    folder = lesson.get("turnin_folder") or ""
    if not form and folder.startswith("https://docs.google.com/forms/"):
        form = folder
    if not form:
        match = re.fullmatch(r"https://drive.google.com/drive/folders/(" + ID + r")/?(?:\?[^#]*)?", folder)
        if not match or not lesson.get("assignment_label"):
            raise ConnectionError("submission_lesson_mapping_required")
        return match[1]  # Keep the legacy folder stream's identity unchanged.
    ref = parse_form(form)
    binding = form_settings(config).get(ref["id"])
    if not isinstance(binding, dict):
        raise ConnectionError("submission_form_mapping_required")
    form_id = binding.get("form_id", ref["id"] if ref["kind"] == "api" else "")
    question = binding.get("assignment_question_id", "")
    entry = str(binding.get("assignment_entry_id", ""))
    label = lesson.get("assignment_label")
    if (not isinstance(form_id, str) or not re.fullmatch(ID, form_id)
            or form_id.startswith("1FAIpQL")
            or not isinstance(question, str) or not re.fullmatch(QUESTION_ID, question)
            or not isinstance(label, str) or not 0 < len(label) <= 200):
        raise ConnectionError("submission_form_mapping_required")
    if ref["kind"] == "api" and form_id != ref["id"]:
        raise ConnectionError("submission_form_mapping_conflict")
    # A prefilled entry number is not assumed to be a Forms API questionId.
    if ref["prefill"] and (not re.fullmatch(r"\d+", entry)
                           or ref["prefill"].get("entry." + entry) != [label]):
        raise ConnectionError("submission_form_assignment_mismatch")
    identities = binding.get("identity_questions", {})
    if (not isinstance(identities, dict) or set(identities) - {"name", "email", "period"}
            or any(not isinstance(v, str) or not re.fullmatch(QUESTION_ID, v)
                   for v in identities.values())):
        raise ConnectionError("submission_form_mapping_required")
    return {"kind": "form", "id": form_id, "assignment": label,
            "assignment_question_id": question,
            "published_id": ref["id"] if ref["kind"] == "published" else None,
            "identity_questions": identities}
