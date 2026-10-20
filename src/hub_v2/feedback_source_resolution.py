"""Bounded retries for read-only feedback source captures, never writes.

One budget is shared by all source workers in a feedback request. This is not
permission to skip source checks or a replacement for exact source resolution.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timezone
from email.utils import parsedate_to_datetime
import math
from threading import Lock
import time

from . import google

_budget = ContextVar('feedback_source_read_budget', default=None)
MAX_RETRIES = 2
MAX_WAIT_SECONDS = 2.0
MAX_ELAPSED_SECONDS = 20.0


class ReadBudget:
    def __init__(self, *, clock=time.monotonic, wall_clock=time.time, sleep=time.sleep):
        self.clock, self.wall_clock, self.sleep = clock, wall_clock, sleep
        self.started = clock()
        self.lock = Lock()
        self.retries = 0
        self.waited = 0.0

    def delay(self, error):
        """Reserve one retry, or refuse; never shorten provider retry guidance."""
        with self.lock:
            if self.retries >= MAX_RETRIES:
                return None
            delay = .5 * (2 ** self.retries)
            if error.retry_after is not None:
                try:
                    raw = error.retry_after
                    if not isinstance(raw, str) or len(raw) > 100:
                        return None
                    if raw.isascii() and raw.isdigit():
                        guidance = float(raw)
                    else:
                        when = parsedate_to_datetime(raw)
                        if when.tzinfo is None:
                            return None
                        guidance = max(0.0, when.astimezone(timezone.utc).timestamp() - self.wall_clock())
                    if not math.isfinite(guidance):
                        return None
                    delay = max(delay, guidance)
                except (ValueError, TypeError, OverflowError):
                    return None
            if (self.waited + delay > MAX_WAIT_SECONDS
                    or self.clock() - self.started + delay > MAX_ELAPSED_SECONDS):
                return None
            self.retries += 1
            self.waited += delay
            return delay


@contextmanager
def source_reads(budget=None):
    """Nest without resetting the request-wide retry bound."""
    if _budget.get() is not None:
        yield
        return
    context = _budget.set(budget if budget is not None else ReadBudget())
    try:
        yield
    finally:
        _budget.reset(context)


def read(operation, *args):
    """Repeat only an explicit source GET/export that returned HTTP 429."""
    budget = _budget.get()
    while True:
        try:
            return operation(*args)
        except google.GoogleError as error:
            if budget is None or error.status != 429:
                raise
            delay = budget.delay(error)
            if delay is not None:
                budget.sleep(delay)
            # A scheduler may resume a sleeping worker after its deadline.
            # Never start another provider read under an expired budget.
            if delay is None or budget.clock() - budget.started >= MAX_ELAPSED_SECONDS:
                raise google.GoogleError(
                    'Google is rate-limiting feedback source reads. Wait before a fresh source check.',
                    error.status, error.reason, error.retry_after) from None
