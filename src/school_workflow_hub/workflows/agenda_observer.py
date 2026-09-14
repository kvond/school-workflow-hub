"""Read -> normalize -> compare -> checkpoint; no platform writes or deployment.

The host injects reader, private SQLite connection, clock and HMAC key. Actual
identity/storage configuration is owned by the separate cloud implementation.
"""
from dataclasses import dataclass, field
from typing import Tuple

from school_workflow_hub.adapters.agenda_rows import AgendaLayout, AgendaRow, normalize_agenda_rows
from school_workflow_hub.adapters.google_sheets import fingerprint_snapshot, validate_ranges
from school_workflow_hub.core.observation_baseline import read_baseline, checkpoint_observation


@dataclass(frozen=True)
class AgendaObservationResult:
    status: str
    rows: Tuple[AgendaRow, ...] = field(repr=False)
    issue_codes: Tuple[str, ...]
    saved: bool
    change_proven: bool
    reader_invoked: bool
    external_writes: int = 0


def _column(index):
    value, result = index + 1, ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def observe_agenda(reader, connection, *, spreadsheet_id, sheet_title,
                   layout: AgendaLayout, key: bytes, stream_key: str, clock):
    """Only a complete, validated read may replace the last good observation.

    Callers may inspect rows privately. Do not persist raw rows through fixture
    APIs. A baseline conflict requires a new read, not replaying a cached result.
    """
    if (not isinstance(sheet_title, str) or not sheet_title or len(sheet_title) > 100
            or any(ord(c) < 32 for c in sheet_title) or not isinstance(layout, AgendaLayout)
            or not isinstance(key, bytes) or len(key) != 32):
        raise ValueError("invalid_agenda_observer_configuration")
    columns = layout.columns()
    selected = "'{}'!{}{}:{}{}".format(sheet_title.replace("'", "''"),
        _column(min(columns)), layout.first_row + 1, _column(max(columns)), layout.last_row)
    validate_ranges((selected,))
    previous = read_baseline(connection, stream_key)
    expected = previous["checkpoint_token"] if previous else None
    try:
        data = reader.read(spreadsheet_id, (selected,))
    except Exception:
        return AgendaObservationResult("blocked", (), ("agenda_read_failed",), False, False, True)
    try:
        matches = [s for s in data["sheets"] if s["properties"]["sheetId"] == layout.sheet_id
                   and s["properties"]["title"] == sheet_title]
        if data["spreadsheetId"] != spreadsheet_id or len(matches) != 1:
            raise ValueError()
        observation = fingerprint_snapshot(data, key, (selected,))
        normalized = normalize_agenda_rows(data, layout, source_revision=observation["fingerprint"],
            coverage=(layout.first_row, layout.last_row, min(columns), max(columns) + 1))
        if not normalized.complete:
            return AgendaObservationResult("blocked", (), tuple(sorted({i.code for i in normalized.issues})),
                                           False, False, True)
        now = clock()
        if now.utcoffset() is None:
            raise ValueError()
        checkpoint = checkpoint_observation(connection, stream_key, observation,
            observed_at=now.isoformat(), now=now.isoformat(), expected_checkpoint=expected)
    except (ValueError, TypeError, KeyError, AttributeError):
        return AgendaObservationResult("blocked", (), ("invalid_agenda_observation",), False, False, True)
    return AgendaObservationResult(checkpoint["status"], normalized.rows if checkpoint["saved"] else (),
                                   (), checkpoint["saved"], checkpoint["change_proven"], True)
