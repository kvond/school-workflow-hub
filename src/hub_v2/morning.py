"""Read-only morning lists and counts-only receipts in the existing private ledger.

The runtime's explicit Form/section/component bindings extend the existing IC
resource reference contract. No destination is inferred from a lesson title.
"""
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from . import completion, google, ledger, work

KIND = "Morning helper"
_log = logging.getLogger(__name__)
CODES = frozenset(("running", "finished", "no_scores", "mapping_required", "signed_out", "hub_unavailable",
    "ic_unavailable", "panel_unusable", "missed_noon", "interrupted", "disabled", "uncertain_blocked",
    "read_back", "nothing_to_enter", "ic_changed", "ic_unreadable", "typing_stopped", "typed_values_differ",
    "save_unconfirmed", "readback_incomplete", "readback_mismatch", "destination_mapping_changed",
    "students_dont_match", "several_sections", "ic_not_saved", "unsupported_points_scale",
    "unsupported_gradebook", "list_expired", "source_changed", "receipt_unconfirmed", "coverage_incomplete", "excluded_week", "source_scores_held"))


def bindings(config):
    try:
        value = json.loads(config.get("HUB_IC_ASSIGNMENT_MAP", "[]"))
    except (ValueError, TypeError):
        raise ValueError("mapping") from None
    if (not isinstance(value, list) or len(value) > 500
            or any(not isinstance(m, dict) or set(m) != {"class_key", "form_id", "targets"}
                   or not isinstance(m["targets"], list) for m in value)):
        raise ValueError("mapping")
    return value


def catalog(agenda, config=None):
    today = ledger.eastern_now()[:10]
    out, seen, exclusions, problems = [], set(), [], []
    for cls in agenda.classes:
        for week in agenda.week_options(cls):
            if not week.get("monday") or week["monday"] > today:
                continue
            policy = work.week_policy(config or {}, agenda, cls, week["label"])
            if policy["problem"]:
                problems.append(policy["problem"])
                continue
            if policy["excluded"]:
                exclusions.append({"class": cls, "class_name": agenda.classes[cls]["name"],
                                   "week": week["label"], "school_year": policy["school_year"],
                                   "reason": policy["reason"]})
                continue
            for lesson in agenda.lessons(cls, week["label"]):
                for form in lesson["forms"]:
                    fid = form.get("form_id")
                    if fid and (cls, fid) not in seen:
                        seen.add((cls, fid))
                        label = form.get("label")
                        if label and re.fullmatch(r"https://docs\.google\.com/forms/d/" + re.escape(str(fid))
                                                  + r"(?:/(?:edit|viewform))?/?(?:[?#]\S*)?", str(label).strip()):
                            label = lesson["topic"]
                        out.append({"class": cls, "class_name": agenda.classes[cls]["name"],
                                    "week": week["label"], "form": fid,
                                    "assignment": str(label or lesson["topic"])[:200]})
    return {"assignments": out, "exclusions": exclusions, "problems": list(dict.fromkeys(problems)), "roster": "unavailable",
            "roster_note": "IC is the roster source of truth. Roster sync is not connected; normal additions and departures are not student-work exceptions."}


def person(row):
    return (work.norm(row.get("last", "")), work.norm(row.get("first", "")), row["section"])


def closed_recheck(form, mark, now=None):
    """A prior final entry permits current submitted-work rechecks, not new zeros."""
    if mark.get("configured") is not True or mark.get("problem"):
        return False
    cutoff = work.when((form.get("window") or {}).get("at"))
    entered = work.when((mark.get("entered_at") or {}).get("at"))
    now = now or datetime.now(timezone.utc)
    return bool(cutoff and entered and cutoff < now and cutoff <= entered <= now)


def rows(form, scored, now, *, recheck=False):
    scores = {person(row): score for row, score in scored}
    window = form.get("window")
    if not isinstance(window, dict):
        window = None
    start = work.when(window.get("from")) if window else None
    cutoff = work.when(window.get("at")) if window else None
    started = bool(window and (not window.get("from") or (start and start <= now)))
    deadline_known = bool(cutoff and (not window.get("from") or (start and start <= cutoff)))
    closed = bool(deadline_known and cutoff < now)
    elsewhere = set()
    elsewhere_details = {}
    accepted = set()
    for item in form.get("elsewhere", []):
        last, _, first = item.get("who", "").partition(",")
        identity = person({"last": last, "first": first, "section": item["section"]})
        (accepted if item.get("accepted_intake") else elsewhere).add(identity)
        if not item.get("accepted_intake"):
            elsewhere_details.setdefault(identity, []).append(item)
    result = {}

    def add(row, kind, notes=None, revision=None, reason=""):
        key = person(row)
        if not str(row.get("last", "")).strip() or not row.get("section"):
            return
        if key in result:
            result[key].update(kind="check", notes=None, revision=None, reason="listed twice on the roster")
            return
        # The helper accepts plain reasons of at most 200 UTF-16 characters.
        # Keep the complete source problem in scored/work; bound only this row view.
        reason = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", str(reason or "")).split())
        reason = reason.encode("utf-16-le", errors="replace")[:400].decode("utf-16-le", errors="ignore")
        result[key] = {"last": row["last"], "first": row.get("first", ""), "section": row["section"],
                       "kind": kind, "notes": notes, "revision": revision, "reason": reason}

    candidates = (completion.scoring_candidates(form, include_late=recheck) if form.get('combined_assessment') else
                  completion.recheck_candidates(form, now=now) if recheck else form["students"])
    for row in candidates:
        if not row.get("matched"):
            continue
        score = scores.get(person(row))
        if row.get("selection_problem"):
            add(row, "check", reason=row["selection_problem"])
            continue
        if (score and not score.get("problem") and not score.get("note")
                and score.get("scoring_basis") == "found_codes"
                and all(score.get(k) is None for k in ("notes_score", "revision_score"))
                and all(score.get("counts", {}).get(category, {}).get("required") == 0 for category in ("NOTES", "BANK", "DRAFT"))):
            add(row, "none", reason="N/A: no countable coded boxes; no zero prepared")
        elif not score or score.get("problem") or score.get("note") or all(score.get(k) is None for k in ("notes_score", "revision_score")):
            add(row, "check", reason=(score.get('problem') or score.get('note') or 'deck needs scoring by hand')
                if score else 'deck needs scoring by hand')
        else:
            add(row, "score", score.get("notes_score"), score.get("revision_score"),
                reason=(score.get('component_coverage') or {}).get('text', '')[:200])
    absent = form.get("missing", []) + ([] if recheck else [r for r in form.get("late", []) if r.get("matched")])
    if form.get('combined_assessment'):
        represented = {person(row) for row in candidates if row.get('combined_components') and not row.get('selection_problem')}
        absent = [row for row in absent if person(row) not in represented]
    for row in absent:
        if form.get('combined_assessment'):
            add(row, 'none', reason='No eligible submitted component; no combined missing-work zero prepared')
        elif not started:
            add(row, "none", reason="its two weeks haven't started" if deadline_known else
                "its two-week deadline is unconfirmed; no zero prepared")
        elif row.get("unsorted") or person(row) in elsewhere:
            # Only the canonical routed destination may name another lesson.
            # A filename or an unverified/ambiguous destination cannot supply it.
            destinations = {" ".join(str(item.get("to") or "").split())
                            for item in elsewhere_details.get(person(row), [])
                            if form.get("form_id") and item.get("source_form_id") == form.get("form_id")
                            and item.get("assignment_form_id")
                            and item["assignment_form_id"] != form.get("form_id")
                            and isinstance(item.get("to"), str) and item["to"].strip()}
            if len(destinations) == 1:
                destination = next(iter(destinations))
                destination = destination.encode("utf-16-le", errors="replace")[:160].decode("utf-16-le", errors="ignore")
                reason = "Received for %s; not this lesson. Review its original turn-in route; preserve current grades." % destination
            else:
                reason = "Submission needs review; confirm its original lesson and turn-in route. Preserve current grades."
            add(row, "check", reason=reason)
        elif person(row) in accepted:
            add(row, "none", reason="accepted through this Form for another assignment; no score inferred here")
        elif recheck:
            add(row, "none", reason="previously entered assignment; no missing-work zero in this recheck")
        elif not closed:
            add(row, "none", reason="its two-week deadline has not passed; no zero prepared" if deadline_known else
                "its two-week deadline is unconfirmed; no zero prepared")
        else:
            add(row, "zero", 0, 0)
    for row in form.get("excused", []):
        add(row, "excused", reason="new student")
    return list(result.values())



def mapped_entry_rows(values, targets):
    """Project only helper values to an intentional exact component mapping.

    Source assessments and feedback are not modified. An absent mapping is not
    an intentional partial map; its existing downstream refusal is unchanged.
    """
    from . import morning_entry
    if not targets:
        return values, targets, False
    if not isinstance(targets, list) or len(targets) > 12:
        raise ValueError("mapping")
    pairs, urls, sections, section_ids = set(), set(), {}, {}
    for target in targets:
        morning_entry._target(target)
        pair = (target["section"], target["column"])
        context = tuple(target[k] for k in ("section_id", "term_id", "year", "school_id", "calendar_id", "structure_id", "task_id"))
        if (pair in pairs or target["url"] in urls
                or target["section"] in sections and sections[target["section"]][0] != context
                or target["section_id"] in section_ids and section_ids[target["section_id"]] != target["section"]):
            raise ValueError("mapping")
        pairs.add(pair); urls.add(target["url"])
        sections.setdefault(target["section"], (context, set()))[1].add(target["column"])
        section_ids[target["section_id"]] = target["section"]
    if any(row["kind"] in ("score", "zero") and row["section"] not in sections for row in values):
        raise ValueError("mapping")
    partial = any(columns != {"notes", "revision"} for _, columns in sections.values())
    if not partial:
        return values, targets, False
    projected = []
    for row in values:
        # A partial mapping does not authorize an automatic absence zero.
        if row["kind"] == "zero":
            continue
        item = dict(row)
        if row["kind"] == "score":
            for column in ("notes", "revision"):
                if (row["section"], column) not in pairs:
                    item[column] = None
            if item["notes"] is None and item["revision"] is None:
                item.update(kind="none", reason="Mapped IC component has no measured score; no zero prepared")
        projected.append(item)
    needed = {(row["section"], column) for row in projected if row["kind"] == "score"
              for column in ("notes", "revision") if row[column] is not None}
    return projected, [target for target in targets if (target["section"], target["column"]) in needed], True


def make_list(token, config, agenda, cls, week, fid, load_work, score_rows):
    from . import combined_assessment
    try:
        with combined_assessment.source_request():
            return _make_list(token, config, agenda, cls, week, fid, load_work, score_rows)
    except (combined_assessment.Held, google.GoogleError) as error:
        return {'code': 'coverage_incomplete', 'problem': str(error),
                **({'provider_status': error.status, 'provider_reason': error.reason}
                   if isinstance(error, google.GoogleError) else {})}


def _make_list(token, config, agenda, cls, week, fid, load_work, score_rows):
    policy = work.assignment_policy(config, agenda, cls, week, fid)
    if policy["problem"]:
        return {"code": "coverage_incomplete", "problem": policy["problem"]}
    if policy["excluded"]:
        return {"code": "excluded_week", "problem": policy["reason"]}
    found = load_work(token, config, agenda, cls, week, fresh=True)
    forms = [(lesson, form) for lesson in found["lessons"] for form in lesson["forms"] if form["form_id"] == fid]
    if len(forms) != 1:
        raise ValueError("form")
    lesson, form = forms[0]
    if found.get("problems") or form.get("problem"):
        return {"code": "coverage_incomplete"}
    # Do not score one assignment while submissions from the same weekly
    # folders still lack a safe lesson match.
    if found.get("other_files"):
        return {"code": "coverage_incomplete"}
    ready = completion.assignment_readiness(token, config, agenda, cls, week, fid)
    if (form.get('combined_assessment') or {}).get('problem'):
        return {'code': 'coverage_incomplete', 'problem': form['combined_assessment']['problem']}
    form["grade_readiness"] = ready
    if ready["status"] != "READY":
        return {"code": "coverage_incomplete", "readiness": ready, "problem": ready["problem"], "action": ready.get("action")}
    now = datetime.now(timezone.utc)
    mark = ledger.read(token, config, fid)
    if not mark.get("configured") or mark.get("problem"):
        return {"code": "receipt_unconfirmed"}
    window = form.get("window")
    cutoff = work.when(window.get("at")) if window else None
    if window and not cutoff:
        return {"code": "coverage_incomplete"}
    entered = work.when((mark.get("entered_at") or {}).get("at"))
    if mark.get("entered_at") and (not entered or entered > now):
        return {"code": "receipt_unconfirmed"}
    closed = bool(cutoff and cutoff < now)
    recheck = closed_recheck(form, mark, now)
    # Current-source uncertainty belongs to that student's check row. The
    # scorer and list preserve it while other verified candidates proceed.
    scored = (score_rows(token, agenda, cls, week, fid, form, found, include_late=True) if recheck
              else score_rows(token, agenda, cls, week, fid, form, found))
    blocked = next((score for _, score in scored if score.get("code") == "deck_not_ready"), None)
    if blocked:
        return {"code": "coverage_incomplete", "readiness": blocked.get("readiness"),
                "problem": blocked["problem"], "action": blocked.get("action")}
    values = rows(form, scored, now, recheck=recheck)
    if not any(r["kind"] in ("score", "zero") for r in values):
        checks = sum(r["kind"] == "check" for r in values)
        if checks:
            # A submitted score needing review is not an empty assignment.
            # Keep its count visible without claiming any IC comparison.
            return {"code": "source_scores_held", "source_holds": {"checks": checks}}
        return {"code": "no_scores"}
    matched = [m for m in bindings(config) if m.get("class_key") == cls and m.get("form_id") == fid]
    if len(matched) != 1:
        return {"code": "mapping_required"}
    try:
        values, targets, partial = mapped_entry_rows(values, matched[0].get("targets", []))
    except ValueError:
        return {"code": "mapping_required"}
    if not any(r["kind"] == "score" or r["kind"] == "zero" for r in values):
        checks = sum(r["kind"] == "check" for r in values)
        return ({"code": "source_scores_held", "source_holds": {"checks": checks}} if checks else {"code": "no_scores"})
    label = " ".join(str(form.get("label") or lesson["topic"]).split())[:200]
    value = {"schema": "hub-ic-list/v1", "list_id": str(uuid.uuid4()), "class_key": cls,
        "class_name": agenda.classes[cls]["name"], "week": week, "form_id": fid, "assignment": label,
        "form_label": label, "status": "closed" if closed else "open", "status_text": "Morning scores",
        "sent_at": now.isoformat(), "rows": values}
    basis = [form, scored, values, targets] if partial else [form, scored]
    revision = hashlib.sha256(json.dumps(basis, sort_keys=True, default=str).encode()).hexdigest()
    return {"code": "ready", "list": value, "revision": revision, "targets": targets,
            **({"partial_components": True} if partial else {})}


PREFLIGHT_STAGES = frozenset(("summary_publish", "summary_backlog", "hub_connection"))
PREFLIGHT_CAUSES = frozenset(("hub_tab_missing", "hub_tab_count", "hub_tab_closed", "hub_origin_changed",
    "hub_bridge_missing", "hub_bridge_closed", "hub_bridge_timeout", "hub_bridge_unavailable",
    "hub_signed_out", "hub_access_denied", "hub_http_error", "hub_request_timeout", "hub_network_error",
    "hub_invalid_response", "hub_report_not_acknowledged", "previous_receipt_pending", "unrecorded"))


def validate_report(value):
    fields = {"day", "run_id", "code", "roster", "items"}
    if not isinstance(value, dict) or set(value) not in (fields, fields | {"preflight"}):
        raise ValueError("report")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value["day"])) or str(uuid.UUID(value["run_id"])) != value["run_id"]:
        raise ValueError("report")
    if value["code"] not in CODES or value["roster"] != "unavailable" or not isinstance(value["items"], list) or len(value["items"]) > 300:
        raise ValueError("report")
    if "preflight" in value:
        detail = value["preflight"]
        if (value["items"] or value["code"] not in ("receipt_unconfirmed", "hub_unavailable")
                or not isinstance(detail, dict)
                or set(detail) not in ({"stage", "cause"}, {"stage", "cause", "status"})
                or not isinstance(detail.get("stage"), str) or detail["stage"] not in PREFLIGHT_STAGES
                or not isinstance(detail.get("cause"), str) or detail["cause"] not in PREFLIGHT_CAUSES
                or ("status" in detail and (type(detail["status"]) is not int or not 100 <= detail["status"] <= 599))):
            raise ValueError("report")
    for item in value["items"]:
        legacy_item = {"assignment", "code", "filled", "raised", "kept", "left", "reasons"}
        current_item = legacy_item | {"class_name", "week"}
        source_item = current_item | {"source_holds"}
        if not isinstance(item, dict) or set(item) not in (legacy_item, current_item, source_item):
            raise ValueError("report")
        if not isinstance(item["assignment"], str) or len(item["assignment"]) > 200 or item["code"] not in CODES:
            raise ValueError("report")
        if "class_name" in item and (not isinstance(item["class_name"], str) or not item["class_name"].strip() or len(item["class_name"]) > 100):
            raise ValueError("report")
        if "week" in item and (not isinstance(item["week"], str) or not item["week"].strip() or len(item["week"]) > 100):
            raise ValueError("report")
        if any(type(item[k]) is not int or not 0 <= item[k] <= 3600 for k in ("filled", "raised", "kept", "left")):
            raise ValueError("report")
        legacy_reasons = {"flag", "excused", "check", "name", "missing"}
        current_reasons = legacy_reasons | {"exempt"}
        if (not isinstance(item["reasons"], dict)
                or set(item["reasons"]) not in (legacy_reasons, current_reasons)
                or any(type(n) is not int or not 0 <= n <= 3600 for n in item["reasons"].values())):
            raise ValueError("report")
        if item["code"] == "source_scores_held" or "source_holds" in item:
            detail = item.get("source_holds")
            if (set(item) != source_item or item["code"] != "source_scores_held"
                    or not isinstance(detail, dict) or set(detail) != {"checks"}
                    or type(detail["checks"]) is not int or not 1 <= detail["checks"] <= 3600
                    or any(item[k] for k in ("filled", "raised", "kept", "left"))
                    or any(item["reasons"].values())):
                raise ValueError("report")
    source_held = any(item["code"] == "source_scores_held" for item in value["items"])
    if (value["code"] == "source_scores_held" and not source_held
            or value["code"] == "finished" and source_held):
        raise ValueError("report")
    return value


def _report_target(token, config):
    sheet = ledger.find(token, config)
    if not sheet:
        return None
    tab = ledger._first_tab(token, sheet["id"])
    return (sheet["id"], tab) if tab else None


def _report_history(token, target):
    # One sentinel row detects incomplete coverage. The ten-report dashboard
    # projection is never an idempotency or persistence-verification source.
    sheet, tab = target
    source = google.sheet_values(token, sheet, f"{tab}!A1:G{ledger.MAX_ROWS + 1}")
    if len(source) > ledger.MAX_ROWS:
        raise google.GoogleError("The morning receipt history exceeds its bounded read; preserve it for repair.")
    history = []
    for row in source:
        if len(row) < 7 or row[1] != KIND:
            continue
        try:
            value = validate_report(json.loads(row[6]))
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
        history.append(value)
    return history, len(source)


def reports(token, config):
    target = _report_target(token, config)
    if target is None:
        return []
    history, _ = _report_history(token, target)
    runs = {}
    for value in history:
        prior = runs.pop(value["run_id"], [])
        if value not in prior:
            prior.append(value)
        runs[value["run_id"]] = prior
    recent = sorted(reversed(list(runs.values())), key=lambda values: max(r["day"] for r in values), reverse=True)[:10]
    return [_project_report(values) for values in recent]


def _project_report(values):
    """Project only proven progress, never Sheets append arrival order.

    The helper publishes exact completed-item prefixes while running and one
    terminal snapshot. A late concurrent starting append cannot erase that
    terminal result. Conflicting histories have no protocol sequence with which
    to order them: refuse the read instead of inventing a successful receipt.
    """
    def prefix(earlier, later):
        return (earlier["day"] == later["day"]
                and earlier["items"] == later["items"][:len(earlier["items"])])

    terminal = [value for value in values if value["code"] != "running"]
    running = [value for value in values if value["code"] == "running"]
    candidate = terminal[0] if terminal else max(running, key=lambda value: len(value["items"]))
    if len(terminal) > 1 or any(not prefix(value, candidate) for value in running):
        _report_event(candidate, "projection", "conflicting_saved_states")
        raise google.GoogleError("Morning run %s has conflicting saved receipt states. Preserve its history and reconcile the exact helper receipt before reporting completion." % candidate["run_id"])
    return candidate


def _report_event(value, stage, outcome, error=None):
    # Correlate only safe request identity and fixed stages/outcomes. Never log
    # assignment labels, source IDs, tokens, raw payloads or provider messages.
    event = {"event": "morning_report", "run_id": value["run_id"], "day": value["day"],
             "code": value["code"], "stage": stage, "outcome": outcome}
    status = getattr(error, "status", None)
    if type(status) is int and 100 <= status <= 599:
        event["provider_status"] = status
    if google.read_timed_out(error):
        event["provider_reason"] = "read_timeout"
    _log.log(logging.WARNING if error else logging.INFO, json.dumps(event, sort_keys=True))


def record(token, write_token, config, report):
    value = validate_report(report)
    payload = json.dumps(value, separators=(",", ":"))
    if len(payload) > 45000:
        raise ValueError("report")
    stage = "locate"
    try:
        target = _report_target(token, config)
        if target is None:
            raise google.GoogleError("The Scores entered sheet is unavailable.")
        stage = "history_read"
        history, count = _report_history(token, target)
        # A known older state must not replace a later saved state for this run.
        # This also acknowledges historical outbox entries outside the UI list.
        if value in history:
            _report_event(value, stage, "already_recorded")
            return
        if count >= ledger.MAX_ROWS:
            raise google.GoogleError("The morning receipt history is full; preserve it for repair before adding a report.")
    except google.GoogleError as error:
        _report_event(value, stage, "unconfirmed", error)
        raise
    sheet, tab = target
    append_error = None
    try:
        google.append_rows(write_token, sheet, tab + "!A1", [[ledger.eastern_now(), KIND, "", value["day"], "Morning run", value["run_id"], payload]])
    except google.GoogleError as error:
        # The provider may have accepted the append before losing its response.
        # Read this exact destination once; do not repeat the write.
        append_error = error
        _report_event(value, "append", "uncertain", error)
    try:
        saved, _ = _report_history(token, target)
        if value not in saved:
            raise google.GoogleError("The morning summary could not be read back.")
    except google.GoogleError as error:
        _report_event(value, "readback", "unconfirmed", error)
        raise
    _report_event(value, "readback", "recovered_after_uncertain_append" if append_error else "recorded")
