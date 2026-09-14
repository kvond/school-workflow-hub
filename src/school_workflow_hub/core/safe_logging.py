"""Structured local logging that avoids routine secret/content exposure."""

from __future__ import annotations

import json
import re
from typing import Any


SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "cookie",
    "authorization",
    "credential",
    "student_content",
    "submission_text",
)

PRIVATE_KEYS = frozenset({"student_name", "learner_name", "student_id", "learner_id",
                         "email", "email_address", "real_grade", "raw_text"})
PRIVATE_VALUE_PATTERN = re.compile(
    r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b|https?://(?:docs\.google\.com|drive\.google\.com|"
    r"[^/\s]*schoology\.com|[^/\s]*infinitecampus\.[^/\s]+)[^\s]*", re.I
)

FREE_TEXT_KEY_PARTS = (
    "error_message",
    "exception_message",
    "raw_response",
    "response_body",
)

SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?:\b(?:password|secret|token|cookie|authorization|credential)\b\s*[:=]\s*\S+|"
    r"\b(?:gh[pousr]_[A-Za-z0-9_]{12,}|AIza[0-9A-Za-z_-]{12,}|ya29\.[A-Za-z0-9._-]{12,}))",
    re.IGNORECASE,
)


def sanitize(value: Any, key: str = "") -> Any:
    key_lower = key.lower()
    if key_lower in PRIVATE_KEYS:
        return "[redacted private data]"
    if any(part in key_lower for part in SENSITIVE_KEY_PARTS):
        return "[redacted]"
    if any(part in key_lower for part in FREE_TEXT_KEY_PARTS):
        return "[redacted free text]"
    if isinstance(value, dict):
        return {str(item_key): sanitize(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        if PRIVATE_VALUE_PATTERN.search(value):
            return "[redacted private data]"
        if SENSITIVE_VALUE_PATTERN.search(value):
            return "[redacted sensitive-looking text]"
        if len(value) > 512:
            return value[:512] + "…[truncated]"
    return value


def safe_exception_message(error: BaseException) -> str:
    """Keep failure type while never persisting arbitrary exception content."""

    return "{}: [details withheld]".format(type(error).__name__)


def safe_json(event: dict[str, Any]) -> str:
    return json.dumps(sanitize(event), sort_keys=True, ensure_ascii=False)
