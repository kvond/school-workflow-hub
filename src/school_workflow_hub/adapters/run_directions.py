"""Private values for the control panel's run directions, from hosted config.

`web/run-directions.json` carries the teacher's wording with {{placeholders}}
where every school identifier would be, because the repository is not allowed to
hold one. The values arrive as runtime configuration on the service instead, so
the filled directions render on any computer she signs in from rather than on
whichever laptop happens to hold a local file.

Reading them needs the same verified IAP identity as the Agenda reader. These are
private inputs, not preview assets: an unauthenticated request never reaches them,
and no value is written to a log or an error message.
"""
import json
import os

from .hosted_agenda import ConnectionError, _identity_configuration, authorize

SCHEMA_VERSION = "hub-run-directions-values/v1"

# Exactly the placeholders the committed template declares. A name outside this
# set is refused rather than passed through, so a mistyped or hostile variable
# cannot inject text into directions the teacher will follow.
DECLARED = frozenset({
    "timezone", "school_year", "school_account", "infinite_campus_url",
    "dashboard_url", "agenda_url", "roster_workbook_url",
    "assessment_history_url", "roster_sections", "roster_protected_cells",
    "checkpoint_override",
})

# The checkpoint section is optional by design: it names one cycle's assignment
# and is meant to be absent between cycles, so its absence is not a gap.
OPTIONAL = frozenset({"checkpoint_override"})

MAX_VALUE = 500


def _values(config):
    raw = config.get("HUB_RUN_DIRECTIONS", "")
    if not str(raw).strip():
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise ConnectionError("run_directions_not_valid_json") from None
    if not isinstance(parsed, dict):
        raise ConnectionError("run_directions_not_an_object")
    values = {}
    for name, value in parsed.items():
        if name not in DECLARED:
            raise ConnectionError("run_directions_unknown_placeholder")
        if not isinstance(value, str) or len(value) > MAX_VALUE:
            raise ConnectionError("run_directions_value_not_short_text")
        if value.strip():
            values[name] = value
    return values


def read_values(assertion, config=None):
    """Return the configured values for a verified teacher, and what is missing."""
    config = os.environ if config is None else config
    audience, allowed = _identity_configuration(config)
    authorize(assertion, audience, allowed)
    values = _values(config)
    missing = tuple(sorted(DECLARED - OPTIONAL - set(values)))
    return {
        "schema_version": SCHEMA_VERSION,
        "values": values,
        "missing": missing,
        "complete": not missing,
        "external_writes": 0,
        "live_model_calls": 0,
    }
