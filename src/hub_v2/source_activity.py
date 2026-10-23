"""Read-only trusted-host activity evidence for the shared source guard.

This module does not discover a browser, authorize maintenance, or write state.
Readers are injected by the coordinator from existing private native records.
A popup's visible day/outcome is correlated only with a UNIQUE fresh Hub run;
it is never represented as a natively observed run ID or grade confirmation.
"""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from time import monotonic
from zoneinfo import ZoneInfo

from school_workflow_hub.core.cloud_state import Claim, Conflict, instant
from .morning import validate_report

EASTERN = ZoneInfo("America/New_York")
CLAIM_ROLES = frozenset(("roster", "grading", "archive_biology", "archive_forensics", "archive_anatomy"))
TERMINAL = frozenset(("finished", "source_scores_held", "missed_noon"))
ITEM_TERMINAL = frozenset(("read_back", "nothing_to_enter", "no_scores", "mapping_required",
                           "coverage_incomplete", "excluded_week", "source_scores_held"))


class ActivityHold(Conflict):
    """Fixed reason plus private exact conflicting claim, never public logging."""
    def __init__(self, reason, **private_detail):
        self.source_operation_hold = reason
        self.source_operation_action = "Refresh the exact private ownership, helper and receipt evidence before further writes."
        self.private_detail = private_detail
        super().__init__(reason)


def _require(condition, reason):
    if not condition:
        raise ActivityHold(reason)


def i12_record(*, record_id, value, note):
    """Decode the existing I-12 cell note; hash the actual cell value AND note.

    The host freshly reads the row ID, raw userEnteredValue and note from the
    existing sheet. This never writes/replaces teacher prose. The note is a JSON
    object with a source_activity property; sibling properties are preserved.
    """
    _require(isinstance(note, str) and len(note.encode()) <= 250_000, "activity_record_unavailable")
    try:
        record = json.loads(note)["source_activity"]
        encoded = json.dumps([record_id, value, note], sort_keys=True, allow_nan=False).encode()
    except (KeyError, ValueError, TypeError):
        raise ActivityHold("activity_record_unavailable") from None
    _require(isinstance(record, dict) and record.get("record_id") == record_id, "activity_record_unavailable")
    return {"revision": hashlib.sha256(encoded).hexdigest(), "record": record}


def read_i12_cell(token, *, spreadsheet_id, tab, row, id_column=1, note_column=6):
    """Read the exact existing private Issue row through the normal Sheets API."""
    from urllib.parse import quote
    from . import google
    _require(type(row) is int and row > 0 and type(id_column) is int and type(note_column) is int
             and 1 <= id_column <= 26 and 1 <= note_column <= 26 and id_column != note_column,
             "activity_record_location_invalid")
    column = max(id_column, note_column)
    selected = "'" + tab.replace("'", "''") + "'!A%d:%s%d" % (row, chr(64 + column), row)
    raw = google.get_json("https://sheets.googleapis.com/v4/spreadsheets/" + quote(spreadsheet_id), token,
        {"ranges": selected, "includeGridData": "true",
         "fields": "spreadsheetId,sheets(properties(title),data(startRow,startColumn,rowData(values(userEnteredValue,note))))"},
        timeout=30)
    _require(raw.get("spreadsheetId") == spreadsheet_id and len(raw.get("sheets", [])) == 1,
             "activity_record_read_partial")
    sheet = raw["sheets"][0]
    blocks = sheet.get("data", [])
    _require(sheet.get("properties", {}).get("title") == tab and len(blocks) == 1,
             "activity_record_read_partial")
    block = blocks[0]
    rows = block.get("rowData", [])
    _require(block.get("startRow", 0) == row - 1 and block.get("startColumn", 0) == 0 and len(rows) == 1,
             "activity_record_read_partial")
    cells = rows[0].get("values", [])
    _require(len(cells) >= column and cells[id_column - 1].get("userEnteredValue") == {"stringValue": "I-12"},
             "activity_record_identity_changed")
    cell = cells[note_column - 1]
    return i12_record(record_id="I-12", value=cell.get("userEnteredValue"), note=cell.get("note"))


def read_morning_receipts(token, config):
    """Full bounded existing ledger history, projected by exact run identity."""
    from . import google, ledger, morning
    target = morning._report_target(token, config)
    _require(target is not None, "activity_receipt_coverage_unverified")
    sheet, tab = target
    rows = google.sheet_values(token, sheet, f"{tab}!A1:G{ledger.MAX_ROWS + 1}")
    _require(isinstance(rows, list) and len(rows) <= ledger.MAX_ROWS, "activity_receipt_coverage_unverified")
    groups = {}
    for row in rows:
        if len(row) < 2 or row[1] != morning.KIND:
            continue
        _require(len(row) >= 7, "activity_receipt_coverage_unverified")
        try:
            report = validate_report(json.loads(row[6]))
        except (TypeError, ValueError):
            raise ActivityHold("activity_receipt_coverage_unverified") from None
        values = groups.setdefault(report["run_id"], [])
        if report not in values:
            values.append(report)
    return {"complete": True, "reports": [morning._project_report(values) for values in groups.values()]}


class PostRunSourceActivity:
    """POST-RUN maintenance check_activity; not the normal before-07 reader.

    Use only with an independently verified SourceOperations maintenance grant.
    All reads repeat per call; this adapter itself never authorizes a window.

    known_claims maps the five role names above to actual existing task IDs.
    read_record returns i12_record(...); read_helper returns a fresh, attributed
    native popup observation; read_receipts returns a complete bounded current
    report history projected per run (not the dashboard's ten-run projection).
    owned_claims optionally returns actual Claim objects held by this executor;
    a matching owner label alone never exempts an active claim.
    """
    def __init__(self, *, port, known_claims, read_record, read_helper, read_receipts,
                 expected_revision, source_scope, owner, account, source_origin,
                 destination_origin, participants, maintenance_authorization, owned_claims=lambda: ()):
        _require(set(known_claims) == CLAIM_ROLES and len(set(known_claims.values())) == 5,
                 "activity_claim_scope_incomplete")
        _require(all(isinstance(v, str) and v for v in known_claims.values()), "activity_claim_scope_incomplete")
        _require(all(callable(v) for v in (read_record, read_helper, read_receipts, owned_claims)),
                 "activity_reader_unavailable")
        _require(isinstance(participants, dict) and list(participants.values()).count("code_read_only") == 3
                 and all(v in ("code_read_only", "deferred", "consulting_only") for v in participants.values())
                 and list(participants.values()).count("deferred") >= 2, "activity_participants_incomplete")
        _require(all(isinstance(v, str) and v for v in (expected_revision, source_scope, owner, account,
                                                       source_origin, destination_origin)), "activity_configuration_incomplete")
        _require(isinstance(maintenance_authorization, dict)
                 and maintenance_authorization.get("schema") == "school-source-maintenance/v1"
                 and maintenance_authorization.get("status") == "authorized"
                 and maintenance_authorization.get("source_scope") == source_scope
                 and maintenance_authorization.get("owner") == owner
                 and maintenance_authorization.get("authorization_revision") == expected_revision,
                 "activity_maintenance_binding_unverified")
        self.authority = {name: maintenance_authorization.get(key) for name, key in (
            ("authorization_ref", "authorization_ref"), ("authorized_from", "starts_at"),
            ("authorized_until", "expires_at"))}
        _require(all(isinstance(value, str) and value for value in self.authority.values()),
                 "activity_maintenance_binding_unverified")
        self.port, self.known_claims = port, dict(known_claims)
        self.read_record, self.read_helper, self.read_receipts = read_record, read_helper, read_receipts
        self.expected_revision, self.source_scope, self.owner = expected_revision, source_scope, owner
        self.account, self.source_origin, self.destination_origin = account, source_origin, destination_origin
        self.participants, self.owned_claims = dict(participants), owned_claims
        self.last_evidence = None

    def _record(self, value, now):
        _require(isinstance(value, dict) and value.get("revision") == self.expected_revision,
                 "activity_record_revision_changed")
        record = value.get("record", {})
        _require(record.get("schema") == "source-activity/v1" and record.get("record_id") == "I-12"
                 and record.get("source_scope") == self.source_scope and record.get("owner") == self.owner
                 and record.get("sole_live_executor") == self.owner, "activity_owner_changed")
        _require(all(record.get(k) == v for k, v in self.authority.items()), "activity_maintenance_binding_changed")
        _require(isinstance(record.get("authorization_ref"), str) and bool(record["authorization_ref"]) and instant(record["authorized_from"]) <= now
                 < instant(record["authorized_until"]), "activity_authorization_expired")
        owners = record.get("claim_owners")
        _require(isinstance(owners, dict) and set(owners) == CLAIM_ROLES
                 and all(isinstance(owner, str) and owner for owner in owners.values()),
                 "activity_claim_owners_unverified")
        people = record.get("participants")
        _require(isinstance(people, dict) and set(people) == set(self.participants), "activity_participants_changed")
        for name, mode in self.participants.items():
            person = people[name]
            _require(isinstance(person, dict) and person.get("mode") == mode
                     and isinstance(person.get("evidence_ref"), str) and bool(person["evidence_ref"])
                     and instant(person["valid_until"]) >= instant(record["authorized_until"]),
                     "activity_participant_deferral_unavailable")

    def _claims(self, snapshot, now, owned, claim_owners):
        _require(set(snapshot.documents) == set(self.known_claims.values()) | {c.run_id for c in owned},
                 "activity_claim_read_partial")
        roles = {task_id: role for role, task_id in self.known_claims.items()}
        _require(all(claim.owner == claim_owners[roles[claim.task_id]] for claim in owned),
                 "activity_owned_claims_owner_mismatch")
        historical = []
        for role, task_id in self.known_claims.items():
            doc = snapshot.documents[task_id]
            _require(doc is not None and isinstance(doc.value, dict) and doc.version,
                     "activity_claim_unavailable")
            value = doc.value
            _require(value.get("schema_version") == "hub-cloud-task/v1" and "active_run" in value,
                     "activity_claim_unverified")
            if value["active_run"] is None:
                continue
            expiry = instant(value["expires_at"])
            if expiry <= now:
                historical.append(role)
                continue  # Preserve the old task and its uncertainty verbatim.
            matching = [claim for claim in owned if isinstance(claim, Claim) and not claim.completed
                        and claim.task_id == task_id and claim.run_id == value["active_run"]
                        and claim.owner == claim_owners[role] and claim.owner == value.get("owner") and claim.fence == value.get("fence")
                        and instant(claim.expires_at) > now]
            if len(matching) != 1:
                raise ActivityHold("activity_foreign_claim_active", task_id=task_id,
                                   owner=value.get("owner"), expires_at=value["expires_at"])
            claim = matching[0]
            run = snapshot.documents.get(claim.run_id)
            _require(run is not None and run.value.get("task_id") == task_id
                     and run.value.get("owner") == claim.owner and run.value.get("fence") == claim.fence
                     and run.value.get("input_hash") == claim.input_hash and "receipt" in run.value
                     and run.value["receipt"] is None,
                     "activity_owned_run_unverified")
        return historical

    def _helper(self, helper, receipts, now):
        _require(isinstance(helper, dict) and helper.get("schema") == "native-helper-observation/v1",
                 "activity_helper_unavailable")
        observed = instant(helper["observed_at"])
        day = now.astimezone(EASTERN).date().isoformat()
        _require(0 <= (now - observed).total_seconds() <= 60
                 and observed.astimezone(EASTERN).hour >= 7, "activity_helper_observation_stale")
        _require(helper.get("observer") == self.owner and bool(helper.get("evidence_ref"))
                 and helper.get("account") == self.account and helper.get("source_origin") == self.source_origin
                 and helper.get("destination_origin") == self.destination_origin, "activity_helper_identity_unverified")
        _require(helper.get("busy") is False and helper.get("blocked") is False
                 and helper.get("ready") is True and helper.get("run_now_visible") is True
                 and helper.get("run_now_enabled") is True, "activity_helper_not_idle")
        _require(helper.get("last_attempt_day") == day and helper.get("last_attempt_code") in TERMINAL,
                 "activity_helper_today_not_settled")
        _require(isinstance(receipts, dict) and receipts.get("complete") is True
                 and isinstance(receipts.get("reports"), list), "activity_receipt_coverage_unverified")
        reports = receipts["reports"]
        _require(len(reports) <= 10_000, "activity_receipt_coverage_unverified")
        for report in reports:
            validate_report(report)
        _require(len({r["run_id"] for r in reports}) == len(reports), "activity_receipt_conflict")
        today = [r for r in reports if r["day"] == day]
        _require(all(r["code"] in TERMINAL for r in today), "activity_receipt_unsettled")
        matches = [r for r in today if r["code"] == helper["last_attempt_code"]]
        _require(len(matches) == 1, "activity_receipt_correlation_ambiguous")
        report = matches[0]
        _require(all(item["code"] in ITEM_TERMINAL for item in report["items"]), "activity_receipt_unsettled")
        _require(report["code"] != "missed_noon" or not report["items"], "activity_receipt_unsettled")
        return report

    def __call__(self):
        self.last_evidence = None
        try:
            owned = tuple(self.owned_claims())
            _require(len(owned) <= 5 and all(isinstance(c, Claim) and c.task_id in self.known_claims.values() for c in owned)
                     and len({c.task_id for c in owned}) == len(owned), "activity_owned_claims_invalid")
            ids = [*self.known_claims.values(), *(c.run_id for c in owned)]
            first_record = deepcopy(self.read_record())
            first_claims = self.port.read(ids)
            first_receipts = deepcopy(self.read_receipts())
            helper = deepcopy(self.read_helper())
            last_receipts = deepcopy(self.read_receipts())
            last_record = deepcopy(self.read_record())
            _require(tuple(self.owned_claims()) == owned, "activity_owned_claims_changed")
            started = monotonic()
            last_claims = self.port.read(ids)
            now = instant(last_claims.read_time) + timedelta(seconds=max(0, monotonic() - started))
            _require(0 <= (now - instant(first_claims.read_time)).total_seconds() <= 60,
                     "activity_read_window_stale")
            _require(first_record == last_record, "activity_record_changed_during_read")
            _require(first_receipts == last_receipts, "activity_receipt_changed_during_read")
            _require(first_claims.documents == last_claims.documents, "activity_claim_changed_during_read")
            self._record(last_record, now)
            owners = last_record["record"]["claim_owners"]
            self._claims(first_claims, instant(first_claims.read_time), owned, owners)
            historical = self._claims(last_claims, now, owned, owners)
            report = self._helper(helper, last_receipts, now)
            self.last_evidence = {"record_revision": self.expected_revision, "read_time": now.isoformat(),
                                  "historical_expired_claims": historical,
                                  "receipt_run_id": report["run_id"], "receipt_day": report["day"],
                                  "correlation_basis": "unique_day_and_outcome"}
        except ActivityHold:
            raise
        except Exception:
            raise ActivityHold("activity_evidence_unavailable") from None
        return None
