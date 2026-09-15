"""Cloud persistence of task ownership and immutable results; no school actions.

The cloud adapter is the sole hosted store. SQLite remains the offline ledger.
Every change uses compare-and-set versions, including the task's fencing counter.
Only trusted host workers call this API; browser JSON is not a source assessment.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re

from .runs import content_hash


class StoreUnavailable(RuntimeError):
    pass


class Conflict(RuntimeError):
    pass


class Busy(Conflict):
    pass


class LeaseLost(Conflict):
    pass


@dataclass(frozen=True)
class Document:
    value: dict
    version: str


@dataclass(frozen=True)
class Snapshot:
    documents: dict
    read_time: str


@dataclass(frozen=True)
class Claim:
    task_id: str
    run_id: str
    owner: str
    fence: int
    input_hash: str
    expires_at: str
    completed: bool = False


def instant(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise ValueError("invalid_state_timestamp") from None


def bounded_json(value):
    try:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                             separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 250_000:
            raise ValueError()
        return encoded
    except (ValueError, TypeError, RecursionError):
        raise ValueError("invalid_or_oversized_state_record") from None


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,160}", value):
        raise ValueError("invalid_state_identifier")
    return value


def key(kind, value):
    return kind + "_" + content_hash(value)


class CloudState:
    """Port requires strongly consistent reads and an atomic CAS commit.

    commit maps record IDs to (new JSON, expected version); None means absent.
    Server read times determine lease expiry, never a browser-supplied clock.
    No retry here can repeat a school action: these methods only mutate state.
    """
    def __init__(self, port):
        self.port = port

    def create_task(self, scope):
        if (not isinstance(scope, dict) or set(scope) != {"workflow", "class_key", "assignment_key"}):
            raise ValueError("invalid_task_scope")
        for value in scope.values():
            identifier(value)
        task_id = key("task", scope)
        for _ in range(4):
            snapshot = self.port.read([task_id])
            old = snapshot.documents[task_id]
            if old:
                if old.value.get("scope") != scope:
                    raise Conflict("task_scope_conflict")
                return task_id
            task = {"schema_version": "hub-cloud-task/v1", "scope": dict(scope),
                    "status": "Queued", "fence": 0, "active_run": None,
                    "baseline": None, "created_at": snapshot.read_time}
            try:
                self.port.commit({task_id: (task, None)})
                return task_id
            except Conflict:
                continue
        raise Conflict("task_creation_contention")

    def claim(self, task_id, *, owner, request_id, input_hash, ttl_seconds=120):
        for value in (task_id, owner, request_id, input_hash):
            identifier(value)
        if type(ttl_seconds) is not int or not 30 <= ttl_seconds <= 900:
            raise ValueError("invalid_lease_duration")
        run_id = key("run", {"task": task_id, "request": request_id})
        completed_id = key("completed", {"task": task_id, "input_hash": input_hash})
        for _ in range(4):
            snapshot = self.port.read([task_id, run_id, completed_id])
            doc, previous = snapshot.documents[task_id], snapshot.documents[run_id]
            if doc is None:
                raise Conflict("unknown_task")
            task = dict(doc.value)
            now = instant(snapshot.read_time)
            if previous:
                run = previous.value
                if run["input_hash"] != input_hash:
                    raise Conflict("idempotency_key_reused_with_different_input")
                if run.get("receipt"):
                    return self._claim(task_id, run_id, run, completed=True)
                if run["owner"] != owner:
                    raise Busy("request_already_owned_by_another_worker")
                if task["active_run"] != run_id or now >= instant(task["expires_at"]):
                    raise LeaseLost("request_lease_expired_or_superseded")
                return self._claim(task_id, run_id, {**run, "expires_at": task["expires_at"]})
            completed = snapshot.documents[completed_id]
            if completed:
                return self._claim(task_id, completed.value["run_id"], completed.value, completed=True)
            if task["active_run"] and now < instant(task["expires_at"]):
                raise Busy("task_already_working")
            expires = (now + timedelta(seconds=ttl_seconds)).isoformat()
            run = {"task_id": task_id, "owner": owner, "input_hash": input_hash,
                   "fence": task["fence"] + 1, "expires_at": expires,
                   "started_at": snapshot.read_time, "receipt": None}
            task.update(status="Working", active_run=run_id, owner=owner,
                        fence=run["fence"], expires_at=expires)
            try:
                self.port.commit({task_id: (task, doc.version), run_id: (run, None)})
                return self._claim(task_id, run_id, run)
            except Conflict:
                continue
        raise Conflict("claim_contention")

    @staticmethod
    def _claim(task_id, run_id, run, completed=False):
        return Claim(task_id, run_id, run["owner"], run["fence"], run["input_hash"],
                     run["expires_at"], completed)

    @staticmethod
    def _owns(claim, task, read_time):
        if (claim.completed or task["active_run"] != claim.run_id
                or task["fence"] != claim.fence or task["owner"] != claim.owner
                or instant(read_time) >= instant(task["expires_at"])):
            raise LeaseLost("run_no_longer_owns_task")

    def renew(self, claim, ttl_seconds=120):
        if type(ttl_seconds) is not int or not 30 <= ttl_seconds <= 900:
            raise ValueError("invalid_lease_duration")
        snapshot = self.port.read([claim.task_id])
        doc = snapshot.documents[claim.task_id]
        if doc is None:
            raise LeaseLost("unknown_task")
        task = dict(doc.value)
        self._owns(claim, task, snapshot.read_time)
        task["expires_at"] = (instant(snapshot.read_time) + timedelta(seconds=ttl_seconds)).isoformat()
        self.port.commit({claim.task_id: (task, doc.version)})
        return Claim(claim.task_id, claim.run_id, claim.owner, claim.fence,
                     claim.input_hash, task["expires_at"])

    def finish(self, claim, *, records, baseline=None, expected_checkpoint=None,
               complete_scan=False):
        """Publish reviewed proposals atomically, not grades or delivery receipts.

        records are immutable named JSON values; large reports must be partitioned
        by the worker. Missing/failed scans cannot advance a successful baseline.
        An exact retry returns the existing receipt, including after an uncertain
        network response. A different result with the same request is rejected.
        """
        if not isinstance(records, dict) or not 1 <= len(records) <= 32:
            raise ValueError("invalid_result_records")
        for name, value in records.items():
            identifier(name)
            bounded_json(value)
        if len(json.dumps(records, ensure_ascii=False).encode()) > 2_000_000:
            raise ValueError("result_batch_too_large")
        if baseline is not None:
            if (complete_scan is not True or not isinstance(baseline, dict)
                    or set(baseline) != {"scope", "source_revision", "observed_at", "fingerprint"}):
                raise ValueError("complete_verified_scan_required")
            for field in ("scope", "source_revision", "fingerprint"):
                identifier(baseline[field])
            instant(baseline["observed_at"])
        digest = content_hash({"records": records, "baseline": baseline,
                               "expected_checkpoint": expected_checkpoint})
        ids = {name: key("record", {"task": claim.task_id, "run": claim.run_id, "name": name})
               for name in records}
        completed_id = key("completed", {"task": claim.task_id, "input_hash": claim.input_hash})
        snapshot = self.port.read([claim.task_id, claim.run_id, completed_id, *ids.values()])
        task_doc, run_doc = snapshot.documents[claim.task_id], snapshot.documents[claim.run_id]
        if task_doc is None or run_doc is None:
            raise LeaseLost("unknown_run")
        task, run = dict(task_doc.value), dict(run_doc.value)
        if run["owner"] != claim.owner or run["input_hash"] != claim.input_hash or run["fence"] != claim.fence:
            raise LeaseLost("run_identity_mismatch")
        if run["receipt"]:
            if run["receipt"]["result_hash"] != digest:
                raise Conflict("completed_run_result_conflict")
            if any(snapshot.documents[ids[n]] is None
                   or snapshot.documents[ids[n]].value.get("payload") != v for n, v in records.items()):
                raise StoreUnavailable("state_readback_failed")
            return run["receipt"]
        self._owns(claim, task, snapshot.read_time)
        if snapshot.documents[completed_id] is not None:
            raise Conflict("input_already_completed")
        checkpoint = task.get("baseline")
        if baseline is not None:
            if (checkpoint or {}).get("checkpoint_token") != expected_checkpoint:
                raise Conflict("baseline_changed")
            if checkpoint and (checkpoint["scope"] != baseline["scope"]
                               or instant(baseline["observed_at"]) <= instant(checkpoint["observed_at"])):
                raise Conflict("baseline_scope_or_time_conflict")
            age = instant(snapshot.read_time) - instant(baseline["observed_at"])
            if not timedelta(0) <= age <= timedelta(days=1):
                raise ValueError("stale_or_future_baseline")
            checkpoint = {**baseline, "checkpoint_token": content_hash(baseline)}
        changes = {}
        for name, record_id in ids.items():
            if snapshot.documents[record_id] is not None:
                raise Conflict("immutable_record_conflict")
            changes[record_id] = ({"scope": task["scope"], "run_id": claim.run_id,
                                    "name": name, "payload": records[name]}, None)
        receipt = {"run_id": claim.run_id, "result_hash": digest, "record_ids": ids,
                   "stored_at": snapshot.read_time,
                   "checkpoint_token": (checkpoint or {}).get("checkpoint_token")}
        run.update(receipt=receipt, baseline=checkpoint)
        task.update(status="Needs you", active_run=None, baseline=checkpoint,
                    last_result=receipt, next_action="review_stored_result")
        changes.update({claim.task_id: (task, task_doc.version), claim.run_id: (run, run_doc.version)})
        changes[completed_id] = ({**run, "run_id": claim.run_id}, None)
        self.port.commit(changes)
        # Do not claim verified persistence from a successful HTTP commit alone.
        reread = self.port.read([claim.task_id, claim.run_id, *ids.values()])
        if (reread.documents[claim.run_id] is None
                or reread.documents[claim.run_id].value.get("receipt") != receipt
                or any(reread.documents[ids[n]] is None
                       or reread.documents[ids[n]].value.get("payload") != v for n, v in records.items())):
            raise StoreUnavailable("state_readback_failed")
        return receipt

    def release_for_review(self, claim, reason_code):
        """Stop on a failed/partial source read without replacing good history."""
        identifier(reason_code)
        snapshot = self.port.read([claim.task_id])
        doc = snapshot.documents[claim.task_id]
        if doc is None:
            raise LeaseLost("unknown_task")
        task = dict(doc.value)
        self._owns(claim, task, snapshot.read_time)
        task.update(status="Needs you", active_run=None, next_action=reason_code)
        self.port.commit({claim.task_id: (task, doc.version)})

    def read_task(self, task_id):
        identifier(task_id)
        snapshot = self.port.read([task_id])
        doc = snapshot.documents[task_id]
        if doc is None:
            return None
        task = dict(doc.value)
        if task["status"] == "Working" and instant(snapshot.read_time) >= instant(task["expires_at"]):
            task.update(status="Needs you", next_action="expired_run_needs_recovery")
        return task
