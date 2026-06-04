"""
observability/structured_logger.py

Sprint 2.11 B4: Structured JSON logging.

Provides a JSON log formatter that emits structured records with standardised
fields for ingestion by log aggregators (Datadog, CloudWatch, Loki, etc.).

Standard fields emitted per log record:
  timestamp   — ISO 8601 UTC (always)
  level       — DEBUG | INFO | WARNING | ERROR | CRITICAL
  service     — constant "kwikid-ai-ingest"
  logger      — logger name (module path)
  message     — formatted log message
  request_id  — correlation ID (from contextvars, set by RequestIdMiddleware)
  action_id   — if set in context
  case_id     — if set in context
  client      — if set in context
  exc_info    — exception type + message (when an exception is being logged)

Security:
  API keys, tokens, and secrets are NEVER included in log output.
  The formatter strips any field whose name matches _REDACT_KEYS.
  Stack traces are included only when LOG_LEVEL=DEBUG to prevent secret leakage
  through exception arguments.

Usage:
    from observability.structured_logger import configure_structured_logging
    configure_structured_logging(service="kwikid-ai-ingest", level="INFO")

Context propagation:
    from observability.structured_logger import log_context
    with log_context(request_id="req-123", action_id="act-456"):
        logger.info("processing action")  # fields injected automatically
"""
from __future__ import annotations

import json
import logging
import sys
import traceback
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

_SERVICE_NAME = "kwikid-ai-ingest"

# Contextvars for per-request field injection
_ctx_request_id: ContextVar[str] = ContextVar("request_id", default="")
_ctx_action_id: ContextVar[str] = ContextVar("action_id", default="")
_ctx_case_id: ContextVar[str] = ContextVar("case_id", default="")
_ctx_client: ContextVar[str] = ContextVar("client", default="")

# Field names that must never appear in log output
_REDACT_KEYS: frozenset[str] = frozenset({
    "api_key", "apikey", "api_secret", "password", "secret", "token",
    "supabase_key", "openai_api_key", "embedding_api_key", "authorization",
    "x-api-key", "x_api_key", "key", "credential", "credentials",
})


class StructuredJsonFormatter(logging.Formatter):
    """
    Logging formatter that emits one JSON object per log record.

    Suitable for consumption by Datadog, Loki, CloudWatch Logs Insights,
    and any structured-log aggregator.
    """

    def __init__(self, service: str = _SERVICE_NAME) -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        obj: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "service": self._service,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Inject context vars
        if rid := _ctx_request_id.get(""):
            obj["request_id"] = rid
        if aid := _ctx_action_id.get(""):
            obj["action_id"] = aid
        if cid := _ctx_case_id.get(""):
            obj["case_id"] = cid
        if cl := _ctx_client.get(""):
            obj["client"] = cl

        # Inject extra fields from record (e.g., logger.info("...", extra={"action_id": ...}))
        for key, val in record.__dict__.items():
            if key in _RESERVED_RECORD_KEYS or key.startswith("_"):
                continue
            if _should_redact(key):
                continue
            obj[key] = val

        # Exception info — limited to type+message to avoid leaking secrets in tracebacks
        if record.exc_info and record.exc_info[0] is not None:
            exc_type, exc_val, exc_tb = record.exc_info
            obj["exc_type"] = exc_type.__name__ if exc_type else "unknown"
            obj["exc_message"] = str(exc_val)
            # Full traceback only at DEBUG level
            if record.levelno <= logging.DEBUG:
                obj["exc_traceback"] = traceback.format_exception(exc_type, exc_val, exc_tb)

        return json.dumps(obj, default=str, ensure_ascii=False)


_RESERVED_RECORD_KEYS: frozenset[str] = frozenset({
    "args", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "message", "module", "msecs",
    "msg", "name", "pathname", "process", "processName", "relativeCreated",
    "stack_info", "thread", "threadName",
})


def _should_redact(key: str) -> bool:
    lower = key.lower()
    return any(rk in lower for rk in _REDACT_KEYS)


# ── Context management ─────────────────────────────────────────────────────────

class log_context:
    """
    Context manager that injects structured fields into all log records
    emitted within the block.

    Usage:
        with log_context(request_id="req-abc", action_id="act-123"):
            logger.info("handling request")
    """

    def __init__(
        self,
        *,
        request_id: str = "",
        action_id: str = "",
        case_id: str = "",
        client: str = "",
    ) -> None:
        self._values = {
            "request_id": request_id,
            "action_id": action_id,
            "case_id": case_id,
            "client": client,
        }
        self._tokens: list = []

    def __enter__(self) -> "log_context":
        if v := self._values["request_id"]:
            self._tokens.append(_ctx_request_id.set(v))
        if v := self._values["action_id"]:
            self._tokens.append(_ctx_action_id.set(v))
        if v := self._values["case_id"]:
            self._tokens.append(_ctx_case_id.set(v))
        if v := self._values["client"]:
            self._tokens.append(_ctx_client.set(v))
        return self

    def __exit__(self, *_) -> None:
        for token in reversed(self._tokens):
            token.var.reset(token)


def set_request_id(request_id: str) -> None:
    """Set the current request_id in logging context (without context manager)."""
    _ctx_request_id.set(request_id)


def get_request_id() -> str:
    """Return the current request_id from logging context (empty string if unset)."""
    return _ctx_request_id.get("")


def set_action_context(
    *,
    action_id: str = "",
    case_id: str = "",
    client: str = "",
) -> None:
    """Set action-level context fields in the current context."""
    if action_id:
        _ctx_action_id.set(action_id)
    if case_id:
        _ctx_case_id.set(case_id)
    if client:
        _ctx_client.set(client)


# ── Configuration ──────────────────────────────────────────────────────────────

def configure_structured_logging(
    service: str = _SERVICE_NAME,
    level: str = "INFO",
    stream=None,
) -> None:
    """
    Configure the root logger to emit structured JSON.

    Call once at application startup (before any log messages).
    Replaces all existing handlers on the root logger.

    Args:
        service: service name embedded in every log record.
        level:   minimum log level (e.g. "INFO", "DEBUG").
        stream:  output stream (default: sys.stdout).
    """
    if stream is None:
        stream = sys.stdout

    formatter = StructuredJsonFormatter(service=service)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    logging.getLogger(__name__).info(
        "structured_logger: JSON logging configured level=%s service=%s", level, service
    )
