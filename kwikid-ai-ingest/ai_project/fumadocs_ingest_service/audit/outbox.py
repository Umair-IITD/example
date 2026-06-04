"""
audit/outbox.py

Sprint 2.10 Hardening: Lightweight audit event outbox buffer.

Design:
  When all retry attempts are exhausted, events are written to a bounded,
  thread-safe in-process queue (the outbox). A background flush loop
  attempts to re-deliver outbox events on the next write cycle.

  This provides a best-effort durability layer WITHOUT over-engineering:
  - No persistent storage (process restart clears the outbox)
  - Bounded queue prevents unbounded memory growth
  - Flush is triggered on each new insert_event() call
  - If the queue is full, oldest events are evicted with a warning

Limitations (documented gaps, not design defects):
  - Process crash loses all queued events
  - Queue is per-process (no cross-worker sharing)
  - For guaranteed delivery, use a durable message broker (Kafka, SQS)

Metrics emitted (via MetricsService if injected):
  audit_outbox_size            — current queue depth
  audit_outbox_flush_success   — events successfully flushed from queue
  audit_outbox_flush_failure   — flush attempts that failed

Thread safety:
  All operations are protected by a threading.Lock.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any, Callable

LOGGER = logging.getLogger(__name__)

_DEFAULT_MAX_SIZE = 1000


class AuditOutbox:
    """
    Bounded thread-safe outbox for failed audit events.

    Usage:
        outbox = AuditOutbox(max_size=1000)
        outbox.enqueue(event)          # store for later retry
        outbox.flush(flush_fn)         # attempt to drain the queue
        outbox.size                    # current queue depth
    """

    def __init__(self, max_size: int = _DEFAULT_MAX_SIZE, metrics: Any = None) -> None:
        self._max_size = max(1, max_size)
        self._queue: deque = deque()
        self._lock = threading.Lock()
        self._metrics = metrics

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._queue)

    def enqueue(self, event: Any) -> None:
        """
        Add an event to the outbox.

        If the queue is at capacity, the OLDEST event is evicted to make room.
        This is a deliberate tradeoff: recent events are more actionable.
        """
        with self._lock:
            if len(self._queue) >= self._max_size:
                evicted = self._queue.popleft()
                LOGGER.warning(
                    "audit.outbox: queue full (%d). Evicting oldest event event_id=%s",
                    self._max_size,
                    getattr(evicted, "event_id", "?"),
                )
            self._queue.append(event)
            LOGGER.debug(
                "audit.outbox: enqueued event_id=%s (queue depth=%d)",
                getattr(event, "event_id", "?"), len(self._queue),
            )
        self._emit_size()

    def flush(self, flush_fn: Callable[[Any], None]) -> tuple[int, int]:
        """
        Attempt to flush all queued events using flush_fn.

        flush_fn(event) must not raise — it returns a boolean-like success result.
        Events that flush_fn raises on are re-queued at the back.

        Returns:
            (success_count, failure_count)
        """
        with self._lock:
            to_flush = list(self._queue)
            self._queue.clear()

        success = 0
        failed: list[Any] = []

        for event in to_flush:
            try:
                flush_fn(event)
                success += 1
                self._emit("audit_outbox_flush_success")
            except Exception as exc:
                LOGGER.warning(
                    "audit.outbox: flush failed for event_id=%s, re-queuing: %s",
                    getattr(event, "event_id", "?"), exc,
                )
                failed.append(event)
                self._emit("audit_outbox_flush_failure")

        if failed:
            with self._lock:
                for ev in failed:
                    self._queue.append(ev)

        self._emit_size()
        return success, len(failed)

    def drain(self) -> list[Any]:
        """Remove and return all queued events (for testing/inspection)."""
        with self._lock:
            items = list(self._queue)
            self._queue.clear()
        self._emit_size()
        return items

    def _emit(self, counter_name: str) -> None:
        if self._metrics is None:
            return
        try:
            self._metrics._col.increment(counter_name)
        except Exception:
            pass

    def _emit_size(self) -> None:
        if self._metrics is None:
            return
        try:
            with self._lock:
                size = len(self._queue)
            self._metrics._col._counters["audit_outbox_size"] = size
        except Exception:
            pass
