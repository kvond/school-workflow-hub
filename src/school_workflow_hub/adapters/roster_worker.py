"""Trusted-host roster synchronization; no HTTP route, IC login or scheduler.

The host injects an accepted complete IC reader and source-ownership guard.
The concrete Sheets mirror never deletes/reorders rows or touches unowned cells.
A durable intent precedes its one write; an uncertain write is read-only recovery.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math
import re
from time import monotonic
from urllib.parse import quote

from hub_v2 import google
from .hosted_agenda import ConnectionError
from .roster_matching import CLASS_TABS, _mapping
from .roster_status import configured_columns
from .roster_only import configured_targets as roster_only_targets
from .roster_sync import ROSTER_SYNC_SCOPE
from ..core.cloud_state import CloudState, Conflict, StoreUnavailable, bounded_json, instant, key
from ..core.runs import content_hash

FIELDS = {"last": "Last Name", "first": "First Name", "status": "Roster status",
          "student_id": "IC student ID", "membership_id": "IC enrollment ID",
          "start_date": "Start date", "end_date": "End date", "checked_at": "IC checked at"}
STATUSES = {"active", "incoming", "withdrawn", "inactive"}
SOURCE_STATUSES = STATUSES - {"inactive"}
NATIVE_WRITE_READBACK_SECONDS = 170  # one65s POST + two read-onlyGETs (at most50s each) + margin
FINAL_ROSTER_CHECK_SECONDS = 30
REPORT_SCHEME = "ic_roster_report_v1"
REPORT_MEMBER_PREFIX = "ic-report-membership:"


def _fail(code):
    raise ConnectionError("roster_sync_" + code)


def _text(value, *, empty=False):
    return (isinstance(value, str) and len(value) <= 200
            and (empty or bool(value.strip())) and not any(c in value for c in "\r\n\x00"))


def _id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9:_-]{1,100}", value)


def owned_headers(target):
    headers = {**FIELDS, **({"name": "Full name"} if target["class_key"] == "study_hall" else {})}
    if target.get("identity_scheme") == REPORT_SCHEME:
        headers.update(student_id="IC local student number", membership_id="IC membership key")
    return headers


def validate_targets(targets):
    if not isinstance(targets, list) or not 1 <= len(targets) <= 8:
        _fail("targets_invalid")
    sections, sheets = set(), set()
    for t in targets:
        if (not isinstance(t, dict) or set(t) - {"identity_scheme"} != {"class_key", "section_name", "section_id",
                "school_year", "sheet_id", "columns", "row_limit", "column_limit"}
                or ("identity_scheme" in t and t["identity_scheme"] != REPORT_SCHEME)
                or not _id(t["section_id"]) or not _text(t["section_name"])
                or not re.fullmatch(r"20\d{2}-\d{2}", str(t["school_year"]))
                or type(t["sheet_id"]) is not int or t["sheet_id"] < 0
                or type(t["row_limit"]) is not int or not 2 <= t["row_limit"] <= 200
                or type(t["column_limit"]) is not int or not 8 <= t["column_limit"] <= 26
                or not isinstance(t["columns"], dict)
                or set(t["columns"]) != set(owned_headers(t))
                or any(type(c) is not int or not 1 <= c <= t["column_limit"] for c in t["columns"].values())
                or len(set(t["columns"].values())) != len(owned_headers(t))
                or t["section_id"] in sections or t["sheet_id"] in sheets):
            _fail("targets_invalid")
        if t["class_key"] == "study_hall":
            if any(t["columns"][f] != c for f, c in {"name": 1, "status": 2, "start_date": 3,
                                                    "end_date": 4, "checked_at": 5}.items()):
                _fail("targets_invalid")
        else:
            if (t["section_name"] not in CLASS_TABS.get(t["class_key"], ())
                    or {t["columns"]["first"], t["columns"]["last"]} != {1, 2}
                    or t["columns"]["status"] > 25):
                _fail("targets_invalid")
        expected_year = ROSTER_SYNC_SCOPE["assignment_key"][:5] + ROSTER_SYNC_SCOPE["assignment_key"][-2:]
        if t["school_year"] != expected_year:
            _fail("targets_invalid")
        sections.add(t["section_id"])
        sheets.add(t["sheet_id"])
    return deepcopy(targets)


def validate_capture(capture, targets, now):
    """Validate every section before planning any departure; explicit ID scheme.

    Completeness/counts/revision must come from the accepted reader's original
    IC response, never a names-only workbook or an assistant-supplied claim.
    Pagination/filtered views cannot be relabeled a complete response.
    """
    report = all(t.get("identity_scheme") == REPORT_SCHEME for t in targets)
    if any(t.get("identity_scheme") == REPORT_SCHEME for t in targets) and not report:
        _fail("mixed_identity_schemes")
    if (not isinstance(capture, dict)
            or set(capture) != ({"source", "reader_id", "complete", "errors", "sections"}
                               | ({"report_proof"} if report else set()))
            or capture.get("source") != "infinite_campus"
            or capture.get("complete") is not True or capture.get("errors") != []
            or not _id(capture.get("reader_id")) or not isinstance(capture.get("sections"), list)):
        _fail("complete_capture_required")
    if report:
        from .roster_report import validate_proof
        validate_proof(capture["report_proof"], now)
    wanted = {t["section_id"]: t for t in targets}
    if len(capture["sections"]) != len(wanted):
        _fail("section_coverage_incomplete")
    seen, people, memberships = set(), {}, {}
    result = deepcopy(capture)
    for section in result["sections"]:
        if not isinstance(section, dict):
            _fail("capture_invalid")
        target = wanted.get(section.get("section_id"))
        if (set(section) != {"section_id", "school_year", "complete", "filtered", "next_page",
                             "revision", "checked_at", "rows", "expected_count"}
                or target is None or section["section_id"] in seen or section.get("complete") is not True
                or section.get("school_year") != target["school_year"]
                or section.get("filtered") is not False or section.get("next_page") is not None
                or not _id(section.get("revision")) or not isinstance(section.get("rows"), list)
                or type(section.get("expected_count")) is not int
                or section["expected_count"] != len(section["rows"]) or len(section["rows"]) > 199):
            _fail("section_coverage_incomplete")
        try:
            checked = instant(section.get("checked_at"))
        except ValueError:
            _fail("capture_time_invalid")
        if not timedelta(0) <= now - checked <= timedelta(minutes=15):
            _fail("capture_stale")
        seen.add(section["section_id"])
        student_ids = set()
        for row in section["rows"]:
            if (not isinstance(row, dict) or set(row) != set(FIELDS) - {"checked_at"}
                    or any(not _id(row.get(f)) for f in ("student_id", "membership_id"))
                    or any(not _text(row.get(f)) for f in ("first", "last"))
                    or row.get("status") not in SOURCE_STATUSES
                    or any(row.get(f) is not None and not _text(row.get(f), empty=True) for f in ("start_date", "end_date"))):
                _fail("identity_invalid")
            for field in ("start_date", "end_date"):
                if row[field]:
                    try:
                        if datetime.strptime(row[field], "%Y-%m-%d").date().isoformat() != row[field]:
                            raise ValueError()
                    except ValueError:
                        _fail("membership_date_invalid")
            student, member = row["student_id"], row["membership_id"]
            if report:
                from .roster_report import membership_key
                if (not re.fullmatch(r"[0-9]{1,20}", student)
                        or not re.fullmatch(REPORT_MEMBER_PREFIX + r"[0-9a-f]{64}", member)
                        or member != membership_key(capture["report_proof"]["context"], section["section_id"], student)
                        or capture["report_proof"]["context"]["school_year"] != section["school_year"]
                        or row["start_date"] is None or row["end_date"] is None):
                    _fail("report_identity_invalid")
            elif member.startswith(REPORT_MEMBER_PREFIX):
                _fail("report_identity_scheme_required")
            if student in student_ids or member in memberships:
                _fail("identity_conflict")
            student_ids.add(student)
            identity = (row["first"], row["last"])
            if student in people and people[student] != identity:
                _fail("identity_conflict")
            people[student] = identity
            memberships[member] = (student, section["section_id"])
        section["rows"].sort(key=lambda r: r["student_id"])
    result["sections"].sort(key=lambda s: s["section_id"])
    return result


def source_fingerprint(capture):
    copy = deepcopy(capture)
    for section in copy["sections"]:
        section.pop("checked_at", None)
    if "report_proof" in copy:
        for field in ("capture_id", "captured_at", "observed_at"):
            copy["report_proof"].pop(field, None)
    return content_hash(copy)


def _value(cell):
    if not isinstance(cell, dict) or set(cell) - {"stringValue", "numberValue", "boolValue", "formulaValue"}:
        _fail("mirror_invalid")
    if "formulaValue" in cell:
        _fail("owned_formula")
    if not cell:
        return ""
    if set(cell) != {"stringValue"} or not isinstance(cell["stringValue"], str):
        _fail("owned_cell_not_text")
    return cell["stringValue"]


def plan_mirror(capture, targets, baseline):
    """Keep each existing student's physical row and all unowned values/formulas.

    Existing named rows without native IDs require a separately verified initial
    crosswalk. This operation never guesses that crosswalk from matching names.
    """
    if set(baseline) != {str(t["sheet_id"]) for t in targets}:
        _fail("mirror_coverage_incomplete")
    after, changes, counts = deepcopy(baseline), [], {"active": 0, "incoming": 0, "withdrawn": 0,
                                                    "inactive": 0, "added": 0, "reactivated": 0,
                                                    "updated": 0, "unchanged": 0, "departed": 0}
    captures = {s["section_id"]: s for s in capture["sections"]}
    for target in targets:
        sid, cols = str(target["sheet_id"]), target["columns"]
        rows = baseline[sid]
        if (not isinstance(rows, list) or len(rows) != target["row_limit"]
                or any(not isinstance(r, list) or len(r) != target["column_limit"] for r in rows)):
            _fail("mirror_invalid")
        if any(_value(rows[0][cols[f]-1]).casefold() != header.casefold() for f, header in owned_headers(target).items()):
            _fail("owned_headers_required")
        existing, member_owner, empty = {}, {}, []
        for position, row in enumerate(rows[1:], 1):
            cells = {f: _value(row[c-1]) for f, c in cols.items()}
            if not any(cells.values()):
                # Even a blank name row may own attendance/formulas: never reuse it.
                if not any(row):
                    empty.append(position)
                continue
            if (not all(_id(cells[f]) for f in ("student_id", "membership_id"))
                    or not all(_text(cells[f]) for f in ("first", "last"))
                    or cells["status"] not in (STATUSES | ({"dropped"} if target["class_key"] == "study_hall" else set()))):
                _fail("verified_crosswalk_required")
            student, member = cells["student_id"], cells["membership_id"]
            if (member.startswith(REPORT_MEMBER_PREFIX)) != (target.get("identity_scheme") == REPORT_SCHEME):
                _fail("mirror_identity_scheme_mismatch")
            if student in existing or member in member_owner:
                _fail("mirror_identity_conflict")
            existing[student] = (position, cells)
            member_owner[member] = student
        section = captures[target["section_id"]]
        desired = {r["student_id"]: r for r in section["rows"]}
        for student in sorted(set(existing) | set(desired)):
            old = existing.get(student)
            row = deepcopy(desired.get(student) or old[1])
            if student not in desired:
                row["status"] = "inactive"
            if row["membership_id"] in member_owner and member_owner[row["membership_id"]] != student:
                _fail("mirror_identity_conflict")
            if old:
                position, before = old
                # A re-enrollment may get a new native membership ID; immutable
                # before/after receipts retain the older enrollment and its dates.
                if before["status"] != "active" and row["status"] == "active":
                    counts["reactivated"] += 1
            else:
                if not empty:
                    _fail("mirror_capacity_exhausted")
                position = empty.pop(0)
                counts["added"] += 1
            for field in ("start_date", "end_date"):
                if row[field] is None:
                    row[field] = old[1][field] if old and old[1]["membership_id"] == row["membership_id"] else ""
            if target["class_key"] == "study_hall":
                row["name"] = row["last"] + ", " + row["first"]
            row["checked_at"] = section["checked_at"]
            counts[row["status"]] += 1
            if target["class_key"] == "study_hall" and row["status"] == "withdrawn":
                row["status"] = "dropped"  # preserve the existing roster-only A:E contract
            if old:
                changed = any(old[1][f] != row[f] for f in cols if f != "checked_at")
                counts["updated" if changed else "unchanged"] += 1
                if old[1]["status"] == "active" and row["status"] in {"inactive", "withdrawn", "dropped"}:
                    counts["departed"] += 1
            for field, column in cols.items():
                value = {"stringValue": row[field]} if row[field] else {}
                if rows[position][column-1] != value:
                    changes.append({"sheet_id": target["sheet_id"], "row": position,
                                    "column": column-1, "value": value})
                    after[sid][position][column-1] = value
    return {"source_sha256": source_fingerprint(capture), "before": deepcopy(baseline),
            "after": after, "changes": changes, "counts": counts}


class SheetsRosterMirror:
    """Existing Google backend, exact stable tabs and narrow userEnteredValue writes.

    Sheets has no conditional cell CAS. The host must hold the shared source
    lease; fresh baseline and complete readback detect external teacher edits.
    Never describe these checks as a provider-enforced transaction with Firestore.
    """
    def __init__(self, config, targets):
        self.config, self.targets = config, validate_targets(targets)
        tabs = _mapping(config, "HUB_ROSTER_TABS")
        sections = _mapping(config, "HUB_ROSTER_SECTION_IDS")
        columns = configured_columns(config, [s for v in CLASS_TABS.values() for s in v])
        study_halls = roster_only_targets(config)
        for t in self.targets:
            if t["class_key"] == "study_hall":
                expected = study_halls.get(t["section_name"], {})
                if any(expected.get(f) != t[f] for f in ("section_id", "school_year")) or expected.get("tab_id") != t["sheet_id"]:
                    _fail("consumer_mapping_mismatch")
            elif (tabs.get(t["section_name"]) != t["sheet_id"]
                    or sections.get(t["section_name"]) != t["section_id"]
                    or columns.get(t["section_name"]) != t["columns"]["status"]):
                _fail("consumer_mapping_mismatch")
        self.workbook = config.get("HUB_ROSTER_SPREADSHEET_ID", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", self.workbook):
            _fail("workbook_unconfigured")

    def read(self, token=None):
        token = token or google.service_token(self.config)
        url = "https://sheets.googleapis.com/v4/spreadsheets/" + quote(self.workbook)
        metadata = google.get_json(url, token, {"fields": "spreadsheetId,sheets(properties(sheetId,title,gridProperties))"})
        if metadata.get("spreadsheetId") != self.workbook:
            _fail("mirror_identity_mismatch")
        tabs = {s["properties"]["sheetId"]: s["properties"] for s in metadata.get("sheets", [])}
        ranges = []
        for target in self.targets:
            props = tabs.get(target["sheet_id"], {})
            grid = props.get("gridProperties", {})
            if (not isinstance(props.get("title"), str) or grid.get("rowCount", 0) < target["row_limit"]
                    or grid.get("rowCount", 0) > 2000
                    or grid.get("columnCount", 0) != target["column_limit"]):
                _fail("mirror_target_unavailable")
            ranges.append("'%s'!A1:%s%d" % (props["title"].replace("'", "''"),
                          chr(64 + target["column_limit"]), grid["rowCount"]))
        params = [("fields", "spreadsheetId,sheets(properties(sheetId),data(startRow,startColumn,rowData(values(userEnteredValue))))")]
        params += [("ranges", value) for value in ranges]
        data = google.get_json(url, token, params)
        if data.get("spreadsheetId") != self.workbook:
            _fail("mirror_identity_mismatch")
        result, expected = {}, {t["sheet_id"]: t for t in self.targets}
        for sheet in data.get("sheets", []):
            sid = sheet.get("properties", {}).get("sheetId")
            if sid not in expected or str(sid) in result:
                _fail("mirror_coverage_incomplete")
            target = expected[sid]
            rows = [[{} for _ in range(target["column_limit"])] for _ in range(target["row_limit"])]
            occupied = set()
            for block in sheet.get("data", []):
                for r, row in enumerate(block.get("rowData", []), block.get("startRow", 0)):
                    for c, cell in enumerate(row.get("values", []), block.get("startColumn", 0)):
                        if ((r, c) in occupied or not 0 <= r < tabs[sid]["gridProperties"]["rowCount"]
                                or not 0 <= c < target["column_limit"]):
                            _fail("mirror_coverage_incomplete")
                        occupied.add((r, c))
                        value = deepcopy(cell.get("userEnteredValue", {}))
                        if r >= len(rows):
                            if c+1 in target["columns"].values() and value:
                                _fail("mirror_rows_outside_supported_range")
                            continue
                        rows[r][c] = value
            result[str(sid)] = rows
        if set(result) != {str(s) for s in expected}:
            _fail("mirror_coverage_incomplete")
        return result

    def apply(self, plan, before_write):
        if not callable(before_write):
            _fail("guard_required")
        allowed = {t["sheet_id"]: t for t in self.targets}
        seen = set()
        expected = deepcopy(plan["before"])
        for change in plan["changes"]:
            target = allowed.get(change.get("sheet_id"))
            cell = (change.get("sheet_id"), change.get("row"), change.get("column"))
            if (target is None or cell in seen or type(cell[1]) is not int
                    or not 1 <= cell[1] < target["row_limit"]
                    or cell[2] not in {c-1 for c in target["columns"].values()}):
                _fail("unowned_cell_write")
            _value(change["value"])
            seen.add(cell)
            expected[str(cell[0])][cell[1]][cell[2]] = change["value"]
        if expected != plan["after"]:
            _fail("mirror_plan_invalid")
        read_token = google.service_token(self.config)
        write_token = google.service_token(self.config, write=True)
        if self.read(token=read_token) != plan["before"]:
            _fail("destination_changed")
        requests = [{"updateCells": {"range": {"sheetId": c["sheet_id"], "startRowIndex": c["row"],
                    "endRowIndex": c["row"]+1, "startColumnIndex": c["column"], "endColumnIndex": c["column"]+1},
                    "rows": [{"values": [{"userEnteredValue": c["value"]}]}], "fields": "userEnteredValue"}}
                    for c in plan["changes"]]
        before_write(required_seconds=NATIVE_WRITE_READBACK_SECONDS)
        if requests:
            google.batch_update(write_token, self.workbook, requests)
        # Reuse the in-memory read token obtained before the reservation; no
        # credential refresh can extend the reserved native write/readback span.
        return self.read(token=read_token)


class RosterWorker:
    """One complete read, one fenced plan, one write, exact readback.

    reader.read_complete(targets) is an accepted host capability, not a browser
    report or a fixture relabeled live. No reader is configured by this module.
    The required before_write callback is the existing shared source-owner guard.
    """
    def __init__(self, port, reader, mirror, targets, *, before_write):
        if not callable(before_write) or not callable(getattr(reader, "read_complete", None)):
            _fail("accepted_reader_and_guard_required")
        self.port, self.state, self.reader, self.mirror = port, CloudState(port), reader, mirror
        self.targets, self.before_write = validate_targets(targets), before_write
        if isinstance(mirror, SheetsRosterMirror) and mirror.targets != self.targets:
            _fail("mirror_target_mismatch")
        self.intent_key = key("roster-intent", ROSTER_SYNC_SCOPE)

    def run(self, *, owner, request_id):
        state = self.port.read([self.intent_key])
        if state.documents[self.intent_key] and state.documents[self.intent_key].value.get("state") != "verified":
            _fail("uncertain_write_requires_readback")
        old = state.documents[self.intent_key]
        if old and old.value.get("request_id") == request_id:
            if old.value.get("claim", {}).get("owner") != owner:
                _fail("request_owner_conflict")
            return self.reconcile()
        raw_source = self.reader.read_complete(self.targets)
        now = instant(self.port.read([self.intent_key]).read_time)
        source = validate_capture(raw_source, self.targets, now)
        baseline = self.mirror.read()
        plan = plan_mirror(source, self.targets, baseline)
        task_id = self.state.create_task(ROSTER_SYNC_SCOPE)
        claim = self.state.claim(task_id, owner=owner, request_id=request_id,
                                 input_hash=content_hash({"source": source, "plan": plan}))
        if claim.completed:
            return {"status": "previously_verified", "external_writes": 0}
        try:
            self.before_write(required_seconds=NATIVE_WRITE_READBACK_SECONDS)
            fresh_capture = self.reader.read_complete(self.targets)
            fresh = validate_capture(fresh_capture, self.targets,
                                     instant(self.port.read([task_id]).read_time))
            if source_fingerprint(fresh) != plan["source_sha256"]:
                _fail("source_changed")
            if "report_proof" in source:
                before, after = source["report_proof"], fresh["report_proof"]
                if (before["capture_id"] == after["capture_id"]
                        or instant(after["captured_at"]) <= instant(before["observed_at"])):
                    _fail("independent_native_report_bookend_required")
            if self.mirror.read() != baseline:
                _fail("destination_changed")
            claim = self.state.renew(claim, ttl_seconds=300)
            self.before_write(required_seconds=NATIVE_WRITE_READBACK_SECONDS)
            current = self.port.read([self.intent_key, task_id])
            task = current.documents[task_id]
            CloudState._owns(claim, task.value, current.read_time)
            old = current.documents[self.intent_key]
            if old and old.value.get("state") != "verified":
                _fail("uncertain_write_requires_readback")
            checkpoint = task.value.get("baseline")
            scope = "roster_" + content_hash(self.targets)
            if checkpoint and (checkpoint["scope"] != scope or instant(current.read_time) <= instant(checkpoint["observed_at"])):
                _fail("baseline_scope_or_time_changed")
            baseline_record = {"scope": scope, "source_revision": plan["source_sha256"],
                               "observed_at": current.read_time, "fingerprint": content_hash(plan["after"])}
            intent = {"state": "pending", "request_id": request_id, "claim": claim.__dict__, "source": source,
                      "targets": self.targets, "plan": plan, "baseline": baseline_record,
                      "expected_checkpoint": (checkpoint or {}).get("checkpoint_token")}
            if "report_proof" in fresh:
                intent["source_bookend"] = fresh
            # Check private state size before any native action; an oversized
            # receipt must not first be discovered after the sheet was changed.
            bounded_json(intent)
            # Include the task CAS in the intent transaction so an expired owner
            # cannot publish a new write intent after another owner takes over.
            self.port.commit({self.intent_key: (intent, old.version if old else None),
                              task_id: (task.value, task.version)})
        except Exception:
            self.state.release_for_review(claim, "roster_source_or_baseline_unavailable")
            raise
        def before_native_write(*, required_seconds):
            # The shared activity check may be slow. Renewal BEFORE it is not
            # permission to write: recheck this exact roster owner afterward.
            self.state.renew(claim, ttl_seconds=max(300, required_seconds + 60))
            self.before_write(required_seconds=required_seconds + FINAL_ROSTER_CHECK_SECONDS)
            started = monotonic()
            final = self.port.read([claim.task_id])
            elapsed = math.ceil(max(0, monotonic() - started))
            task = final.documents[claim.task_id]
            if elapsed > FINAL_ROSTER_CHECK_SECONDS or task is None:
                _fail("final_ownership_check_unavailable")
            # Treat the entire response round trip as time AFTER the server
            # timestamp. This conservative estimate cannot stretch the lease.
            conservative_now = instant(final.read_time) + timedelta(seconds=elapsed)
            CloudState._owns(claim, task.value, conservative_now.isoformat())
            if (instant(task.value["expires_at"]) - conservative_now).total_seconds() < required_seconds:
                _fail("roster_lease_too_short")
            # Shared ownership/cutoff reserved the native budget PLUS this
            # bounded final check. No further I/O occurs before native POST.
        try:
            actual = self.mirror.apply(plan, before_native_write)
            if actual != plan["after"]:
                _fail("readback_mismatch")
        except Exception:
            # Never clear or retry: pending intent survives transport errors,
            # process death and lease expiry, including uncertain native saves.
            raise ConnectionError("roster_sync_uncertain_write_requires_readback") from None
        return self._finish(intent, actual)

    def reconcile(self):
        """Read an existing uncertain mirror, never repeat its write or clear it.

        A lost response is accepted only when the entire planned grid matches.
        An expired lease requires the coordinator to inspect ownership; no new
        claim, reapply or uncertainty reset is attempted here.
        """
        snap = self.port.read([self.intent_key])
        doc = snap.documents[self.intent_key]
        if not doc:
            _fail("no_pending_intent")
        actual = self.mirror.read()
        intent = doc.value
        if actual != intent["plan"]["after"]:
            _fail("uncertain_write_requires_readback")
        if intent["state"] == "verified":
            return deepcopy(intent["result"])
        return self._finish(intent, actual)

    def _finish(self, intent, actual):
        from ..core.cloud_state import Claim
        claim = Claim(**intent["claim"])
        snapshot = self.port.read([self.intent_key])
        doc = snapshot.documents[self.intent_key]
        if not doc or doc.value != intent:
            raise Conflict("roster_intent_changed")
        receipt = self.state.finish(claim, records={"roster_sync": {
            "source": intent["source"], "targets": intent["targets"],
            "before": intent["plan"]["before"], "after": actual,
            "counts": intent["plan"]["counts"], "source_sha256": intent["plan"]["source_sha256"],
            **({"source_bookend": intent["source_bookend"]} if "source_bookend" in intent else {})}},
            baseline=intent["baseline"], expected_checkpoint=intent["expected_checkpoint"], complete_scan=True)
        result = {"status": "verified", "counts": intent["plan"]["counts"],
                  "receipt": receipt, "external_writes": int(bool(intent["plan"]["changes"]))}
        self.port.commit({self.intent_key: ({**intent, "state": "verified", "result": result}, doc.version)})
        saved = self.port.read([self.intent_key]).documents[self.intent_key]
        if not saved or saved.value.get("result") != result or saved.value.get("state") != "verified":
            raise StoreUnavailable("roster_receipt_readback_failed")
        return result
