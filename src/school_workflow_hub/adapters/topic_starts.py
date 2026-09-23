"""Durable topic starts from the teacher's dashboard saves, and their deadlines.

A start lives in the existing private cloud state, keyed by the same stream as
submission monitoring (class + turn-in folder), with its source excerpt. Saves
that repeat the same start change nothing; a genuinely different start date is
kept as a flagged conflict, never resolved by guessing. Deadlines are derived
on read from the Agenda's own Weeks calendar, so a later closure legitimately
moves an unfinished count while nothing else ever does.
"""
from datetime import datetime, timezone

from .dated_agenda import grid, source_date, value
from .hosted_agenda import authenticated_reader, ConnectionError
from ..core.cloud_state import CloudState, Conflict, key
from ..workflows.topic_deadline import (
    _EXCEPTION, derive_deadline, detect_declarations, parse_week_calendar,
)

START_VERSION = "hub-topic-start/v1"
MAX_CONFLICTS = 12


def _start_id(scope):
    return key("topicstart", scope)


def _now():
    return datetime.now(timezone.utc).isoformat()


def read_week_calendar(assertion, config):
    """One bounded read of the Weeks tab: week start -> parsed closure info."""
    reader, source = authenticated_reader(assertion, config)
    data = reader.read(source, ("'🗂 Weeks'!A2:G43",))
    weeks = grid(data, "🗂 Weeks")
    if [value(weeks, 2, c) for c in (2, 3, 4)] != ["course row", "week", "Monday"]:
        raise ConnectionError("agenda_layout_changed")
    calendar = {}
    for row in range(3, 44):
        raw = value(weeks, row, 4)
        if not raw:
            continue
        start = source_date(raw)
        calendar[start.isoformat()] = parse_week_calendar(start, value(weeks, row, 5))
    return calendar


def read_start(store, scope):
    doc = store.port.read([_start_id(scope)]).documents[_start_id(scope)]
    return dict(doc.value) if doc else None


def record_declarations(store, scope, saved_date, plan, notes, *, source_kind):
    """Persist what one day's Plan/Notes actually declare. CAS, idempotent.

    Returns a summary of what happened; storing nothing is a valid outcome.
    Repeated saves of the same text change nothing. A different start date for
    the same stream becomes a flagged conflict beside the original.
    """
    found = detect_declarations(plan, notes, saved_date)
    if not (found["start"] or found["exception"] or found["intention"]):
        return {"recorded": None}
    record_id = _start_id(scope)
    for _ in range(4):
        snapshot = store.port.read([record_id])
        doc = snapshot.documents[record_id]
        state = dict(doc.value) if doc else {
            "schema_version": START_VERSION, "scope": dict(scope), "start": None,
            "exceptions": [], "intentions": [], "conflicts": []}
        changed, outcome = False, {"recorded": None, "conflict": False}
        if found["start"]:
            entry = {**found["start"], "source_kind": source_kind, "recorded_at": snapshot.read_time}
            current = state.get("start")
            if current is None:
                state["start"] = entry
                changed, outcome["recorded"] = True, "start"
            elif current["date"] == entry["date"]:
                outcome["recorded"] = "start_already_recorded"
            elif any(c["date"] == entry["date"] for c in state["conflicts"]):
                outcome.update(recorded="start_conflict_already_flagged", conflict=True)
            else:
                state["conflicts"] = (state["conflicts"] + [entry])[-MAX_CONFLICTS:]
                changed = True
                outcome.update(recorded="start_conflict_flagged", conflict=True)
        if found["exception"]:
            entry = {**found["exception"], "source_kind": source_kind,
                     "declared_on": saved_date, "recorded_at": snapshot.read_time}
            if not any(e["date"] == entry["date"] and e["declared_on"] == saved_date
                       for e in state["exceptions"]):
                state["exceptions"] = (state["exceptions"] + [entry])[-MAX_CONFLICTS:]
                changed = True
                outcome["recorded"] = outcome["recorded"] or "exception"
        if found["intention"] and not found["start"]:
            entry = {**found["intention"], "declared_on": saved_date}
            if entry not in state["intentions"]:
                state["intentions"] = (state["intentions"] + [entry])[-MAX_CONFLICTS:]
                changed = True
                outcome["recorded"] = outcome["recorded"] or "intention"
        if not changed:
            outcome["start_date"] = (state.get("start") or {}).get("date")
            return outcome
        try:
            store.port.commit({record_id: (state, doc.version if doc else None)})
            outcome["start_date"] = (state.get("start") or {}).get("date")
            return outcome
        except Conflict:
            continue
    raise Conflict("topic_start_contention")


def deadline_status(assertion, scope, config, store, calendar=None):
    """The stream's derived deadline: calculated only, never IC-verified here."""
    state = read_start(store, scope)
    if state is None or state.get("start") is None:
        return {"status": "no_topic_start_recorded", "due_date": None,
                "basis": "no_topic_start_recorded", "meetings": [], "provisional": False,
                "conflicts": [], "exception": None,
                "ic_alignment": "calculated_not_verified_in_ic"}
    # Re-validate stored excerpts against the current detector, so an entry a
    # looser earlier rule recorded from routine lesson text stops applying
    # without deleting the stored evidence.
    exceptions = [e for e in (state.get("exceptions") or [])
                  if _EXCEPTION.search(e.get("excerpt") or "")]
    exception = sorted(exceptions, key=lambda e: e["recorded_at"])[-1]["date"] if exceptions else None
    calendar = calendar if calendar is not None else read_week_calendar(assertion, config)
    derived = derive_deadline(scope["class_key"], state["start"]["date"], calendar,
                              exception_date=exception)
    conflicts = state.get("conflicts") or []
    status = "start_conflict_needs_review" if conflicts else (
        "provisional" if derived["provisional"] and not exception else "calculated")
    return {"status": status, "start_date": state["start"]["date"],
            "start_source": {k: state["start"].get(k) for k in ("field", "excerpt", "source_kind")},
            "due_date": derived["due_date"], "basis": derived["basis"],
            "meetings": derived["meetings"], "provisional": derived["provisional"],
            "flags": derived["flags"], "conflicts": [c["date"] for c in conflicts],
            "exception": exception, "ic_alignment": "calculated_not_verified_in_ic"}
