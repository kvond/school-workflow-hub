"""Authenticated coordination for browser workers, never a school executor.

The cookie is an unguessable ownership secret. Public handles and worker reports
cannot prove school reads or writes; only the private state receipt is verified.
"""
import hashlib
import hmac
import json
import re
import secrets
from urllib.parse import parse_qs, urlsplit

from .firestore_state import configured_store
from .hosted_agenda import _identity_configuration, authorize
from .roster_sync import ROSTER_SYNC_SCOPE
from ..core.cloud_state import Claim, CloudState, Conflict, StoreUnavailable, instant, key
from ..core.runs import content_hash

COOKIE = "__Host-hub-worker"
WORKER_PATTERN = r"[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}"
SCOPES = {
    "roster": ROSTER_SYNC_SCOPE,
    "grading": {"workflow": "due-completion-browser", "class_key": "all-classes",
                "assignment_key": "2026-2027"},
}
HANDLE_FIELDS = {"task_id", "run_id", "fence", "input_hash", "expires_at"}
REASONS = {"source_unavailable", "partial_scan", "identity_ambiguous",
           "destination_changed", "needs_review", "cancelled"}
EVIDENCE_KINDS = {"source_read", "workbook_readback", "grade_readback", "review_note"}
MAX_BODY = 100_000
LEASE_SECONDS = 900


class WorkerForbidden(ValueError):
    pass


def _cookie(environ):
    raw = environ.get("HTTP_COOKIE", "")
    if not isinstance(raw, str) or len(raw) > 8192:
        raise WorkerForbidden("browser_worker_cookie_required")
    values = []
    for item in raw.split(";"):
        name, separator, value = item.strip().partition("=")
        if name == COOKIE:
            if not separator or not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
                raise WorkerForbidden("browser_worker_cookie_required")
            values.append(value)
    if len(values) > 1:
        raise WorkerForbidden("browser_worker_cookie_required")
    return values[0] if values else None


def _worker(value):
    if not isinstance(value, str) or not re.fullmatch(WORKER_PATTERN, value):
        raise ValueError("invalid_browser_worker")
    return value


def _owner(secret, worker):
    return "browser_" + hashlib.sha256((secret + ":" + worker).encode("ascii")).hexdigest()


def _origins(config):
    allowed = set()
    for value in config.get("HUB_STATE_PROBE_ORIGINS", "").split(","):
        value = value.strip()
        if not re.fullmatch(r"https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?", value):
            continue
        parsed = urlsplit(value)
        if parsed.hostname and (parsed.port is None or 1 <= parsed.port <= 65535):
            allowed.add(value)
    return allowed


def _same_origin(environ, config, *, required):
    origin = environ.get("HTTP_ORIGIN")
    if origin is None and not required:
        if environ.get("HTTP_SEC_FETCH_SITE") == "cross-site":
            raise WorkerForbidden("browser_worker_origin_required")
        return
    if (origin not in _origins(config)
            or urlsplit(origin).netloc != environ.get("HTTP_HOST")):
        raise WorkerForbidden("browser_worker_origin_required")


def _handle(claim):
    return {name: getattr(claim, name) for name in HANDLE_FIELDS}


def _receipt(receipt):
    return {"stored_at": receipt["stored_at"], "run_id": receipt["run_id"],
            "result_hash": receipt["result_hash"], "provenance": "browser_report",
            "server_verified": False}


def _last_report(store, task, workflow):
    receipt = (task or {}).get("last_result")
    record_id = (receipt or {}).get("record_ids", {}).get("browser_report")
    if not record_id:
        return None
    return _readback_receipt(store, receipt, workflow)


def _readback_receipt(store, receipt, workflow):
    if set(receipt.get("record_ids", {})) != {"browser_report"}:
        raise StoreUnavailable("browser_report_unavailable")
    record_id = receipt["record_ids"]["browser_report"]
    doc = store.port.read([record_id]).documents[record_id]
    if (doc is None or doc.value.get("run_id") != receipt["run_id"]
            or doc.value.get("payload", {}).get("provenance") != "browser_report"
            or doc.value["payload"].get("workflow") != workflow
            or doc.value["payload"].get("server_verified") is not False
            or content_hash({"records": {"browser_report": doc.value["payload"]},
                             "baseline": None, "expected_checkpoint": None}) != receipt["result_hash"]):
        raise StoreUnavailable("browser_report_unavailable")
    return _receipt(receipt)


def _status(store, workflow, owner):
    task_id = key("task", SCOPES[workflow])
    task = store.read_task(task_id)
    busy = bool(task and task.get("status") == "Working")
    owned = bool(busy and owner and hmac.compare_digest(task.get("owner", ""), owner))
    active = None
    if owned:
        run = store.port.read([task["active_run"]]).documents[task["active_run"]]
        if run is None:
            raise StoreUnavailable("browser_run_unavailable")
        active = _handle(_server_claim(task_id, task["active_run"], run.value, task))
    next_action = (task or {}).get("next_action")
    if next_action not in REASONS | {"expired_run_needs_recovery", "review_stored_result"}:
        next_action = None
    recovery = next_action in REASONS | {"expired_run_needs_recovery"}
    if busy and task.get("fence", 0) > 1:
        # A fence gap since the last finished run survives reload/reclaim and
        # means an earlier worker stopped without recording its possible effects.
        previous = (task.get("last_result") or {}).get("run_id")
        completed_fence = 0
        if previous:
            completed = store.port.read([previous]).documents[previous]
            if completed is None:
                raise StoreUnavailable("browser_run_unavailable")
            completed_fence = completed.value["fence"]
        if task["fence"] > completed_fence + 1:
            recovery, next_action = True, "expired_run_needs_recovery"
    return {"schema_version": "hub-browser-runs/v1", "workflow": workflow,
            "status": (task or {}).get("status", "Needs you"), "busy": busy,
            "owned_by_this_browser": owned, "active_claim": active,
            "last_report": _last_report(store, task, workflow),
            "next_action": next_action, "recovery_required": recovery,
            "run_enabled": False, "server_verified": False, "external_writes": 0}


def _server_claim(task_id, run_id, run, task):
    expires = task["expires_at"] if task.get("active_run") == run_id else run["expires_at"]
    return Claim(task_id, run_id, run["owner"], run["fence"], run["input_hash"],
                 expires, completed=bool(run.get("receipt")))


def _checked_claim(store, workflow, handle, owner):
    if not isinstance(handle, dict) or set(handle) != HANDLE_FIELDS:
        raise ValueError("invalid_browser_claim")
    task_id = key("task", SCOPES[workflow])
    if (handle["task_id"] != task_id or not isinstance(handle["run_id"], str)
            or not re.fullmatch(r"run_[a-f0-9]{64}", handle["run_id"])
            or not isinstance(handle["input_hash"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", handle["input_hash"])
            or type(handle["fence"]) is not int or handle["fence"] < 1
            or not isinstance(handle["expires_at"], str) or len(handle["expires_at"]) > 40):
        raise ValueError("invalid_browser_claim")
    instant(handle["expires_at"])
    snapshot = store.port.read([task_id, handle["run_id"]])
    task_doc, run_doc = snapshot.documents[task_id], snapshot.documents[handle["run_id"]]
    if task_doc is None or run_doc is None:
        raise Conflict("browser_claim_not_owned")
    run, task = run_doc.value, task_doc.value
    if (run.get("task_id") != task_id or not hmac.compare_digest(run.get("owner", ""), owner)
            or run.get("fence") != handle["fence"] or run.get("input_hash") != handle["input_hash"]):
        raise Conflict("browser_claim_not_owned")
    # Caller expiry never controls ownership. Renewal can have committed while
    # its response was lost, so reconstruct from authoritative server state.
    return _server_claim(task_id, handle["run_id"], run, task)


def _report(value, workflow):
    if (not isinstance(value, dict)
            or set(value) != {"workflow", "outcome", "summary", "counts", "evidence"}
            or value["workflow"] != workflow
            or value["outcome"] not in {"checked", "needs_review", "posted"}
            or not isinstance(value["summary"], str) or not 1 <= len(value["summary"]) <= 2000
            or not isinstance(value["counts"], dict)
            or set(value["counts"]) != {"checked", "changed", "needs_review"}
            or any(type(n) is not int or not 0 <= n <= 10000 for n in value["counts"].values())
            or not isinstance(value["evidence"], list) or len(value["evidence"]) > 30):
        raise ValueError("invalid_browser_report")
    for evidence in value["evidence"]:
        if (not isinstance(evidence, dict) or set(evidence) != {"kind", "reference", "checked_at"}
                or evidence["kind"] not in EVIDENCE_KINDS
                or not isinstance(evidence["reference"], str) or not 1 <= len(evidence["reference"]) <= 1000
                or not isinstance(evidence["checked_at"], str) or len(evidence["checked_at"]) > 40):
            raise ValueError("invalid_browser_report")
        instant(evidence["checked_at"])
    expected_readback = "workbook_readback" if workflow == "roster" else "grade_readback"
    if value["outcome"] == "posted" and (value["counts"]["changed"] == 0 or
            not any(e["kind"] == expected_readback for e in value["evidence"])):
        raise ValueError("invalid_browser_report")
    # Do not attach a new timestamp: an uncertain finish must retry exactly the
    # same payload. This is an attributed report, not trusted school evidence.
    return {**value, "schema_version": "hub-browser-report/v1",
            "provenance": "browser_report", "server_verified": False}


def _object(pairs):
    value = {}
    for name, item in pairs:
        if name in value:
            raise ValueError("duplicate_json_field")
        value[name] = item
    return value


def handle_request(environ, config):
    audience, allowed = _identity_configuration(config)
    authorize(environ.get("HTTP_X_GOOG_IAP_JWT_ASSERTION"), audience, allowed)
    method = environ.get("REQUEST_METHOD", "GET")
    secret = _cookie(environ)
    if method == "GET":
        _same_origin(environ, config, required=False)
        query = environ.get("QUERY_STRING", "")
        if len(query) > 160:
            raise ValueError("invalid_browser_query")
        params = parse_qs(query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
        if (set(params) != {"workflow", "worker_id"} or any(len(v) != 1 for v in params.values())
                or params["workflow"][0] not in SCOPES):
            raise ValueError("invalid_browser_query")
        worker = _worker(params["worker_id"][0])
        headers = []
        if secret is None:
            secret = secrets.token_urlsafe(32)
            headers = [("Set-Cookie", f"{COOKIE}={secret}; Path=/; Secure; HttpOnly; SameSite=Strict")]
        result = _status(CloudState(configured_store(config)), params["workflow"][0], _owner(secret, worker))
        return result, headers
    if method != "POST":
        raise ValueError("invalid_browser_method")
    _same_origin(environ, config, required=True)
    if not secret:
        raise WorkerForbidden("browser_worker_cookie_required")
    if environ.get("CONTENT_TYPE") != "application/json" or environ.get("QUERY_STRING", ""):
        raise WorkerForbidden("browser_worker_origin_required")
    length = int(environ.get("CONTENT_LENGTH", "0"))
    if not 1 <= length <= MAX_BODY:
        raise ValueError("invalid_browser_body")
    raw = environ["wsgi.input"].read(length)
    if len(raw) != length:
        raise ValueError("invalid_browser_body")
    payload = json.loads(raw, object_pairs_hook=_object, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if not isinstance(payload, dict) or not isinstance(payload.get("workflow"), str) or payload["workflow"] not in SCOPES:
        raise ValueError("invalid_browser_workflow")
    workflow, action = payload["workflow"], payload.get("action")
    fields = {"claim": {"request_id"}, "renew": {"claim"}, "finish": {"claim", "report"},
              "release": {"claim", "reason"}}
    if not isinstance(action, str) or action not in fields or set(payload) != {"workflow", "action", "worker_id"} | fields[action]:
        raise ValueError("invalid_browser_action")
    owner = _owner(secret, _worker(payload["worker_id"]))
    store = CloudState(configured_store(config))
    if action == "claim":
        request = payload["request_id"]
        if not isinstance(request, str) or not re.fullmatch(r"[A-Za-z0-9-]{16,64}", request):
            raise ValueError("invalid_browser_request_id")
        task_id = store.create_task(SCOPES[workflow])
        claim = store.claim(task_id, owner=owner, request_id=request,
                            input_hash=content_hash({"workflow": workflow, "request_id": request}),
                            ttl_seconds=LEASE_SECONDS)
        # CloudState's completed-result path precedes its owner check.
        if not hmac.compare_digest(claim.owner, owner):
            raise Conflict("browser_claim_not_owned")
        result = _status(store, workflow, owner)
        result.update(claim=_handle(claim), completed=claim.completed)
        if claim.completed:
            run = store.port.read([claim.run_id]).documents[claim.run_id]
            if run is None or not run.value.get("receipt"):
                raise StoreUnavailable("browser_report_unavailable")
            result["receipt"] = _readback_receipt(store, run.value["receipt"], workflow)
        return result, []
    claim = _checked_claim(store, workflow, payload["claim"], owner)
    if action == "renew":
        claim = store.renew(claim, LEASE_SECONDS)
        result = _status(store, workflow, owner)
        result.update(claim=_handle(claim), completed=False)
    elif action == "release":
        if payload["reason"] not in REASONS:
            raise ValueError("invalid_browser_release")
        store.release_for_review(claim, payload["reason"])
        result = _status(store, workflow, owner)
    else:
        report = _report(payload["report"], workflow)
        receipt = store.finish(claim, records={"browser_report": report})
        result = _status(store, workflow, owner)
        result.update(receipt=_receipt(receipt), completed=True)
    return result, []
