"""Shared bounded marker vocabulary; labels are identities, never paths."""
import re


PLAIN_ID = r"[A-Za-z0-9 _-]{1,80}"
_ATOM = r"[A-Za-z0-9][A-Za-z0-9 _-]*"
_SLASH_LABEL = _ATOM + r"(?: / " + _ATOM + r")+"
# One balanced nonempty suffix: an explanatory phrase or a comma-separated list.
# Periods occur only in a positive 1–999 numbered label prefix followed by a space.
_MEMBER = r"[A-Za-z0-9](?:[A-Za-z0-9 _-]*[A-Za-z0-9_-])?"
_PAREN_LABEL = _MEMBER + r" \(" + _MEMBER + r"(?:, " + _MEMBER + r")*\)"
_NUMBERED_LABEL = rf"[1-9][0-9]{{0,2}}\. (?:{_MEMBER}|{_SLASH_LABEL}|{_PAREN_LABEL})"
BANK_ID = rf"(?:{PLAIN_ID}|{_SLASH_LABEL}|{_PAREN_LABEL}|{_NUMBERED_LABEL})"


def marker_pattern(*, capture_cycle=True):
    """Keep the historic three completion / two assessment capture groups."""
    cycle = r"([A-Za-z0-9_-]{1,40})" if capture_cycle else r"[A-Za-z0-9_-]{1,40}"
    return re.compile(
        rf"\[\[(?=BANK:|(?:NOTES|DRAFT|OPTIONAL):[A-Za-z0-9_-]{{1,40}}:{PLAIN_ID}\]\])"
        rf"(NOTES|BANK|DRAFT|OPTIONAL):{cycle}:"
        rf"(?=[A-Za-z0-9 ._/(),-]{{1,80}}\]\])({BANK_ID})\]\]")


MARKER = marker_pattern()
_BANK_START = re.compile(r"\[\[BANK\b")


def malformed_bank_marker(text):
    """An unparseable BANK field must not disappear into a neighboring term.

    Other categories retain their existing repair/coverage behavior, including
    historical in-place NOTES repairs that preserve malformed original text.
    """
    return any(MARKER.match(text, start.start()) is None for start in _BANK_START.finditer(text))


def question_id(value, *, category=None):
    return (isinstance(value, str) and value == value.strip() and len(value) <= 80
            and re.fullmatch(BANK_ID if category == "BANK" else PLAIN_ID, value) is not None)


def split_ids(value, *, category, multiline=False):
    """Split inventory separators, retaining commas inside a BANK suffix.

    Leave malformed parentheses intact for the caller's ordinary ID/census
    validation. Never repair them or silently discard their fragments.
    """
    if category != "BANK":
        return re.split(r"[,\n]" if multiline else ",", value)
    parts, start, depth = [], 0, 0
    for index, char in enumerate(value):
        if char == "(":
            if depth:
                return [value]
            depth = 1
        elif char == ")":
            if not depth:
                return [value]
            depth = 0
        elif char in "\r\n" and depth:
            return [value]
        elif not depth and (char == "," or (multiline and char == "\n")):
            parts.append(value[start:index])
            start = index + 1
    return [value] if depth else parts + [value[start:]]
