"""The student-facing understanding scale for critical aspects.

Teacher decision, September 23, 2026: each critical aspect named in a lesson is
rated on one four-level scale that includes how well the mechanism is
explained. It replaces the earlier three-level understanding scale and the
separate explanation descriptors for new assessments. Earlier records keep the
levels they were saved with; they are read, never converted, and never share a
graph line with the new scale.
"""
import re

SCALE_VERSION = "critical-aspect-4/v1"
LEVELS = ("Beginning", "Developing", "Proficient", "Extending")
LEGACY_LEVELS = ("Developing", "Almost there", "Proficient")
UNAVAILABLE = "Evidence unavailable"
# Read-side vocabulary: new levels first, then the legacy-only level.
ALL_LEVELS = tuple(dict.fromkeys(LEVELS + LEGACY_LEVELS))
RUBRIC_KEY = (
    ("Beginning", "Names an idea; the relationship is missing or inaccurate."),
    ("Developing", "Connects some ideas; the mechanism is partly explained."),
    ("Proficient", "Explains the relationship using a scientifically sound mechanism."),
    ("Extending", "Uses the mechanism to explain a new case and its limits."),
)
_PREFIX = re.compile(r"^\s*(?:your answer\s*[\u2014\u2013-]\s*)?(?:critical aspect\s*\d*\s*[\u00b7:.\u2014\u2013-]?\s*)?",
                     re.IGNORECASE)


def aspect_key(name):
    """Exact-name join key for a critical aspect across lessons.

    Only case, spacing, surrounding punctuation and the deck's own
    "Your answer — Critical Aspect N ·" prefix are ignored. A differently
    worded aspect is a different key; linking two keys is a teacher decision.
    """
    if not isinstance(name, str):
        return ""
    text = _PREFIX.sub("", name.strip())
    text = re.sub(r"\s+", " ", text).strip(" .,:;\u00b7\u2014\u2013-").casefold()
    return text
