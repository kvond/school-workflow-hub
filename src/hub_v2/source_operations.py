"""Trusted-host guard for participating before-Helper source writers.

This is an adapter over the existing CAS task/lease, not a scheduler, browser
protocol or evidence that every external session participates. The host must
use the SAME stable source_scope for roster, archive and other preparation
writers, and supply a real fresh activity check. A missing receipt or an idle
agent list cannot supply that check. No school operation is executed here.
"""
import math
from datetime import datetime, time, timedelta
from time import monotonic
from zoneinfo import ZoneInfo

from school_workflow_hub.core.cloud_state import (
    CloudState, Conflict, LeaseLost, StoreUnavailable, identifier, instant, key,
)

EASTERN = ZoneInfo("America/New_York")
RENEWAL_OVERHEAD_SECONDS = 30
MAX_WRITE_SECONDS = 900 - RENEWAL_OVERHEAD_SECONDS


class Held(Conflict):
    def __init__(self, code):
        reasons = {
            "source_activity_reader_unavailable": "Current helper and source-writer ownership could not be verified.",
            "source_activity_verification_incomplete": "Current helper and source-writer ownership could not be verified.",
            "source_activity_verification_unavailable": "Current helper and source-writer ownership could not be verified.",
            "helper_inputs_frozen": "The grade helper owns frozen inputs; source changes must wait for the next safe preparation window.",
            "source_mutation_window_closed": "Source changes are held until the next weekday preparation window before 7:00 a.m. Eastern.",
            "source_operation_stopped": "Source preparation stopped after ownership or readiness could not be verified.",
            "source_operation_already_released": "This source-preparation owner has already released its claim.",
            "source_lease_too_short": "Source ownership could not be renewed long enough to finish the next write safely.",
            "source_write_budget_invalid": "The next write needs a bounded operation and saved-readback budget; split an oversized operation before retrying.",
        }
        if code not in reasons:
            code = "source_activity_verification_unavailable"
        self.source_operation_hold = code
        self.source_operation_action = reasons[code]
        super().__init__(code)


class SourceOperations:
    """One owner of the shared preparation scope; revalidate before each write.

    check_activity is trusted host code, never a boolean from a request. It must
    freshly verify helper-frozen inputs and external writer ownership through
    supported capabilities, return None on success, and raise on unknown/active
    conflict. This guard cannot establish that capability on its own.
    """

    @classmethod
    def acquire(cls, port, *, source_scope, owner, request_id, input_hash,
                check_activity, ttl_seconds=120):
        for value in (source_scope, owner, request_id, input_hash):
            identifier(value)
        if not callable(check_activity):
            raise Held("source_activity_reader_unavailable")
        if type(ttl_seconds) is not int or not 30 <= ttl_seconds <= 900:
            raise ValueError("invalid_lease_duration")
        self = cls()
        self.cloud, self.port = CloudState(port), port
        self.check_activity, self.ttl_seconds = check_activity, ttl_seconds
        self.scope = {"workflow": "school-source-preparation", "class_key": "all",
                      "assignment_key": key("sourcescope", source_scope)}
        self.task_id = key("task", self.scope)
        self.owner, self.request_id, self.input_hash = owner, request_id, input_hash
        self.claim, self.stopped, self.released = None, False, False
        self._activity()
        ttl = self._window()
        self.cloud.create_task(self.scope)
        # Same request recovery is state-only. It never repeats a school action.
        try:
            self.claim = self._claim(ttl)
        except StoreUnavailable:
            self.claim = self._claim(ttl)
        self._validate_claim()
        self._window(require_owned=True)
        return self

    def _activity(self):
        try:
            result = self.check_activity()
            if result is not None:
                raise Held("source_activity_verification_incomplete")
        except Exception as error:
            self.stopped = True
            if isinstance(error, (Conflict, StoreUnavailable)):
                raise
            raise Held("source_activity_verification_unavailable") from None

    def _window(self, *, require_owned=False, required_seconds=0, reserve_lease=False):
        started = monotonic()
        snapshot = self.port.read([self.task_id])
        # Count the entire response interval conservatively. The server stamp
        # alone can be stale by the time a delayed read reaches this host.
        now = instant(snapshot.read_time) + timedelta(seconds=max(0, monotonic() - started))
        local = now.astimezone(EASTERN)
        cutoff = datetime.combine(local.date(), time(7), tzinfo=EASTERN)
        remaining = math.floor((cutoff - local).total_seconds())
        if local.weekday() >= 5 or remaining < 30 or remaining <= required_seconds:
            self.stopped = True
            raise Held("source_mutation_window_closed")
        if require_owned:
            doc = snapshot.documents[self.task_id]
            if doc is None:
                raise LeaseLost("source_owner_unavailable")
            self.cloud._owns(self.claim, doc.value, now.isoformat())
            # A still-valid short lease may be renewed. Reserve its full write
            # budget only AFTER renewal, never before that state-only action.
            if reserve_lease and (instant(doc.value["expires_at"]) - now).total_seconds() <= required_seconds:
                raise Held("source_lease_too_short")
        return min(900, remaining, max(self.ttl_seconds, required_seconds + RENEWAL_OVERHEAD_SECONDS))

    def _claim(self, ttl):
        return self.cloud.claim(self.task_id, owner=self.owner, request_id=self.request_id,
                                input_hash=self.input_hash, ttl_seconds=ttl)

    def _validate_claim(self):
        if (self.claim.completed or self.claim.task_id != self.task_id
                or self.claim.owner != self.owner or self.claim.input_hash != self.input_hash):
            raise LeaseLost("source_owner_mismatch")

    def before_write(self, *, required_seconds):
        """Reserve a trusted bound for this mutation AND its immediate readback.

        The caller includes provider timeouts, bounded read retries, token work
        after this guard and native readback in the budget. Long read-only
        verification may continue later, but every following mutation requires
        another call. Unknown/unbounded operations are not authorized here.
        """
        if type(required_seconds) is not int or not 1 <= required_seconds <= MAX_WRITE_SECONDS:
            raise Held("source_write_budget_invalid")
        if self.stopped or self.released or self.claim is None:
            raise Held("source_operation_stopped")
        try:
            self._activity()
            ttl = self._window(require_owned=True, required_seconds=required_seconds)
            previous = self.claim
            try:
                self.claim = self.cloud.renew(previous, ttl_seconds=ttl)
            except StoreUnavailable:
                # A renewal can commit and lose its response. Recover only the
                # identical unexpired claim and fence, not a new write request.
                current = self._claim(ttl)
                if (current.run_id, current.fence) != (previous.run_id, previous.fence):
                    raise LeaseLost("source_owner_changed")
                self.claim = current
            self._validate_claim()
            self._window(require_owned=True, required_seconds=required_seconds, reserve_lease=True)
        except Exception:
            self.stopped = True
            raise

    def release(self, reason_code="source_preparation_finished"):
        """Release only this exact claim; expiry/takeover are never cleared."""
        identifier(reason_code)
        if self.released or self.claim is None:
            raise Held("source_operation_already_released")
        try:
            self.cloud.release_for_review(self.claim, reason_code)
        except StoreUnavailable:
            # Recover a lost release response by reading, never repeat release.
            pass
        snapshot = self.port.read([self.task_id])
        doc = snapshot.documents[self.task_id]
        if (doc is None or doc.value.get("active_run") is not None
                or doc.value.get("owner") != self.claim.owner
                or doc.value.get("fence") != self.claim.fence
                or doc.value.get("next_action") != reason_code):
            raise StoreUnavailable("source_release_unconfirmed")
        self.released = True
        self.stopped = True
        return {"released": True, "scope": "participating_preparation_workers",
                "external_ownership": "verified_by_host_callback_only"}
