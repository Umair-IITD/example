"""
case_engine/retry

Sprint 2.26: In-memory Retry Queue Framework.

Public API:
  RetryStatus         — lifecycle states (PENDING/RUNNING/SUCCEEDED/FAILED/DEAD_LETTERED)
  RetryJob            — immutable job record
  RetryRepository     — thread-safe in-memory store
  RetryScheduler      — schedule / reschedule with exponential backoff
  RetryExecutor       — protocol for execution adapters
  RetryWorker         — process due jobs via executor
  RetryRunResult      — summary of one worker tick
  DeadLetterQueue     — enumerate / requeue / purge dead jobs

Blueprint alignment (flow_diagram.mermaid RECOVERY subgraph):
  RECOVERY → RETRY_SCHEDULED → RetryScheduler.schedule()
  RetryWorker.run_once() → EXECUTE → Success / Failure → DLQ
"""
from case_engine.retry.models import RetryJob, RetryStatus, TERMINAL_RETRY_STATES
from case_engine.retry.repository import RetryRepository
from case_engine.retry.scheduler import RetryScheduler
from case_engine.retry.worker import RetryExecutor, RetryRunResult, RetryWorker
from case_engine.retry.dlq import DeadLetterQueue

__all__ = [
    "RetryJob",
    "RetryStatus",
    "TERMINAL_RETRY_STATES",
    "RetryRepository",
    "RetryScheduler",
    "RetryExecutor",
    "RetryRunResult",
    "RetryWorker",
    "DeadLetterQueue",
]
