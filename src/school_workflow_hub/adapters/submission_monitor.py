"""Hosted submission monitoring: form-first discovery into durable private state.

Each scan selects exact-assignment responses from the shared Google Form and
then verifies every referenced file inside the approved submission root. The
reviewed grading workbook supplies only student↔file matches; it is never used
as a prerequisite for discovering new work. Reruns, claims, and baselines use
the existing private cloud state. No grades, zeros, schedules, Forms writes, or
Drive writes happen here.
"""
from datetime import datetime, timezone
import re

from .dated_agenda import read_day
from .firestore_state import configured_store
from .grading_records import read_records
from .hosted_agenda import ConnectionError
from .forms_submissions import (collect_assignment_submissions,
                                historical_assignment_file_ids)
from .roster_names import read_forensics_roster, match_hint
from ..core.cloud_state import Busy, CloudState, Conflict, LeaseLost, StoreUnavailable, key
from ..core.runs import content_hash
from ..workflows.submission_monitor import (
    CLASS_KEYS, DERIVATION_VERSION, advance_state, assignment_scope,
)
from . import topic_starts

OWNER = "hosted-submission-monitor"
ATTEMPT_VERSION = "hub-monitor-attempt/v1"
STATUS_VERSION = "hub-submission-monitor-status/v1"
MAX_LISTED = 120
DISCOVERY_VERSION = 5


def _now():
    return datetime.now(timezone.utc).isoformat()


def _lesson(assertion, day, class_key, config):
    if class_key not in CLASS_KEYS:
        raise ConnectionError("invalid_submission_scope")
    agenda = read_day(assertion, day, config)
    lesson = next((l for l in agenda["lessons"] if l["class_key"] == class_key), None)
    if (lesson is None or not lesson.get("assignment_label")
            or not lesson.get("turnin_form")):
        raise ConnectionError("submission_lesson_mapping_required")
    return agenda, lesson, lesson["assignment_label"]



def _student_name_hint(name):
    """Conservative display hint from school-generated upload filenames only."""
    text = str(name or "")
    if not re.match(r"^s\.[^\s]+\s+—\s+", text, re.I):
        return None
    tail = text.rsplit(" - ", 1)[-1]
    tail = re.sub(r"\.(?:pptx|ppt|pdf)$", "", tail, flags=re.I).strip()
    if not 2 <= len(tail.split()) <= 6 or len(tail) > 100:
        return None
    return tail

def _drive_scan(assertion, day, class_key, config):
    """Historical name retained for callers/tests; source is now Form + root."""
    _, lesson, assignment_label = _lesson(assertion, day, class_key, config)
    records = read_records(assertion, config)
    known_file_ids = historical_assignment_file_ids(
        records, class_key, assignment_label)
    folder_match = re.search(r"/folders/([A-Za-z0-9_-]{20,128})",
                             str(lesson.get("turnin_folder") or ""))
    course_root_id = folder_match.group(1) if folder_match else None
    files, evidence = collect_assignment_submissions(
        config, assignment_label, form_id=lesson.get("turnin_form_id"),
        course_root_id=course_root_id, known_file_ids=known_file_ids)
    fresh = read_day(assertion, day, config)
    if next((l for l in fresh["lessons"] if l["class_key"] == class_key), None) != lesson:
        raise ConnectionError("submission_agenda_changed")
    # Bind persisted monitoring evidence to the configured editable Form, but
    # store only a one-way hash. If deployment configuration accidentally points
    # at a different Form, the next complete scan must establish a fresh
    # first-observed baseline instead of labeling historical submissions as new.
    source_id = content_hash({"class_key": class_key,
                              "assignment_label": assignment_label,
                              "form_source": str(lesson.get("turnin_form_id")
                                                 or config.get("HUB_TURNIN_FORM_ID", "")),
                              "discovery_version": DISCOVERY_VERSION})[:40]
    return {"class_key": class_key, "source_id": source_id,
            "source_name": "Current Form responses · 00 ALL TURN IN",
            "source_kind": "form_responses_root_verified",
            "assignment_label": assignment_label, "date": day,
            "observed_at": _now(),
            "day_plan": lesson.get("plan") or "", "day_notes": lesson.get("notes") or "",
            "coverage": {"complete": True, "source": "form_responses_root_verified",
                         "folders_checked": evidence["root_folders_checked"],
                         "forms_found": 1,
                         "responses_checked": evidence["responses_checked"],
                         "matching_responses": evidence["matching_responses"],
                         "root_verified_files": evidence["root_verified_files"],
                         "lesson_folder_files": evidence.get("lesson_folder_files", 0),
                         "form_refs_outside_root": evidence.get("form_refs_outside_root", 0),
                         "form_source": evidence.get("form_source"),
                         "known_recorded_files": evidence.get("known_recorded_files", 0),
                         "legacy_outside_root_files": evidence.get(
                             "legacy_outside_root_files", 0)},
            "files": [{"id": f["id"], "name": f["name"],
                       "modified_at": f.get("modifiedTime"),
                       "version": f.get("version"),
                       "submitted_at": f.get("submitted_at"),
                       "student_hint": _student_name_hint(f.get("name"))}
                      for f in files]}

def _mappings(assertion, class_key, config, files=()):
    records = read_records(assertion, config)
    by_file, conflicted = {}, set()
    import re as _re
    for row in records.get("submissions", []):
        file_id = row.get("source_file_id")
        # Identity for monitoring display: the IC student number when the row
        # has one, else an opaque prefix of the reviewed roster learner key.
        # Roster-matched assessments are real assessments; the panel must not
        # keep showing their files as awaiting.
        identity = str(row.get("student_id") or "")
        if not identity and row.get("status") in {"assessed", "roster_matched"}:
            learner = str(row.get("learner_key") or "")
            if _re.fullmatch(r"roster:[a-f0-9]{8,64}", learner):
                identity = "roster:" + learner[7:23]
        if row.get("class_key") != class_key or not file_id or not identity:
            continue
        # Bind the assessment to the strongest revision evidence the workbook
        # offers: a full check timestamp beats a date, and a bare date can never
        # prove a same-day edit was covered.
        marker, precision = "", "unknown"
        checked = str(row.get("checked_at") or "")
        import re as _re
        if _re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ].+", checked):
            marker, precision = checked, "timestamp"
        else:
            day_marker = str(row.get("source_snapshot_date") or row.get("checked_date") or "")
            if _re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_marker):
                marker, precision = day_marker, "date"
        entry = {"student_id": identity, "student_name": str(row.get("display_name") or "")[:100],
                 "section_id": str(row.get("section_id") or "-"),
                 "section_name": str(row.get("section_name") or "")[:100],
                 "identity_basis": str(row.get("identity_basis") or "workbook")[:64],
                 "assessed_marker": (marker or "unknown")[:40],
                 "marker_precision": precision,
                 "ic_posting_status": str(row.get("ic_posting_status") or "unverified")[:200]}
        prior = by_file.get(file_id)
        if prior and prior["student_id"] != entry["student_id"]:
            # Two students recorded for one file: leave it unmatched for review.
            conflicted.add(file_id)
            continue
        rank = {"unknown": 0, "date": 1, "timestamp": 2}
        if prior is None or (rank[entry["marker_precision"]], entry["assessed_marker"]) >= (
                rank[prior["marker_precision"]], prior["assessed_marker"]):
            by_file[file_id] = entry
    for file_id in conflicted:
        by_file.pop(file_id, None)

    roster_status = "not_used"
    roster_checked = None
    if class_key == "forensics":
        try:
            roster = read_forensics_roster(config)
            roster_status = roster.get("source_title") or "Rosters"
            roster_checked = roster.get("checked_at")
            for item in files or ():
                file_id = str(item.get("id") or "")
                if not file_id or file_id in by_file:
                    continue
                matched = match_hint(roster, item.get("student_hint"))
                if matched:
                    by_file[file_id] = matched
        except ConnectionError:
            # File discovery must still succeed if the roster source is
            # temporarily unavailable. Those files remain unmatched for review.
            roster_status = "unavailable"
    return {"source": "grading_workbook", "checked_at": records["checked_at"],
            "roster_source": roster_status, "roster_checked_at": roster_checked,
            "by_file": by_file}


def _attempt_id(scope):
    return key("attempt", scope)


def _record_attempt(store, scope, outcome, reason, run_id=None):
    """Best-effort CAS status pointer; run records keep the real history."""
    try:
        attempt_id = _attempt_id(scope)
        for _ in range(3):
            snapshot = store.port.read([attempt_id])
            doc = snapshot.documents[attempt_id]
            value = dict(doc.value) if doc else {"schema_version": ATTEMPT_VERSION,
                                                 "scope": dict(scope), "last_success_at": None,
                                                 "last_run_id": None}
            value.update(last_attempt_at=snapshot.read_time, outcome=outcome,
                         reason=reason or None)
            if outcome == "complete":
                value.update(last_success_at=snapshot.read_time, last_run_id=run_id)
            try:
                store.port.commit({attempt_id: (value, doc.version if doc else None)})
                return
            except Conflict:
                continue
    except Exception:
        return


def _read_attempt(store, scope):
    try:
        doc = store.port.read([_attempt_id(scope)]).documents[_attempt_id(scope)]
        return dict(doc.value) if doc else None
    except Exception:
        return None


def _record_ids(task_id, run_id):
    return {name: key("record", {"task": task_id, "run": run_id, "name": name})
            for name in ("state", "events")}


def _read_run_records(store, task_id, run_id):
    ids = _record_ids(task_id, run_id)
    snapshot = store.port.read(list(ids.values()))
    docs = {name: snapshot.documents[record_id] for name, record_id in ids.items()}
    if any(doc is None for doc in docs.values()):
        raise StoreUnavailable("monitor_state_unavailable")
    return {name: doc.value["payload"] for name, doc in docs.items()}


def _previous(store, task):
    receipt = (task or {}).get("last_result")
    if not task or not receipt:
        return None, None
    records = _read_run_records(store, key("task", task["scope"]), receipt["run_id"])
    return records, receipt


def _summary(state, events, *, unchanged, attempt, schedule, declaration=None,
             deadline=None):
    files = state["files"]
    listed = []
    for file_id, record in sorted(files.items(), key=lambda kv: kv[1]["name"])[:MAX_LISTED]:
        match = record.get("match")
        assessed = bool(match)
        # ic_status_recorded repeats the workbook's own posting note; it is a
        # recorded claim, never proof of a verified gradebook readback.
        listed.append({"file_id": file_id, "name": record["name"], "status": record["status"],
                       "student_hint": record.get("student_hint"),
                       "student_name": (match or {}).get("student_name"),
                       "section_name": (match or {}).get("section_name"),
                       "timing": record["timing"], "modified_at": record["modified_at"],
                       "first_observed_at": record["first_observed_at"],
                       "student_id": match["student_id"] if match else None,
                       "section_id": match["section_id"] if match else None,
                       "ic_status_recorded": match["ic_posting_status"] if match else None,
                       "stage": ("assessed" if assessed and file_id not in
                                 state["awaiting_assessment"] else
                                 "awaiting_assessment" if assessed or
                                 file_id in state["awaiting_assessment"] else "detected")})
    candidate_students = {str(f.get("student_hint") or "").casefold()
                          for f in files.values()
                          if f["status"] == "present" and f.get("student_hint")}
    counts = {"present": sum(1 for f in files.values() if f["status"] == "present"),
              "candidate_students": len(candidate_students),
              "inaccessible": sum(1 for f in files.values() if f["status"] != "present"),
              "unmatched": sum(1 for f in files.values()
                               if not f.get("match") and f["status"] == "present"),
              "awaiting_assessment": len(state["awaiting_assessment"]),
              "needs_review": len(state["needs_review"])}
    return {"schema_version": STATUS_VERSION, "configured": True,
            "stream": state["stream"], "date": state["date"], "due_date": state["due_date"],
            "due_basis": state["due_basis"],
            "deadline": deadline if deadline is not None else state.get("deadline"),
            "topic_declaration": declaration,
            "observed_at": state["observed_at"],
            "baseline_established_at": state["baseline_established_at"],
            "scan_count": state["scan_count"], "coverage": state["coverage"],
            "roster": state["roster"], "counts": counts, "files": listed,
            "needs_review": state["needs_review"][:MAX_LISTED],
            "events_last_scan": events[:MAX_LISTED], "unchanged_rerun": unchanged,
            "last_attempt": attempt, "automation": schedule,
            "external_writes": 0, "run_enabled": False}


def _schedule(config):
    cadence = (config.get("HUB_MONITOR_SCHEDULE") or "").strip()
    return {"active": bool(cadence), "cadence": cadence or None,
            "basis": "configured_scheduler" if cadence else "manual_scans_only"}


def scan_and_persist(assertion, day, class_key, config):
    """One authenticated scan: complete read or a recorded failed attempt."""
    store = CloudState(configured_store(config))
    scope = None
    try:
        scan = _drive_scan(assertion, day, class_key, config)
        scope = assignment_scope(class_key, scan["assignment_label"])
        mappings = _mappings(assertion, class_key, config, scan.get("files") or [])
    except ConnectionError as error:
        if scope is None:
            try:
                _, _, assignment_label = _lesson(assertion, day, class_key, config)
                scope = assignment_scope(class_key, assignment_label)
            except Exception:
                scope = None
        if scope:
            _record_attempt(store, scope, "failed", str(error))
        raise
    task_id = store.create_task(scope)
    task = store.read_task(task_id)
    # The day's own Plan/Notes may declare a topic start, an intention or an
    # explicit deadline exception; record it before deriving the deadline, so
    # notes written directly in Sheets are honored too. "Today" is the scanned
    # dashboard date, never the wall clock.
    declaration = topic_starts.record_declarations(
        store, scope, day, scan.get("day_plan"), scan.get("day_notes"),
        source_kind="monitor_scan")
    calendar = topic_starts.read_week_calendar(assertion, config)
    deadline = topic_starts.deadline_status(assertion, scope, config, store,
                                            calendar=calendar)
    due_date, due_basis = deadline["due_date"], deadline["basis"]
    deadline_hash = content_hash({k: deadline.get(k) for k in
                                  ("status", "start_date", "due_date", "basis",
                                   "meetings", "provisional", "conflicts", "exception")})
    records_prior, receipt_prior = _previous(store, task)
    previous = records_prior["state"] if records_prior else None
    baseline = (task or {}).get("baseline") or {}
    checkpoint = baseline.get("checkpoint_token")
    scan_source_id = str(scan.get("source_id") or scan.get("folder_id") or "")
    previous_source_id = str(((previous or {}).get("stream") or {}).get("source_id")
                             or ((previous or {}).get("stream") or {}).get("folder_id")
                             or "")
    source_changed = bool(previous is not None and scan_source_id and previous_source_id
                          and scan_source_id != previous_source_id)
    fingerprint = content_hash({"files": sorted(
        (f["id"], f["modified_at"], f.get("version")) for f in scan["files"])})
    maps_hash = content_hash(_stable_mappings(mappings))
    if (previous is not None and not source_changed
            and baseline.get("source_revision") == fingerprint
            and previous.get("mappings_fingerprint") == maps_hash
            and previous.get("deadline_fingerprint") == deadline_hash
            and previous.get("derivation_version") == DERIVATION_VERSION):
        # Nothing observable changed and the derivation logic is current:
        # no new run, no new events, no duplicates.
        _record_attempt(store, scope, "complete", None, receipt_prior["run_id"])
        return _summary(previous, records_prior["events"], unchanged=True,
                        attempt=_read_attempt(store, scope), schedule=_schedule(config),
                        declaration=declaration)
    input_hash = content_hash({"scope": scope, "checkpoint": checkpoint,
                               "source": scan_source_id, "scan": fingerprint,
                               "due": due_date, "basis": due_basis,
                               "deadline": deadline_hash, "mappings": maps_hash,
                               "derivation": DERIVATION_VERSION})
    claim = store.claim(task_id, owner=OWNER, request_id="scan-" + input_hash[:32],
                        input_hash=input_hash, ttl_seconds=120)
    if claim.completed:
        records = _read_run_records(store, task_id, claim.run_id)
        _record_attempt(store, scope, "complete", None, claim.run_id)
        return _summary(records["state"], records["events"], unchanged=True,
                        attempt=_read_attempt(store, scope), schedule=_schedule(config),
                        declaration=declaration)
    try:
        # A different configured source is not evidence that previously unseen
        # files appeared after the old baseline. Rebaseline the observation
        # stream while retaining the assignment task, deadline declarations and
        # durable audit history.
        prior_for_advance = None if source_changed else previous
        result = advance_state(prior_for_advance, scan, mappings=mappings,
                               due_date=due_date, due_basis=due_basis, as_of=_now())
        result["state"]["mappings_fingerprint"] = maps_hash
        result["state"]["deadline"] = deadline
        result["state"]["deadline_fingerprint"] = deadline_hash
        baseline = {"scope": "submission-monitor:" + class_key + ":" + scope["assignment_key"],
                    "source_revision": fingerprint, "observed_at": scan["observed_at"],
                    "fingerprint": result["fingerprint"]}
        store.finish(claim, records={"state": result["state"], "events": result["events"]},
                     baseline=baseline, expected_checkpoint=checkpoint, complete_scan=True)
    except (Busy, Conflict, LeaseLost):
        _record_attempt(store, scope, "failed", "monitor_scan_conflict")
        raise
    except ValueError as error:
        try:
            store.release_for_review(claim, "needs_review")
        except Exception:
            pass
        _record_attempt(store, scope, "failed", str(error))
        raise ConnectionError("monitor_scan_invalid") from None
    _record_attempt(store, scope, "complete", None, claim.run_id)
    return _summary(result["state"], result["events"], unchanged=False,
                    attempt=_read_attempt(store, scope), schedule=_schedule(config),
                    declaration=declaration)


def _stable_mappings(mappings):
    return {"checked": "", "by_file": {k: [v["student_id"], v["assessed_marker"]]
                                       for k, v in sorted(mappings["by_file"].items())}}


def read_status(assertion, day, class_key, config):
    """Status read only: never scans, never writes observations."""
    _, _, assignment_label = _lesson(assertion, day, class_key, config)
    scope = assignment_scope(class_key, assignment_label)
    store = CloudState(configured_store(config))
    task = store.read_task(key("task", scope))
    attempt = _read_attempt(store, scope)
    schedule = _schedule(config)
    # A fresh calculated deadline for display; reading never records anything.
    try:
        deadline = topic_starts.deadline_status(assertion, scope, config, store)
    except Exception:
        deadline = {"status": "deadline_unavailable", "due_date": None,
                    "basis": "deadline_unavailable", "meetings": [],
                    "provisional": True, "conflicts": [],
                    "ic_alignment": "calculated_not_verified_in_ic"}
    if not task or not task.get("last_result"):
        return {"schema_version": STATUS_VERSION, "configured": True,
                "stream": {"class_key": class_key, "assignment_label": assignment_label},
                "status": "no_complete_scan_recorded", "counts": None, "files": [],
                "needs_review": [], "events_last_scan": [], "unchanged_rerun": False,
                "deadline": deadline, "topic_declaration": None,
                "last_attempt": attempt, "automation": schedule,
                "external_writes": 0, "run_enabled": False}
    records = _read_run_records(store, key("task", scope), task["last_result"]["run_id"])
    return _summary(records["state"], records["events"], unchanged=False, attempt=attempt,
                    schedule=schedule, deadline=deadline)
