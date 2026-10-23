"""One-time canonical roster tabs in the existing workbook; no activation.

Original tabs remain untouched and unlinked. A trusted complete native reader,
current private configuration reader and actual shared ownership guard are
required. One durable fenced intent precedes one native batch; uncertainty is
read-only reconciliation, never a retry or inferred name/email crosswalk.
"""
from copy import deepcopy
from datetime import timedelta
import math
import re
from time import monotonic
from urllib.parse import quote

from hub_v2 import google
from . import roster_worker as rw
from .roster_matching import CLASS_TABS, _mapping, _tab_mapping
from .roster_only import configured_targets
from .roster_sync import ROSTER_SYNC_SCOPE
from ..core.cloud_state import Claim, CloudState, Conflict, StoreUnavailable, bounded_json, instant, key
from ..core.runs import content_hash

CONFIG_KEYS = ("HUB_ROSTER_SPREADSHEET_ID", "HUB_ROSTER_TABS", "HUB_ROSTER_SECTION_IDS",
               "HUB_ROSTER_STATUS_COLUMNS", "HUB_ROSTER_COLUMN_LAYOUTS", "HUB_ROSTER_ONLY_SECTIONS")
INSTRUCTIONAL = {"first": 1, "last": 2, "status": 3, "student_id": 4,
                 "membership_id": 5, "start_date": 6, "end_date": 7, "checked_at": 8}
STUDY = {"name": 1, "status": 2, "start_date": 3, "end_date": 4, "checked_at": 5,
         "student_id": 6, "membership_id": 7, "first": 8, "last": 9}
CELL_FIELDS = "userEnteredValue,userEnteredFormat,note,textFormatRuns,dataValidation"
SHEET_FIELDS = "properties,merges,conditionalFormats,basicFilter,filterViews,protectedRanges,rowGroups,columnGroups"
MAX_SHEETS, MAX_GRID_CELLS = 30, 300_000


def fail(reason):
    rw._fail("bootstrap_" + reason)


def basis(config):
    if not isinstance(config, dict):
        fail("configuration_unavailable")
    result = {name: deepcopy(config.get(name, "")) for name in CONFIG_KEYS}
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", result[CONFIG_KEYS[0]]):
        fail("workbook_unconfigured")
    return result


def targets_valid(targets, titles):
    result = rw.validate_targets(targets)
    academic = {(cls, name) for cls, names in CLASS_TABS.items() for name in names}
    if (len(result) != 5 or {(t["class_key"], t["section_name"]) for t in result if t["class_key"] != "study_hall"} != academic
            or sum(t["class_key"] == "study_hall" for t in result) != 1
            or not isinstance(titles, dict) or set(titles) != {str(t["sheet_id"]) for t in result}
            or len(set(titles.values())) != 5):
        fail("all_five_targets_required")
    for target in result:
        cols = STUDY if target["class_key"] == "study_hall" else INSTRUCTIONAL
        title = titles[str(target["sheet_id"])]
        if (target.get("identity_scheme") != rw.REPORT_SCHEME or target["columns"] != cols
                or target["row_limit"] != 200 or target["column_limit"] != len(cols)
                or not isinstance(title, str) or not title.strip() or len(title) > 100
                or any(c in title for c in "[]:*?/\\\r\n")):
            fail("canonical_target_invalid")
    return result


def bookends(before, after, targets, now):
    first, last = (rw.validate_capture(value, targets, now) for value in (before, after))
    if rw.source_fingerprint(first) != rw.source_fingerprint(last):
        fail("native_source_changed")
    a, b = first["report_proof"], last["report_proof"]
    if a["capture_id"] == b["capture_id"] or instant(b["captured_at"]) <= instant(a["observed_at"]):
        fail("independent_native_bookends_required")
    return first, last


def _new_rows(capture, targets):
    baseline = {}
    for target in targets:
        rows = [[{} for _ in range(target["column_limit"])] for _ in range(200)]
        for field, column in target["columns"].items():
            rows[0][column-1] = {"stringValue": rw.owned_headers(target)[field]}
        baseline[str(target["sheet_id"])] = rows
    plan = rw.plan_mirror(capture, targets, baseline)
    return plan["after"], plan["counts"]


def _project_destination(plan):
    projected = deepcopy(plan["original"])
    for target in plan["targets"]:
        sid = str(target["sheet_id"])
        projected["sheets"][sid] = {"properties": {
            "sheetId": target["sheet_id"], "title": plan["titles"][sid], "sheetType": "GRID",
            "gridProperties": {"rowCount": 200, "columnCount": target["column_limit"]}},
            "cells": {f"{r}:{c}": {"userEnteredValue": value}
                      for r, row in enumerate(plan["canonical_rows"][sid]) for c, value in enumerate(row) if value}}
    if (len(projected["sheets"]) > MAX_SHEETS or sum(
            s["properties"]["gridProperties"]["rowCount"] * s["properties"]["gridProperties"]["columnCount"]
            for s in projected["sheets"].values()) > MAX_GRID_CELLS):
        fail("projected_workbook_bounds_unverified")
    bounded_json(projected)
    return projected


def prepare_plan(first, second, *, config, original, targets, titles, now):
    targets = targets_valid(targets, titles)
    config = basis(config)
    first, second = bookends(first, second, targets, now)
    if original.get("workbook") != config[CONFIG_KEYS[0]] or not isinstance(original.get("sheets"), dict):
        fail("original_snapshot_unverified")
    metadata = {"sheets": [{"properties": v["properties"]} for v in original["sheets"].values()]}
    old_tabs = _tab_mapping(metadata, config)
    properties = {p["properties"]["title"]: p["properties"] for p in original["sheets"].values()}
    section_ids, study = _mapping(config, "HUB_ROSTER_SECTION_IDS"), configured_targets(config)
    if len(study) != 1:
        fail("study_hall_scope_unverified")
    legacy = []
    updates = {"HUB_ROSTER_TABS": {}, "HUB_ROSTER_STATUS_COLUMNS": {}, "HUB_ROSTER_COLUMN_LAYOUTS": {},
               "HUB_ROSTER_ONLY_SECTIONS": deepcopy(_mapping(config, "HUB_ROSTER_ONLY_SECTIONS"))}
    for target in targets:
        sid, name = str(target["sheet_id"]), target["section_name"]
        if sid in original["sheets"] or titles[sid] in properties:
            fail("destination_already_exists")
        if target["class_key"] == "study_hall":
            prior = study.get(name)
            if not prior or any(prior[f] != target[f] for f in ("section_id", "school_year")):
                fail("native_section_mapping_changed")
            old_id = str(prior["tab_id"])
            updates["HUB_ROSTER_ONLY_SECTIONS"][name]["tab_id"] = target["sheet_id"]
        else:
            if section_ids.get(name) != target["section_id"]:
                fail("native_section_mapping_changed")
            old_id = str(properties[old_tabs[name]]["sheetId"])
            updates["HUB_ROSTER_TABS"][name] = target["sheet_id"]
            updates["HUB_ROSTER_STATUS_COLUMNS"][name] = 3
            updates["HUB_ROSTER_COLUMN_LAYOUTS"][name] = "first_last_columns"
        if old_id not in original["sheets"]:
            fail("legacy_scope_unavailable")
        legacy.append({"section_name": name, "sheet_id": int(old_id), "status": "legacy_unlinked"})
    rows, counts = _new_rows(second, targets)
    proposal = {"schema": "roster-bootstrap/v1", "config_basis": config, "original": deepcopy(original),
                "targets": targets, "titles": deepcopy(titles), "source_before": first, "source_after": second,
                "canonical_rows": rows, "counts": counts, "legacy": legacy, "proposed_config_updates": updates}
    _project_destination(proposal)
    bounded_json(proposal)
    return proposal


class SheetsBootstrap:
    """Concrete native reader and ONE atomic addSheet+initial cells request."""
    def __init__(self, config):
        self.config, self.workbook = config, basis(config)[CONFIG_KEYS[0]]

    def read(self, token=None):
        token = token or google.service_token(self.config)
        url = "https://sheets.googleapis.com/v4/spreadsheets/" + quote(self.workbook)
        metadata = google.get_json(url, token, {"fields": "spreadsheetId,sheets(properties)"}, timeout=20)
        sheets = metadata.get("sheets", [])
        if metadata.get("spreadsheetId") != self.workbook or not 1 <= len(sheets) <= MAX_SHEETS:
            fail("workbook_coverage_unverified")
        total = 0
        for sheet in sheets:
            props = sheet.get("properties", {})
            grid = props.get("gridProperties", {})
            if (props.get("sheetType", "GRID") != "GRID" or type(props.get("sheetId")) is not int
                    or type(grid.get("rowCount")) is not int or not 1 <= grid["rowCount"] <= 2000
                    or type(grid.get("columnCount")) is not int or not 1 <= grid["columnCount"] <= 52):
                fail("workbook_bounds_unverified")
            total += grid["rowCount"] * grid["columnCount"]
        if total > MAX_GRID_CELLS:
            fail("workbook_bounds_unverified")
        data = google.get_json(url, token, {"includeGridData": "true",
            "fields": "spreadsheetId,sheets(" + SHEET_FIELDS + ",data(startRow,startColumn,rowData(values(" + CELL_FIELDS + "))))"}, timeout=20)
        if data.get("spreadsheetId") != self.workbook:
            fail("workbook_identity_changed")
        result = {}
        expected = {s["properties"]["sheetId"]: s["properties"] for s in sheets}
        for sheet in data.get("sheets", []):
            props = sheet.get("properties", {})
            sid = props.get("sheetId")
            if sid not in expected or props != expected[sid] or str(sid) in result:
                fail("workbook_metadata_changed")
            cells, visited = {}, set()
            for block in sheet.get("data", []):
                for r, row in enumerate(block.get("rowData", []), block.get("startRow", 0)):
                    for c, cell in enumerate(row.get("values", []), block.get("startColumn", 0)):
                        if ((r, c) in visited or not 0 <= r < props["gridProperties"]["rowCount"]
                                or not 0 <= c < props["gridProperties"]["columnCount"]):
                            fail("workbook_cells_unverified")
                        visited.add((r, c))
                        normalized = {k: deepcopy(v) for k, v in cell.items() if v not in ({}, [], None)}
                        if normalized:
                            cells[f"{r}:{c}"] = normalized
            result[str(sid)] = {k: deepcopy(v) for k, v in sheet.items() if k != "data"}
            result[str(sid)]["cells"] = cells
        if set(result) != {str(sid) for sid in expected}:
            fail("workbook_coverage_unverified")
        snapshot = {"workbook": self.workbook, "sheets": result}
        bounded_json(snapshot)
        return snapshot

    def apply(self, plan, before_write):
        read_token = google.service_token(self.config)
        write_token = google.service_token(self.config, write=True)
        if self.read(read_token) != plan["original"]:
            fail("original_changed")
        requests = []
        for target in plan["targets"]:
            sid = target["sheet_id"]
            requests.append({"addSheet": {"properties": {"sheetId": sid, "title": plan["titles"][str(sid)],
                "gridProperties": {"rowCount": 200, "columnCount": target["column_limit"]}}}})
            values = [[{"userEnteredValue": cell} if cell else {} for cell in row]
                      for row in plan["canonical_rows"][str(sid)]]
            requests.append({"updateCells": {"start": {"sheetId": sid, "rowIndex": 0, "columnIndex": 0},
                "rows": [{"values": row} for row in values], "fields": "userEnteredValue"}})
        before_write(required_seconds=rw.NATIVE_WRITE_READBACK_SECONDS)
        google.batch_update(write_token, self.workbook, requests)
        return self.read(read_token)


def verify_destination(plan, actual):
    if actual.get("workbook") != plan["original"]["workbook"]:
        fail("readback_identity_mismatch")
    original, current = plan["original"]["sheets"], actual.get("sheets", {})
    if set(current) != set(original) | set(plan["canonical_rows"]):
        fail("readback_scope_mismatch")
    if any(current[sid] != value for sid, value in original.items()):
        fail("legacy_preservation_unconfirmed")
    for target in plan["targets"]:
        sid = str(target["sheet_id"])
        sheet = current[sid]
        props = sheet.get("properties", {})
        if (props.get("sheetId") != target["sheet_id"] or props.get("title") != plan["titles"][sid]
                or props.get("gridProperties", {}).get("rowCount") != 200
                or props.get("gridProperties", {}).get("columnCount") != target["column_limit"]
                or props.get("sheetType", "GRID") != "GRID" or set(sheet) != {"properties", "cells"}):
            fail("canonical_metadata_unverified")
        expected = {f"{r}:{c}": {"userEnteredValue": value} for r, row in enumerate(plan["canonical_rows"][sid])
                    for c, value in enumerate(row) if value}
        if sheet["cells"] != expected:
            fail("canonical_readback_mismatch")
    return True


def _record(plan, destination_sha256):
    return {"plan": plan, "destination_sha256": destination_sha256,
            "activation": "pending_coordinator_runtime_map_update"}


def _result(intent, receipt):
    return {"status": "verified_not_activated", "counts": intent["plan"]["counts"],
            "receipt": receipt, "proposed_config_updates": intent["plan"]["proposed_config_updates"],
            "targets": intent["plan"]["targets"], "legacy_status": "legacy_unlinked"}


def _check_state_size(intent):
    """Prove all eventual envelopes fit before committing the first intent.

    Hash lengths and record IDs are exact. The timestamp slot deliberately
    exceeds any supported server ISO timestamp; no evidence is truncated.
    """
    claim = Claim(**intent["claim"])
    payload = _record(intent["plan"], "f" * 64)
    bounded_json(payload)
    bounded_json({"scope": ROSTER_SYNC_SCOPE, "run_id": claim.run_id,
                  "name": "roster_bootstrap", "payload": payload})
    receipt = {"run_id": claim.run_id, "result_hash": "f" * 64,
               "record_ids": {"roster_bootstrap": key("record", {
                   "task": claim.task_id, "run": claim.run_id, "name": "roster_bootstrap"})},
               "stored_at": "x" * 64, "checkpoint_token": None}
    bounded_json({**intent, "state": "verified", "result": _result(intent, receipt)})


class BootstrapWorker:
    def __init__(self, port, reader, mirror, *, read_config, targets, titles, before_write):
        if not all(callable(v) for v in (getattr(reader, "read_complete", None), read_config, before_write,
                                        getattr(port, "commit_guarded", None))):
            fail("trusted_capabilities_required")
        self.port, self.cloud, self.reader, self.mirror = port, CloudState(port), reader, mirror
        self.targets, self.titles = targets_valid(targets, titles), deepcopy(titles)
        self.read_config, self.before_write, self.claim = read_config, before_write, None
        self.task_id = key("task", ROSTER_SYNC_SCOPE)
        self.intent_key = key("roster-bootstrap-intent", ROSTER_SYNC_SCOPE)
        self.sync_intent_key = key("roster-intent", ROSTER_SYNC_SCOPE)

    def prepare(self):
        config = basis(self.read_config())
        first = self.reader.read_complete(self.targets)
        original = self.mirror.read()
        second = self.reader.read_complete(self.targets)
        if basis(self.read_config()) != config or self.mirror.read() != original:
            fail("preview_basis_changed")
        now = instant(self.port.read([self.task_id]).read_time)
        plan = prepare_plan(first, second, config=config, original=original,
                            targets=self.targets, titles=self.titles, now=now)
        return {"plan": plan, "sha256": content_hash(plan)}

    def _owned(self, seconds, *, finished=False):
        self.before_write(required_seconds=seconds + rw.FINAL_ROSTER_CHECK_SECONDS)
        started = monotonic()
        snapshot = self.port.read([self.task_id])
        elapsed = math.ceil(max(0, monotonic() - started))
        doc = snapshot.documents[self.task_id]
        now = instant(snapshot.read_time) + timedelta(seconds=elapsed)
        if elapsed > rw.FINAL_ROSTER_CHECK_SECONDS or doc is None:
            fail("owner_read_unavailable")
        if finished:
            if (doc.value.get("active_run") is not None or doc.value.get("owner") != self.claim.owner
                    or doc.value.get("fence") != self.claim.fence
                    or doc.value.get("last_result", {}).get("run_id") != self.claim.run_id):
                fail("completed_owner_changed")
        else:
            CloudState._owns(self.claim, doc.value, now.isoformat())
        if (instant(doc.value["expires_at"]) - now).total_seconds() < seconds:
            fail("lease_too_short")
        return doc

    def _native_guard(self, *, required_seconds):
        self.claim = self.cloud.renew(self.claim, ttl_seconds=300)
        self._owned(required_seconds)

    def apply(self, proposal, *, expected_sha256, owner, request_id):
        pending = self.port.read([self.intent_key, self.sync_intent_key, self.task_id])
        if pending.documents[self.intent_key]:
            fail("existing_intent_requires_reconciliation")
        other = pending.documents[self.sync_intent_key]
        if other and other.value.get("state") != "verified":
            fail("uncertain_roster_sync_preserved")
        task = pending.documents[self.task_id]
        if task and task.value.get("baseline") is not None:
            fail("existing_canonical_baseline_preserved")
        plan = proposal["plan"]
        if proposal.get("sha256") != expected_sha256 or content_hash(plan) != expected_sha256:
            fail("preview_hash_changed")
        current_config = basis(self.read_config())
        if current_config != plan["config_basis"] or self.mirror.read() != plan["original"]:
            fail("preview_basis_changed")
        rebuilt = prepare_plan(plan["source_before"], plan["source_after"], config=current_config,
                               original=plan["original"], targets=self.targets, titles=self.titles,
                               now=instant(self.port.read([self.task_id]).read_time))
        if rebuilt != plan:
            fail("preview_changed")
        self.before_write(required_seconds=60)
        self.cloud.create_task(ROSTER_SYNC_SCOPE)
        self.claim = self.cloud.claim(self.task_id, owner=owner, request_id=request_id,
                                      input_hash=expected_sha256, ttl_seconds=300)
        if self.claim.completed:
            fail("prior_bootstrap_requires_readback")
        state = self.port.read([self.intent_key, self.sync_intent_key, self.task_id])
        if (state.documents[self.intent_key] is not None or state.documents[self.sync_intent_key] != other
                or state.documents[self.task_id].value.get("baseline") is not None):
            fail("intent_basis_changed")
        task = state.documents[self.task_id]
        intent = {"state": "pending", "claim": self.claim.__dict__, "plan": plan, "sha256": expected_sha256,
                  "request_id": request_id}
        bounded_json(intent)
        _check_state_size(intent)
        marker = {"state": "pending", "kind": "bootstrap", "request_id": request_id,
                  "bootstrap_intent": self.intent_key, "claim": self.claim.__dict__}
        changes = {self.intent_key: (intent, None), self.task_id: (task.value, task.version),
                   self.sync_intent_key: (marker, other.version if other else None)}
        self.port.commit_guarded(changes, before_write=lambda: self._owned(60))
        try:
            actual = self.mirror.apply(plan, self._native_guard)
            verify_destination(plan, actual)
        except Exception:
            fail("uncertain_native_write_requires_readback")
        return self._finish(intent, actual)

    def reconcile(self):
        doc = self.port.read([self.intent_key]).documents[self.intent_key]
        if doc is None:
            fail("no_pending_intent")
        intent = doc.value
        actual = self.mirror.read()
        verify_destination(intent["plan"], actual)
        if intent["state"] == "verified":
            return deepcopy(intent["result"])
        self.claim = Claim(**intent["claim"])
        return self._finish(intent, actual)

    def _finish(self, intent, actual):
        verify_destination(intent["plan"], actual)
        if basis(self.read_config()) != intent["plan"]["config_basis"]:
            fail("configuration_changed_before_receipt")
        state = self.port.read([self.intent_key, self.sync_intent_key, self.claim.run_id])
        current, marker = state.documents[self.intent_key], state.documents[self.sync_intent_key]
        expected_marker = {"state": "pending", "kind": "bootstrap", "request_id": intent["request_id"],
                           "bootstrap_intent": self.intent_key, "claim": intent["claim"]}
        if marker is None or marker.value != expected_marker:
            fail("uncertainty_marker_changed")
        run = state.documents[self.claim.run_id]
        if run is None:
            fail("run_unavailable")
        if run.value.get("receipt") is None:
            self.claim = self.cloud.renew(self.claim, ttl_seconds=300)
        if current is None or current.value != intent:
            raise Conflict("roster_bootstrap_intent_changed")
        worker = self
        class GuardedData:
            def read(self, ids):
                return worker.port.read(ids)
            def commit(self, changes):
                return worker.port.commit_guarded(changes, before_write=lambda: worker._owned(60))
        receipt = CloudState(GuardedData()).finish(self.claim, records={"roster_bootstrap":
            _record(intent["plan"], content_hash(actual))})
        result = _result(intent, receipt)
        task = self._owned(60, finished=True)
        self.port.commit_guarded({self.intent_key: ({**intent, "state": "verified", "result": result}, current.version),
                                  self.task_id: (task.value, task.version),
                                  self.sync_intent_key: ({**expected_marker, "state": "verified", "receipt": receipt}, marker.version)},
                                 before_write=lambda: self._owned(60, finished=True))
        readback = self.port.read([self.intent_key, self.sync_intent_key])
        saved, gate = readback.documents[self.intent_key], readback.documents[self.sync_intent_key]
        if (not saved or saved.value.get("state") != "verified" or saved.value.get("result") != result
                or not gate or gate.value != {**expected_marker, "state": "verified", "receipt": receipt}):
            raise StoreUnavailable("roster_bootstrap_receipt_readback_failed")
        return result
