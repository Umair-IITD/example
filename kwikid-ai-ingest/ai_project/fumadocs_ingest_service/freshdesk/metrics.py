"""
freshdesk/metrics.py

Sprint 2.28.1: Freshdesk-specific metric counter names.

Metrics defined here follow the same pattern as metrics/collector.py.
All counters are registered as string constants and incremented via
MetricsCollector.increment(counter_name).

Metrics:
  COUNTER_FD_WEBHOOKS_RECEIVED_TOTAL   — total webhook calls received
  COUNTER_FD_WEBHOOKS_REJECTED_TOTAL   — rejected (HMAC failure, replay, etc.)
  COUNTER_FD_DUPLICATE_EVENTS_TOTAL    — idempotency hits (duplicate suppression)
  COUNTER_FD_CUSTOMER_REPLIES_TOTAL    — customer reply events processed
  COUNTER_FD_PRIVATE_NOTES_TOTAL       — private notes written to Freshdesk
  COUNTER_FD_PUBLIC_REPLIES_TOTAL      — public replies sent to customers
  LATENCY_FD_PROCESSING_MS             — webhook processing latency (ms, histogram)
"""
from __future__ import annotations

# ── Counter names ──────────────────────────────────────────────────────────────
COUNTER_FD_WEBHOOKS_RECEIVED_TOTAL  = "freshdesk_webhooks_received_total"
COUNTER_FD_WEBHOOKS_REJECTED_TOTAL  = "freshdesk_webhooks_rejected_total"
COUNTER_FD_DUPLICATE_EVENTS_TOTAL   = "freshdesk_duplicate_events_total"
COUNTER_FD_CUSTOMER_REPLIES_TOTAL   = "freshdesk_customer_replies_total"
COUNTER_FD_PRIVATE_NOTES_TOTAL      = "freshdesk_private_notes_total"
COUNTER_FD_PUBLIC_REPLIES_TOTAL     = "freshdesk_public_replies_total"
COUNTER_FD_API_ERRORS_TOTAL         = "freshdesk_api_errors_total"

# ── Latency histogram names ────────────────────────────────────────────────────
LATENCY_FD_PROCESSING_MS            = "freshdesk_processing_latency_ms"
LATENCY_FD_API_CALL_MS              = "freshdesk_api_call_latency_ms"

# ── All metric names (for validation in tests) ────────────────────────────────
ALL_COUNTER_NAMES: tuple[str, ...] = (
    COUNTER_FD_WEBHOOKS_RECEIVED_TOTAL,
    COUNTER_FD_WEBHOOKS_REJECTED_TOTAL,
    COUNTER_FD_DUPLICATE_EVENTS_TOTAL,
    COUNTER_FD_CUSTOMER_REPLIES_TOTAL,
    COUNTER_FD_PRIVATE_NOTES_TOTAL,
    COUNTER_FD_PUBLIC_REPLIES_TOTAL,
    COUNTER_FD_API_ERRORS_TOTAL,
)

ALL_LATENCY_NAMES: tuple[str, ...] = (
    LATENCY_FD_PROCESSING_MS,
    LATENCY_FD_API_CALL_MS,
)
