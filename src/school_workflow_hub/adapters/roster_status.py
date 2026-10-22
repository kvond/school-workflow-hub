"""Explicit mirror status columns shared by the two existing roster readers.

An unconfigured legacy sheet keeps its existing behavior. A configured status
column must have its exact header and a recognized status on every named row;
unavailable/blank status can never silently revive a withdrawn membership.
"""
import json
from .hosted_agenda import ConnectionError

STATUSES = {"active", "incoming", "withdrawn", "inactive"}


def configured_columns(config, sections):
    raw = config.get("HUB_ROSTER_STATUS_COLUMNS", {})
    try:
        columns = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        columns = None
    if (not isinstance(columns, dict) or set(columns) - set(sections)
            or any(type(c) is not int or not 3 <= c <= 25 for c in columns.values())):
        raise ConnectionError("roster_status_configuration_invalid")
    return columns


def active_status(value):
    if value not in STATUSES:
        raise ConnectionError("roster_status_unverified")
    return value == "active"


def require_header(value):
    if not isinstance(value, str) or value.strip().casefold() != "roster status":
        raise ConnectionError("roster_status_header_unverified")
