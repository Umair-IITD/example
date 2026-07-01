from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from dotenv import load_dotenv
load_dotenv()

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from api.middleware.request_id import RequestIdMiddleware
from api.routes import (
    actions as _gw_actions,
    admin as _gw_admin,
    audit as _gw_audit,
    case_engine as _case_engine_routes,
    gateway_admin as _gw_gateway_admin,
    health as _gw_health,
    metrics as _gw_metrics,
    recovery_admin as _gw_recovery_admin,
    investigation_admin as _investigation_admin_routes,
    action_proposals_admin as _action_proposals_admin_routes,
    execution_admin as _execution_admin_routes,
    reasoning_admin as _reasoning_admin_routes,
    clarification_admin as _clarification_admin_routes,
    adapter_admin as _adapter_admin_routes,
    tickets as _tickets_routes,
    tool_admin as _tool_admin_routes,
    watchdog as _gw_watchdog,
    webhook as _gw_webhook,
    worker as _gw_worker,
    workflow_admin as _wf_admin_routes,
)
from api.routes.webhooks import freshdesk as _fd_webhook_routes
from app.security import (
    _GATEWAY_PREFIXES,
    api_key_auth_middleware,
    initialize as security_initialize,
    load_api_keys,
    validate_startup_security,
)
from app.rate_limiter import get_limiters, pick_limiter

from observability import logger, tracer, ChunkMetadata, LLMResponseMetadata
from observability.metrics import (
    ActiveRequestContext,
    force_initialize as _metrics_force_initialize,
    get_metrics_response,
    record_rate_limit_rejection,
    record_request,
    record_retrieval_candidates,
    record_retrieval_latency,
)

from app.chat import run_chat
from app.config import get_settings
from app.freshdesk_webhook import (
    FreshdeskReplyClient,
    build_query_text,
    extract_ticket_info,
    format_note_html,
    format_reply_html,
    meets_confidence_threshold,
    resolve_tenant,
    verify_webhook_token,
)
from app.embeddings import EmbeddingClient
from app.ingest import run_ingest
from app.parser_freshdesk import PRIORITY_MAP, STATUS_MAP
from app.query import run_query
from app.suggestions import get_suggestions
from app.train import (
    DraftKnowledgeCard,
    commit_knowledge_card,
    delete_knowledge_card,
    get_knowledge_card,
    list_knowledge_cards,
    run_train_chat,
)
from app.vector_store import VectorStore

from supabase import create_client
from rag_engine.config.rag_settings import get_rag_settings
from rag_engine.embedding.openai_provider import OpenAIEmbeddingProvider
from rag_engine.generation.chat_generator import B1HistoryStore, ChatGenerator, GenerationRequest
from rag_engine.generation.llm_client import B1LLMClient
from rag_engine.retrieval.ticket_retriever import TicketRetriever
from rag_engine.feedback.feedback_loop import FeedbackIngester
from rag_engine.feedback.review_queue import ReviewQueueManager
from app.feedback import FeedbackRequest, FeedbackResponse, handle_feedback
from app.query_router import QueryRouter

from case_engine.case_state import CaseState
from case_engine.service import CaseService, build_case_service
from app.pii_masking import mask_aadhaar
from app.action_proposer import build_synthetic_case, propose_rag_note_action


_LOGGER_PRE = logging.getLogger(__name__)
LOGGER = _LOGGER_PRE

_B1_HYBRID_ENABLED = os.getenv("B1_HYBRID_RETRIEVAL_ENABLED", "false").strip().lower() in {
    "1", "true", "yes", "on"
}

# Sprint 2.12: Route RAG note postings through the ActionGateway when true.
# Default false → existing FreshdeskReplyClient direct-post path (production safety).
# Set ACTION_GATEWAY_ENABLED=true to activate the autonomous action flow.
_ACTION_GATEWAY_ENABLED: bool = os.getenv("ACTION_GATEWAY_ENABLED", "false").strip().lower() in {
    "1", "true", "yes", "on"
}
print(
    f"[STARTUP] _ACTION_GATEWAY_ENABLED={_ACTION_GATEWAY_ENABLED!r} "
    f"raw_env={os.getenv('ACTION_GATEWAY_ENABLED')!r}",
    flush=True,
)

# Sprint 2.12: Worker poll interval (seconds). How often the background worker
# loop checks for APPROVED actions across all tenants.
_WORKER_POLL_INTERVAL_S: float = float(os.getenv("ACTION_WORKER_POLL_INTERVAL_S", "5"))

# Sprint 2.12: Watchdog poll interval (seconds). How often the background
# watchdog loop expires stale AWAITING_APPROVAL actions.
_WATCHDOG_POLL_INTERVAL_S: float = float(os.getenv("ACTION_WATCHDOG_POLL_INTERVAL_S", "60"))

_QUERY_ROUTER = QueryRouter()

# ── SPRINT0_FIX_SINGLETON — module-level client singletons ───────────────────
# Sprint 0 benchmark showed that creating new Supabase + OpenAI clients per
# request adds ~2.4 s of TCP/TLS cold-start overhead per /rag/chat call.
# These singletons are pre-warmed during lifespan startup (single-threaded)
# so the first live request is already warm.
#
# Thread-safety model:
#   Supabase client — httpcore.ConnectionPool handles concurrent requests safely.
#   OpenAI embedder — httpx.Client is not documented as fully thread-safe;
#     embed calls are serialised via _EMBEDDER_CALL_LOCK (see _ThreadSafeEmbedderWrapper).
#   Both singletons — initialisation protected by double-checked locking.
#
# Rollback: revert _build_chat_generator() to call create_client() and
#   OpenAIEmbeddingProvider() directly, and remove the lifespan pre-warm block.
import collections as _collections
import threading as _threading

_SB_INIT_LOCK = _threading.Lock()
_EMBEDDER_INIT_LOCK = _threading.Lock()
_EMBEDDER_CALL_LOCK = _threading.Lock()   # serialises concurrent embed_single() calls
_GENERATOR_LOCK = _threading.Lock()
_sb_singleton: Any = None                 # supabase.Client — set in lifespan startup
_embedder_singleton: Any = None          # OpenAIEmbeddingProvider — set in lifespan startup
_generator_singleton: Any = None         # ChatGenerator (holds retriever + LLM + history)
_case_service_singleton: "CaseService | None" = None  # Sprint 1 case engine
_CASE_SERVICE_LOCK = _threading.Lock()

# Phase H: LRU embedding cache — avoids redundant OpenAI API calls for repeated queries.
# Key: SHA-256 of input text (PII-safe; hash is one-way).
# Max 256 entries (~1 MB at 3072-float vectors for text-embedding-3-large; less for small).
# _EMBED_CACHE_LOCK is independent of _EMBEDDER_CALL_LOCK: dict access takes microseconds
# while API calls take ~300 ms — keeping them separate prevents cache reads from blocking
# behind ongoing API calls.
_EMBED_CACHE_LOCK = _threading.Lock()
_EMBED_LRU_CACHE: _collections.OrderedDict = _collections.OrderedDict()  # type: ignore[type-arg]
_EMBED_CACHE_MAX = 256

# Module-level APIRouter for all RAG routes.
# Routes are defined below using @_rag_router decorators.
# create_app() includes this router on the FastAPI instance.
_rag_router = APIRouter()


# ── Sprint 2.12: Background loops and crash recovery ─────────────────────────

async def _background_worker_loop(app: "FastAPI") -> None:
    """
    Async background task: poll for APPROVED actions and execute them.

    Runs every _WORKER_POLL_INTERVAL_S seconds. Discovers tenants with pending
    work via ActionRepository.list_clients_with_approved_work() so it doesn't
    need a configured client list. Exception-safe: any error in a single poll
    cycle is caught and logged; the loop continues.

    Cancelled cleanly by the lifespan shutdown (asyncio.CancelledError propagates).
    """
    while True:
        try:
            stack = getattr(app.state, "stack", None)
            if stack is not None:
                clients = await asyncio.to_thread(
                    stack.repository.list_clients_with_approved_work
                )
                for client in clients:
                    try:
                        await asyncio.to_thread(stack.worker.process, client)
                    except Exception as _client_exc:
                        _LOGGER_PRE.warning(
                            "worker_loop: client=%s error=%s", client, _client_exc
                        )
        except asyncio.CancelledError:
            raise
        except Exception as _exc:
            _LOGGER_PRE.warning("worker_loop: poll_cycle_error error=%s", _exc)
        await asyncio.sleep(_WORKER_POLL_INTERVAL_S)


async def _background_watchdog_loop(app: "FastAPI") -> None:
    """
    Async background task: expire stale AWAITING_APPROVAL actions.

    Runs every _WATCHDOG_POLL_INTERVAL_S seconds (default 60 s). Sweeps all
    tenants (client=None). Exception-safe: any error is caught and logged.

    Cancelled cleanly by the lifespan shutdown.
    """
    while True:
        try:
            stack = getattr(app.state, "stack", None)
            if stack is not None:
                result = await asyncio.to_thread(stack.watchdog.run, None)
                if result.expired_actions > 0:
                    _LOGGER_PRE.info(
                        "watchdog_loop: expired=%d processed=%d",
                        result.expired_actions, result.processed,
                    )
        except asyncio.CancelledError:
            raise
        except Exception as _exc:
            _LOGGER_PRE.warning("watchdog_loop: sweep_error error=%s", _exc)
        await asyncio.sleep(_WATCHDOG_POLL_INTERVAL_S)


def _recover_stale_executing_actions(stack: Any) -> None:
    """
    Sprint 2.12 crash recovery: find EXECUTING actions orphaned by a crashed worker
    and transition them back to APPROVED (or DEAD_LETTER if retries exhausted).

    Calls ActionGateway.record_timeout() which uses the existing state machine.
    The executor_id "crash_recovery" is used so audit logs clearly identify the source.

    Runs synchronously during lifespan startup (before the app begins serving).
    Never raises — recovery failures are logged and the app continues starting.
    """
    if stack is None:
        return
    try:
        from case_engine.action_runtime import EXECUTION_TIMEOUT_DEFAULT  # noqa: PLC0415
        timeout_s = int(EXECUTION_TIMEOUT_DEFAULT.total_seconds())
        stale = stack.repository.list_executing_actions(older_than_seconds=timeout_s)
        if not stale:
            return
        _LOGGER_PRE.warning(
            "crash_recovery: found %d stale EXECUTING action(s) — recovering",
            len(stale),
        )
        for action in stale:
            try:
                stack.gateway.record_timeout(action, executor_id="crash_recovery")
                _LOGGER_PRE.info(
                    "crash_recovery: recovered action_id=%s ticket=%s client=%s",
                    action.action_id, action.ticket_id, action.client,
                )
            except Exception as _exc:
                _LOGGER_PRE.error(
                    "crash_recovery: failed action_id=%s error=%s",
                    action.action_id, _exc,
                )
    except Exception as _exc:
        _LOGGER_PRE.error("crash_recovery: unexpected error error=%s", _exc)


# ── Gateway stack builder ─────────────────────────────────────────────────────

def _build_gateway_stack_from_env(supabase_client: Any = None) -> Any:
    """Build ProductionRuntime for the action gateway, reusing the RAG Supabase singleton."""
    from runtime.assembly import build_production_runtime
    from freshdesk.freshdesk_models import FreshdeskConfig
    freshdesk_config = None
    domain = os.environ.get("FRESHDESK_DOMAIN", "").strip()
    api_key = os.environ.get("FRESHDESK_API_KEY", "").strip()
    if domain and api_key:
        try:
            freshdesk_config = FreshdeskConfig(domain=domain, api_key=api_key)
        except Exception as exc:
            _LOGGER_PRE.warning("app.main: Freshdesk config invalid — offline mode: %s", exc)
    return build_production_runtime(
        freshdesk_config=freshdesk_config,
        supabase_client=supabase_client,
    )


# ── Lifespan factory ─────────────────────────────────────────────────────────

def _build_lifespan(*, skip_config_validation: bool = False):
    """Return a lifespan context manager closed over skip_config_validation."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        global _sb_singleton, _embedder_singleton, _generator_singleton, _case_service_singleton

        if not skip_config_validation:
            # ── Security validation ───────────────────────────────────────────
            api_keys = load_api_keys()
            openai_key = os.getenv("OPENAI_API_KEY", "").strip()
            errors = validate_startup_security(api_keys, openai_key)
            if errors:
                for msg in errors:
                    _LOGGER_PRE.critical("STARTUP_SECURITY_ERROR: %s", msg)
                raise RuntimeError(
                    f"Security configuration error — service refuses to start: {errors[0]}"
                )
            security_initialize(api_keys)
            _LOGGER_PRE.info("security_initialized api_keys_count=%d", len(api_keys))

            # ── Webhook HMAC enforcement ──────────────────────────────────────
            webhook_enabled = os.getenv("FRESHDESK_WEBHOOK_ENABLED", "false").strip().lower() in {
                "1", "true", "yes", "on"
            }
            webhook_secret = os.getenv("FRESHDESK_WEBHOOK_SECRET", "").strip()
            enforce_hmac = os.getenv("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false").strip().lower() in {
                "1", "true", "yes", "on"
            }

            if webhook_enabled and not webhook_secret:
                if enforce_hmac:
                    _LOGGER_PRE.critical(
                        "STARTUP_SECURITY_ERROR: FRESHDESK_WEBHOOK_ENFORCE_HMAC=true but "
                        "FRESHDESK_WEBHOOK_SECRET is not set. Service refuses to start. "
                        "Set FRESHDESK_WEBHOOK_SECRET or set FRESHDESK_WEBHOOK_ENFORCE_HMAC=false."
                    )
                    raise RuntimeError(
                        "FRESHDESK_WEBHOOK_ENFORCE_HMAC=true requires FRESHDESK_WEBHOOK_SECRET."
                    )
                _LOGGER_PRE.warning(
                    "SECURITY_WARNING: FRESHDESK_WEBHOOK_ENABLED=true but FRESHDESK_WEBHOOK_SECRET "
                    "is not set. The webhook endpoint accepts requests from any caller. "
                    "Set FRESHDESK_WEBHOOK_SECRET to enable HMAC-SHA256 validation, or set "
                    "FRESHDESK_WEBHOOK_ENFORCE_HMAC=true to make this a startup error."
                )

            # ── CORS origin validation ────────────────────────────────────────
            cors_origins = [
                o.strip()
                for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
                if o.strip()
            ]
            _bad_origins = [
                o for o in cors_origins if not o.startswith(("http://", "https://"))
            ]
            if _bad_origins:
                raise RuntimeError(
                    f"CORS_ALLOWED_ORIGINS contains non-HTTP origins "
                    f"(data:, javascript:, etc. are unsafe): {_bad_origins}"
                )
            if not cors_origins:
                _LOGGER_PRE.info(
                    "cors_origins: using default localhost-only origins (dev mode)"
                )

        # ── Rate limiters ─────────────────────────────────────────────────────
        try:
            limiters = get_limiters()
            _LOGGER_PRE.info(
                "rate_limiters_initialized backends=%s",
                {name: lim.backend_type for name, lim in limiters.items()},
            )
        except Exception as exc:
            _LOGGER_PRE.warning(
                "rate_limiter_init_failed %s — in-process fallback will be used", exc
            )

        # ── Hybrid retrieval mode ─────────────────────────────────────────────
        _LOGGER_PRE.info(
            "retrieval_mode=%s",
            "hybrid (HybridTicketRetriever)" if _B1_HYBRID_ENABLED else "semantic_only (TicketRetriever)",
        )

        # ── Active index version ──────────────────────────────────────────────
        _active_ver = os.getenv("ACTIVE_INDEX_VERSION", "v1")
        _hnsw_v2_on = os.getenv("B1_HNSW_V2_ENABLED", "false").strip().lower() in {
            "1", "true", "yes", "on"
        }
        _LOGGER_PRE.info(
            "[CONFIG] ACTIVE_INDEX_VERSION=%s B1_INDEX_VERSION=%s DEBUG_RAG=%s hybrid=%s hnsw_v2=%s",
            _active_ver,
            os.getenv("B1_INDEX_VERSION", "v1"),
            os.getenv("DEBUG_RAG", "false"),
            os.getenv("B1_HYBRID_RETRIEVAL_ENABLED", "false"),
            os.getenv("B1_HNSW_V2_ENABLED", "false"),
        )
        if _active_ver == "v1" and (_B1_HYBRID_ENABLED or _hnsw_v2_on):
            _LOGGER_PRE.warning(
                "CONFIG_MISMATCH: ACTIVE_INDEX_VERSION=v1 (default) but "
                "B1_HYBRID_RETRIEVAL_ENABLED=%s / B1_HNSW_V2_ENABLED=%s. "
                "Partial HNSW indexes for v2 (B1_011/B1_012) cannot activate while v1 is queried. "
                "Set ACTIVE_INDEX_VERSION=v2 in .env to enable partial index optimisation, "
                "OR apply B1_012 Section 4 to create v1 partial indexes for the current data version.",
                os.getenv("B1_HYBRID_RETRIEVAL_ENABLED", "false"),
                os.getenv("B1_HNSW_V2_ENABLED", "false"),
            )

        # ── Prometheus metrics: eager init with explicit settings value ───────
        # Bypass os.getenv() timing by reading the already-parsed settings flag.
        # This also bypasses any load_dotenv(override=False) shadowing issue where
        # a system env var set before load_dotenv() would hide the .env value.
        try:
            _metrics_settings = get_settings()
            _metrics_force_initialize(enabled=_metrics_settings.prometheus_enabled)
            _LOGGER_PRE.info(
                "prometheus_metrics_init prometheus_enabled=%s",
                _metrics_settings.prometheus_enabled,
            )
        except Exception as _prom_exc:
            _LOGGER_PRE.warning(
                "prometheus_metrics_init_failed error=%s — lazy init at first request will be used",
                _prom_exc,
            )

        # ── SPRINT0_FIX_SINGLETON: pre-warm singletons ────────────────────────
        try:
            _pre_settings = get_settings()
            _pre_rag = get_rag_settings()
            _pre_key = os.getenv("OPENAI_API_KEY", _pre_settings.chat_api_key).strip()
            _sb_singleton = create_client(_pre_settings.supabase_url, _pre_settings.supabase_key)
            _embedder_singleton = OpenAIEmbeddingProvider(
                api_key=_pre_key,
                model=_pre_rag.embedding_model,
                base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                dimensions=_pre_rag.embedding_dimensions,
                max_retries=_pre_rag.embedding_max_retries,
                retry_base_delay_s=_pre_rag.embedding_retry_base_delay_s,
                retry_max_delay_s=_pre_rag.embedding_retry_max_delay_s,
                connect_timeout_s=_pre_rag.embedding_connect_timeout_s,
                read_timeout_s=_pre_rag.embedding_read_timeout_s,
                write_timeout_s=_pre_rag.embedding_write_timeout_s,
                pool_timeout_s=_pre_rag.embedding_pool_timeout_s,
            )
            _, _generator_singleton, _ = _build_chat_generator(_pre_settings, _pre_rag, _pre_key)
            _LOGGER_PRE.info(
                "sprint0_singleton_warmed supabase=ok embedder=ok generator=ok model=%s",
                _pre_rag.embedding_model,
            )
        except Exception as _exc:  # noqa: BLE001
            _LOGGER_PRE.warning(
                "sprint0_singleton_warmup_failed error=%s — cold start will occur on first request",
                _exc,
            )

        # ── Sprint 1: Pre-warm CaseService singleton ──────────────────────────
        try:
            from case_engine.workflows.playbook_registry import PlaybookRegistry  # noqa: PLC0415
            from case_engine.tools.tool_registry import ToolRegistry              # noqa: PLC0415
            from case_engine.tools.tool_executor import ToolExecutor              # noqa: PLC0415
            from case_engine.reasoning.reasoning_engine import ReasoningEngine    # noqa: PLC0415

            _playbook_registry = PlaybookRegistry.build()
            _tool_registry     = ToolRegistry.build_default()
            _tool_executor     = ToolExecutor(_tool_registry)
            _reasoning_engine  = ReasoningEngine()

            _app.state.playbook_registry = _playbook_registry
            _app.state.tool_registry     = _tool_registry
            _app.state.tool_executor     = _tool_executor
            _app.state.reasoning_engine  = _reasoning_engine

            # CaseService needs the playbook_registry and (optionally) action_gateway.
            # The gateway stack is built below; we pass None here and patch after.
            _case_service_singleton = build_case_service(
                _sb_singleton,
                playbook_registry=_playbook_registry,
            )
            _app.state.case_service = _case_service_singleton
            _LOGGER_PRE.info(
                "sprint1_case_service_warmed supabase_backed=%s playbooks=%d tools=%d",
                _sb_singleton is not None,
                len(_playbook_registry),
                len(_tool_registry),
            )
        except Exception as _exc:  # noqa: BLE001
            _LOGGER_PRE.warning(
                "sprint1_case_service_warmup_failed error=%s — offline mode will be used", _exc
            )
            _app.state.case_service = None

        # ── Sprint 2.18: Pre-warm InvestigationService ────────────────────────
        try:
            from case_engine.investigation import build_investigation_service  # noqa: PLC0415
            _inv_service = build_investigation_service(
                tool_executor=_app.state.tool_executor,
                audit_logger=None,  # case_engine.audit.AuditLogger wired after gateway
            )
            _app.state.investigation_service = _inv_service
            _LOGGER_PRE.info("sprint218_investigation_service_warmed")
        except Exception as _inv_exc:  # noqa: BLE001
            _LOGGER_PRE.warning(
                "sprint218_investigation_service_warmup_failed error=%s", _inv_exc
            )
            _app.state.investigation_service = None

        # ── Action gateway state setup ────────────────────────────────────────
        # Gateway routes use request.app.state.stack / .audit_logger / .authenticator.
        # If values were injected via create_app() (tests), use them as-is.
        # In production, build from environment, reusing the Supabase singleton.
        try:
            if _app.state.stack is None:
                _app.state.stack = _build_gateway_stack_from_env(_sb_singleton)
            if _app.state.processor is None:
                from webhook.freshdesk_processor import build_freshdesk_processor  # noqa: PLC0415
                _app.state.processor = build_freshdesk_processor()
            if _app.state.authenticator is None:
                from security.config import build_authenticator_from_env  # noqa: PLC0415
                _app.state.authenticator = build_authenticator_from_env()
            if _app.state.audit_logger is None and _app.state.audit_service is None:
                from audit.logger import AuditLogger  # noqa: PLC0415
                from audit.service import AuditService  # noqa: PLC0415
                from audit.repository import InMemoryAuditRepository  # noqa: PLC0415
                _repo = InMemoryAuditRepository()
                _app.state.audit_logger = AuditLogger(repository=_repo)
                _app.state.audit_service = AuditService(repository=_repo)
            elif _app.state.audit_logger is not None and _app.state.audit_service is None:
                from audit.service import AuditService  # noqa: PLC0415
                _app.state.audit_service = AuditService(
                    repository=_app.state.audit_logger.repository
                )
            elif _app.state.audit_service is not None and _app.state.audit_logger is None:
                from audit.logger import AuditLogger  # noqa: PLC0415
                _app.state.audit_logger = AuditLogger(
                    repository=_app.state.audit_service._repo
                )
            if _app.state.metrics_service is None and _app.state.stack is not None:
                _app.state.metrics_service = getattr(_app.state.stack, "metrics_service", None)
            # Wire action_gateway into CaseService now that the stack exists.
            if _app.state.case_service is not None and _app.state.stack is not None:
                _app.state.case_service._gateway = getattr(_app.state.stack, "gateway", None)

            # ── Sprint 2.26: Wire workflow services from stack into app.state ──
            # The ProductionRuntime now contains all 7 workflow services. Promote
            # each into app.state so admin endpoints receive real (not None) services.
            _stack = _app.state.stack
            if _stack is not None:
                _wf_service_names = [
                    "clarification_service",
                    "investigation_service",
                    "knowledge_service",
                    "reasoning_service",
                    "action_proposal_service",
                    "action_gateway_service",
                    "execution_service",
                    "workflow_engine",
                    "playbook_registry",
                    "adapter_registry",
                    "adapter_router",
                    # Sprint 2.27.5: Architecture convergence services
                    "router_service",
                    "knowledge_orchestrator",
                    "response_generation_service",
                    "engineering_escalation_service",
                    "support_agent_runtime",
                    "ticket_orchestrator",
                    # Sprint 2.27.9: Multi-tenant resolution stack
                    # Required by FreshdeskTicketCreatedHandler for UNKNOWN_CLIENT detection
                    "tenant_registry",
                    "client_resolver",
                    "tenant_tool_registry",
                ]
                for _svc_name in _wf_service_names:
                    if getattr(_app.state, _svc_name, None) is None:
                        _svc = getattr(_stack, _svc_name, None)
                        if _svc is not None:
                            setattr(_app.state, _svc_name, _svc)
                            _LOGGER_PRE.info("sprint226_service_wired service=%s", _svc_name)

                # Upgrade CaseService to use the fully-wired WorkflowEngine
                if _app.state.case_service is not None:
                    _wf_eng = getattr(_app.state, "workflow_engine", None)
                    if _wf_eng is not None:
                        _app.state.case_service._wf_engine = _wf_eng
                        _LOGGER_PRE.info("sprint226_case_service_workflow_engine_upgraded")

                # Sprint 2.30.1 — Wire HybridRAGProvider into KnowledgeOrchestrator
                # KnowledgeOrchestrator._rag_provider defaults to None → placeholder evidence.
                # Inject the real retriever adapter so RAG retrieval actually executes.
                _ko = getattr(_app.state, "knowledge_orchestrator", None)
                if (
                    _ko is not None
                    and _generator_singleton is not None
                    and hasattr(_generator_singleton, "_retriever")
                    and _generator_singleton._retriever is not None
                ):
                    try:
                        from case_engine.knowledge.rag_adapter import HybridRAGProvider  # noqa: PLC0415
                        _default_tenant = os.getenv("DEFAULT_RAG_TENANT", "unity")
                        _ko._rag_provider = HybridRAGProvider(
                            retriever=_generator_singleton._retriever,
                            default_tenant=_default_tenant,
                        )
                        _LOGGER_PRE.info(
                            "sprint2301_rag_provider_wired retriever=%s tenant=%s",
                            type(_generator_singleton._retriever).__name__,
                            _default_tenant,
                        )
                    except Exception as _rag_exc:  # noqa: BLE001
                        _LOGGER_PRE.warning(
                            "sprint2301_rag_provider_wire_failed error=%s — KnowledgeOrchestrator stays placeholder",
                            _rag_exc,
                        )

                # Sprint 2.30.1 — Upgrade WorkflowEngine to use KnowledgeOrchestrator
                # WorkflowEngine was built before KnowledgeOrchestrator in assembly.py (step 8 vs 12).
                # Patch it now that both are on app.state so the full pipeline executes.
                _wf_eng = getattr(_app.state, "workflow_engine", None)
                _ko = getattr(_app.state, "knowledge_orchestrator", None)
                if _wf_eng is not None and _ko is not None:
                    _wf_eng._knowledge_service = _ko
                    _LOGGER_PRE.info(
                        "sprint2301_workflow_engine_knowledge_upgraded to=KnowledgeOrchestrator"
                    )

            _LOGGER_PRE.info("action_gateway_initialized")
        except Exception as _gw_exc:  # noqa: BLE001
            _LOGGER_PRE.warning(
                "action_gateway_init_failed error=%s — gateway routes will return 503", _gw_exc
            )

        # ── Sprint 2.29 / 2.29.1: Wire Freshdesk production services ────────────
        # Activates POST /webhooks/freshdesk/ticket-created and /ticket-updated.
        # All 7 app.state keys required by api/routes/webhooks/freshdesk.py.
        try:
            from freshdesk.verifier import FreshdeskWebhookVerifier            # noqa: PLC0415
            from freshdesk.idempotency import WebhookIdempotencyStore          # noqa: PLC0415
            from freshdesk.conversation_state import ConversationStateStore    # noqa: PLC0415
            from freshdesk.response_service import FreshdeskResponseService    # noqa: PLC0415
            from freshdesk.client import FreshdeskClient                       # noqa: PLC0415
            from freshdesk.freshdesk_models import FreshdeskConfig             # noqa: PLC0415
            from freshdesk.handlers import (                                   # noqa: PLC0415
                FreshdeskTicketCreatedHandler,
                FreshdeskTicketUpdatedHandler,
            )

            # ── Sprint 2.29.1: Single verifier instance for all Gen 3 routes ─
            # Created ONCE at startup; all requests share the same instance.
            # Switch between direct Freshdesk and n8n by changing only:
            #   FRESHDESK_WEBHOOK_MODE=static  (dev / Freshdesk direct)
            #   FRESHDESK_WEBHOOK_MODE=hmac    (production / n8n in the middle)
            _fd_webhook_secret = os.getenv("FRESHDESK_WEBHOOK_SECRET", "")
            _fd_webhook_mode   = os.getenv("FRESHDESK_WEBHOOK_MODE", "hmac").strip().lower()
            _fd_enforce        = os.getenv("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false").lower() == "true"
            _fd_replay_window  = int(os.getenv("FRESHDESK_WEBHOOK_REPLAY_WINDOW_SECONDS", "300"))
            if _fd_webhook_secret:
                _app.state.freshdesk_verifier = FreshdeskWebhookVerifier(
                    _fd_webhook_secret,
                    mode=_fd_webhook_mode,
                    enforce=_fd_enforce,
                    replay_window_seconds=_fd_replay_window,
                )
                _LOGGER_PRE.info(
                    "sprint2291_freshdesk_verifier_wired mode=%s enforce=%s replay_window=%ss",
                    _fd_webhook_mode, _fd_enforce, _fd_replay_window,
                )
            else:
                _app.state.freshdesk_verifier = None
                _LOGGER_PRE.warning(
                    "sprint2291_freshdesk_verifier_skipped "
                    "FRESHDESK_WEBHOOK_SECRET=MISSING — webhook auth disabled"
                )

            _fd_idem_store = WebhookIdempotencyStore(supabase_client=_sb_singleton)
            _app.state.freshdesk_idempotency_store = _fd_idem_store

            _fd_conv_store = ConversationStateStore(supabase_client=_sb_singleton)
            _app.state.freshdesk_conversation_store = _fd_conv_store

            # Sprint 2.31: Dedicated case_engine.audit.AuditLogger for Freshdesk handlers.
            # handlers.py._audit_event() calls self._audit._write(AuditEntry), which is
            # the case_engine.audit.AuditLogger interface.  app.state.audit_logger is the
            # Action Gateway's audit.logger.AuditLogger (emit() interface) — wrong type.
            # These two audit subsystems are independent; create a separate instance here.
            from case_engine.audit import AuditLogger as _CaseEngineAuditLogger  # noqa: PLC0415
            _fd_case_audit_logger = _CaseEngineAuditLogger(supabase_client=_sb_singleton)

            # ── Sprint 2.29.2: DB table existence probe ────────────────────────
            # Verify that S2_028_freshdesk_foundation.sql has been applied.
            # PostgREST returns a "Could not find table" error when a table is
            # absent — we detect this at startup so the failure is visible
            # immediately rather than buried in per-request error logs.
            _fd_missing_tables: list[str] = []
            if _sb_singleton is not None:
                for _fd_tbl in ("freshdesk_webhook_events", "support_conversation_state"):
                    try:
                        _sb_singleton.table(_fd_tbl).select("*").limit(0).execute()
                        _LOGGER_PRE.info("sprint2292_db_table_ok table=%s", _fd_tbl)
                    except Exception as _tbl_exc:
                        _fd_missing_tables.append(_fd_tbl)
                        _LOGGER_PRE.warning(
                            "sprint2292_db_table_MISSING table=%s error=%s "
                            "— Apply sql/sprint2_migrations/S2_028_freshdesk_foundation.sql "
                            "via Supabase Dashboard SQL Editor",
                            _fd_tbl, str(_tbl_exc)[:300],
                        )
                if _fd_missing_tables:
                    _LOGGER_PRE.warning(
                        "sprint2292_db_migration_required missing_tables=%s "
                        "migration_file=sql/sprint2_migrations/S2_028_freshdesk_foundation.sql "
                        "— Idempotency and conversation persistence are DISABLED until migrated",
                        _fd_missing_tables,
                    )
                else:
                    _LOGGER_PRE.info(
                        "sprint2292_db_migration_verified "
                        "freshdesk_webhook_events=ok support_conversation_state=ok"
                    )

            _fd_domain  = os.getenv("FRESHDESK_DOMAIN", "")
            _fd_api_key = os.getenv("FRESHDESK_API_KEY", "")
            _fd_client  = None
            if _fd_domain and _fd_api_key:
                _fd_config = FreshdeskConfig(
                    domain=_fd_domain,
                    api_key=_fd_api_key,
                    timeout_seconds=float(os.getenv("FRESHDESK_TIMEOUT_SECONDS", "10.0")),
                )
                _fd_client = FreshdeskClient(_fd_config)
                _LOGGER_PRE.info(
                    "sprint229_freshdesk_client_created domain=%s key_prefix=%s",
                    _fd_domain,
                    (_fd_api_key[:4] + "****") if len(_fd_api_key) > 4 else "****",
                )
            else:
                _LOGGER_PRE.warning(
                    "sprint229_freshdesk_client_skipped FRESHDESK_DOMAIN=%s FRESHDESK_API_KEY=%s",
                    "set" if _fd_domain else "MISSING",
                    "set" if _fd_api_key else "MISSING",
                )

            if _fd_client is not None:
                _fd_resp_svc: FreshdeskResponseService | None = FreshdeskResponseService(
                    freshdesk_client=_fd_client,
                    metrics_collector=None,
                    audit_logger=getattr(_app.state, "audit_logger", None),
                )
                _app.state.freshdesk_response_service = _fd_resp_svc
                _LOGGER_PRE.info("sprint229_freshdesk_response_service_wired")
            else:
                _fd_resp_svc = None
                _app.state.freshdesk_response_service = None

            _fd_generator_ref  = _generator_singleton
            _fd_resp_svc_ref   = _fd_resp_svc

            if _fd_generator_ref is not None and _fd_resp_svc_ref is not None:
                from rag_engine.generation.chat_generator import GenerationRequest  # noqa: PLC0415

                async def _freshdesk_rag_processor(
                    ticket_id: str, query_text: str, tenant: str
                ) -> None:
                    try:
                        gen_req = GenerationRequest(
                            query_text=query_text,
                            client=tenant or "unknown",
                            persist_history=False,
                        )
                        loop = asyncio.get_running_loop()
                        gen_result = await loop.run_in_executor(
                            None, _fd_generator_ref.generate, gen_req
                        )
                        answer         = gen_result.answer or ""
                        confidence     = gen_result.confidence
                        requires_human = gen_result.requires_human
                        if requires_human or confidence == "low":
                            note_body = (
                                f"<p><strong>AI Suggestion (requires agent review):"
                                f"</strong><br>{answer}</p>"
                                f"<p><em>Confidence: {confidence}</em></p>"
                            )
                        else:
                            note_body = (
                                f"<p><strong>AI Suggested Response:</strong><br>{answer}</p>"
                                f"<p><em>Confidence: {confidence}</em></p>"
                            )
                        await _fd_resp_svc_ref.add_internal_note(
                            ticket_id, note_body, case_id="", client_id=tenant
                        )
                        _LOGGER_PRE.info(
                            "sprint229_rag_processor note_posted ticket_id=%s confidence=%s requires_human=%s",
                            ticket_id, confidence, requires_human,
                        )
                    except Exception as _rag_exc:  # noqa: BLE001
                        _LOGGER_PRE.error(
                            "sprint229_rag_processor error ticket_id=%s error=%s",
                            ticket_id, _rag_exc,
                        )

                _app.state.freshdesk_rag_processor = _freshdesk_rag_processor
                _LOGGER_PRE.info("sprint229_freshdesk_rag_processor_wired")
            else:
                _app.state.freshdesk_rag_processor = None

            _app.state.freshdesk_ticket_created_handler = FreshdeskTicketCreatedHandler(
                idempotency_store=_fd_idem_store,
                conversation_store=_fd_conv_store,
                ticket_orchestrator=getattr(_app.state, "ticket_orchestrator", None),
                client_resolver=getattr(_app.state, "client_resolver", None),
                audit_logger=_fd_case_audit_logger,
                metrics_collector=None,
            )

            _app.state.freshdesk_ticket_updated_handler = FreshdeskTicketUpdatedHandler(
                idempotency_store=_fd_idem_store,
                conversation_store=_fd_conv_store,
                ticket_orchestrator=getattr(_app.state, "ticket_orchestrator", None),
                audit_logger=_fd_case_audit_logger,
                metrics_collector=None,
            )

            _LOGGER_PRE.info(
                "sprint2291_freshdesk_services_wired "
                "verifier=%s idem=ok conv=ok resp_svc=%s "
                "rag_proc=%s orchestrator=%s client_resolver=%s "
                "case_audit_logger=ok handlers=ok",
                "ok(%s)" % _fd_webhook_mode if _fd_webhook_secret else "SKIPPED(no secret)",
                "ok" if _fd_client is not None else "SKIPPED(no creds)",
                "ok" if _app.state.freshdesk_rag_processor is not None else "SKIPPED(no creds)",
                "ok" if getattr(_app.state, "ticket_orchestrator", None) is not None else "NONE",
                "ok" if getattr(_app.state, "client_resolver", None) is not None else "NONE",
            )

        except Exception as _fd_wiring_exc:  # noqa: BLE001
            _LOGGER_PRE.warning(
                "sprint229_freshdesk_wiring_failed error=%s — routes use offline fallback",
                _fd_wiring_exc,
            )

        # ── Sprint 2.12: Crash recovery — recover stale EXECUTING actions ─────
        _recover_stale_executing_actions(_app.state.stack)

        # ── Sprint 2.12: Background loops ─────────────────────────────────────
        # Only start background loops in production (not during unit tests).
        # Tests use skip_config_validation=True.
        _bg_tasks: list[asyncio.Task] = []  # type: ignore[type-arg]
        if not skip_config_validation:
            _bg_tasks.append(
                asyncio.create_task(
                    _background_worker_loop(_app),
                    name="action_worker_loop",
                )
            )
            _bg_tasks.append(
                asyncio.create_task(
                    _background_watchdog_loop(_app),
                    name="action_watchdog_loop",
                )
            )
            _LOGGER_PRE.info(
                "sprint212_background_loops_started worker_interval=%.0fs watchdog_interval=%.0fs",
                _WORKER_POLL_INTERVAL_S, _WATCHDOG_POLL_INTERVAL_S,
            )

        yield

        # ── Sprint 2.12: Cancel background loops ──────────────────────────────
        for _task in _bg_tasks:
            _task.cancel()
            try:
                await _task
            except asyncio.CancelledError:
                pass
        if _bg_tasks:
            _LOGGER_PRE.info("sprint212_background_loops_stopped count=%d", len(_bg_tasks))

        # ── SPRINT0_FIX_SINGLETON: graceful shutdown ───────────────────────────
        if _generator_singleton is not None:
            try:
                _generator_singleton._llm.close()
                _LOGGER_PRE.info("sprint0_singleton_shutdown llm_client=closed")
            except Exception:  # noqa: BLE001
                pass
        if _embedder_singleton is not None:
            try:
                _embedder_singleton.close()
                _LOGGER_PRE.info("sprint0_singleton_shutdown embedder=closed")
            except Exception:  # noqa: BLE001
                pass
        _LOGGER_PRE.info("service_shutdown")

    return lifespan


# ── SPRINT0_FIX_SINGLETON — helper class and getters ─────────────────────────

class _ThreadSafeEmbedderWrapper:
    """
    SPRINT0_FIX_SINGLETON: Lightweight per-request wrapper around the singleton embedder.

    Created fresh per request (O(1), no connections opened) and handed to the
    retriever in place of a raw OpenAIEmbeddingProvider.  Two responsibilities:

      1. Serialise concurrent embed_single / embed_batch calls via _EMBEDDER_CALL_LOCK.
         OpenAIEmbeddingProvider documents its underlying httpx.Client as not
         thread-safe; the lock ensures only one thread calls it at a time.
         At this system's concurrency level (<5 simultaneous requests) the lock
         wait is negligible compared to the ~300 ms embedding round-trip.

      2. Make close() a no-op so the callers' existing finally-blocks:
             if embedder is not None: await asyncio.to_thread(embedder.close)
         remain safe to run without closing the shared singleton.
    """
    __slots__ = ("_inner", "_lock")

    def __init__(self, inner: Any, lock: _threading.Lock) -> None:
        self._inner = inner
        self._lock = lock

    def embed_single(self, text: str) -> list[float]:
        key = hashlib.sha256(text.encode()).hexdigest()
        with _EMBED_CACHE_LOCK:
            if key in _EMBED_LRU_CACHE:
                _EMBED_LRU_CACHE.move_to_end(key)
                LOGGER.debug("embed_cache=hit key_prefix=%s", key[:8])
                return _EMBED_LRU_CACHE[key]
        LOGGER.debug("sprint0_singleton embedder=warm_reuse cache=miss")
        with self._lock:
            result = self._inner.embed_single(text)
        with _EMBED_CACHE_LOCK:
            _EMBED_LRU_CACHE[key] = result
            _EMBED_LRU_CACHE.move_to_end(key)
            if len(_EMBED_LRU_CACHE) > _EMBED_CACHE_MAX:
                _EMBED_LRU_CACHE.popitem(last=False)
        return result

    def embed_batch(self, texts: list[str]) -> Any:
        with self._lock:
            return self._inner.embed_batch(texts)

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    @property
    def model_name(self) -> str:
        return self._inner.model_name

    def close(self) -> None:
        """No-op: singleton lifecycle is managed by lifespan, not per-request cleanup."""


def _get_supabase_client(app_settings: Any) -> Any:
    """
    SPRINT0_FIX_SINGLETON: Return the module-level Supabase client singleton.

    The underlying httpcore.ConnectionPool handles concurrent HTTP requests safely,
    so no per-call lock is required once the singleton is initialised.
    Initialisation itself uses double-checked locking to prevent duplicate creation
    under concurrent cold-start (should not occur after lifespan pre-warms the client).
    """
    global _sb_singleton
    if _sb_singleton is not None:
        LOGGER.debug("sprint0_singleton supabase=warm_reuse")
        return _sb_singleton
    with _SB_INIT_LOCK:
        if _sb_singleton is None:
            LOGGER.warning(
                "sprint0_singleton supabase=cold_start — lifespan pre-warm may have failed; "
                "creating Supabase client now"
            )
            _sb_singleton = create_client(app_settings.supabase_url, app_settings.supabase_key)
    return _sb_singleton


def _get_embedder_wrapper(
    app_settings: Any,
    rag_settings: Any,
    openai_api_key: str,
) -> _ThreadSafeEmbedderWrapper:
    """
    SPRINT0_FIX_SINGLETON: Return a per-request _ThreadSafeEmbedderWrapper.

    The wrapper delegates to the singleton OpenAIEmbeddingProvider and serialises
    concurrent embed calls. Creating the wrapper is O(1) — no connections are opened.
    """
    global _embedder_singleton
    if _embedder_singleton is None:
        with _EMBEDDER_INIT_LOCK:
            if _embedder_singleton is None:
                LOGGER.warning(
                    "sprint0_singleton embedder=cold_start — lifespan pre-warm may have failed; "
                    "creating OpenAIEmbeddingProvider now"
                )
                _embedder_singleton = OpenAIEmbeddingProvider(
                    api_key=openai_api_key,
                    model=rag_settings.embedding_model,
                    base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                    dimensions=rag_settings.embedding_dimensions,
                    max_retries=rag_settings.embedding_max_retries,
                    retry_base_delay_s=rag_settings.embedding_retry_base_delay_s,
                    retry_max_delay_s=rag_settings.embedding_retry_max_delay_s,
                    connect_timeout_s=rag_settings.embedding_connect_timeout_s,
                    read_timeout_s=rag_settings.embedding_read_timeout_s,
                    write_timeout_s=rag_settings.embedding_write_timeout_s,
                    pool_timeout_s=rag_settings.embedding_pool_timeout_s,
                )
    return _ThreadSafeEmbedderWrapper(_embedder_singleton, _EMBEDDER_CALL_LOCK)


def _get_generator(
    app_settings: Any,
    rag_settings: Any,
    openai_api_key: str,
) -> Any:
    """
    SPRINT0_FIX_SINGLETON: Return the module-level ChatGenerator singleton.

    Double-checked locking guards against concurrent cold-start (should not occur
    after lifespan pre-warms the generator, but protects if pre-warm failed).
    The generator holds the singleton retriever, LLM client (with persistent
    httpx.Client), and history store — all created once and reused per process.
    """
    global _generator_singleton
    if _generator_singleton is not None:
        LOGGER.debug("sprint0_singleton generator=warm_reuse")
        return _generator_singleton
    with _GENERATOR_LOCK:
        if _generator_singleton is None:
            LOGGER.warning(
                "sprint0_singleton generator=cold_start — lifespan pre-warm may have failed; "
                "creating ChatGenerator now"
            )
            _, _generator_singleton, _ = _build_chat_generator(app_settings, rag_settings, openai_api_key)
    return _generator_singleton


def _get_case_service() -> CaseService:
    """
    Sprint 1: Return the CaseService singleton.

    Falls back to offline mode (no DB) if the Supabase singleton is not ready.
    All case engine errors are caught by callers — Phase 1 is never blocked.
    """
    global _case_service_singleton
    if _case_service_singleton is not None:
        return _case_service_singleton
    with _CASE_SERVICE_LOCK:
        if _case_service_singleton is None:
            _case_service_singleton = build_case_service(_sb_singleton)
    return _case_service_singleton


# ── Standalone middleware dispatch functions ──────────────────────────────────
# Defined outside create_app() so they can be registered via app.middleware("http")(fn).

async def _log_requests_dispatch(request: Request, call_next):
    start_time = time.time()
    path = request.url.path
    try:
        response = await call_next(request)
        duration = time.time() - start_time
        record_request(request.method, path, response.status_code, duration)
        logger.info(
            f"Handled {request.method} {path}",
            extra={
                "extra_data": {
                    "method": request.method,
                    "path": path,
                    "status_code": response.status_code,
                    "duration_s": round(duration, 4),
                    "client_ip": request.client.host if request.client else None,
                }
            },
        )
        return response
    except Exception as e:
        duration = time.time() - start_time
        record_request(request.method, path, 500, duration)
        logger.error(
            f"Failed {request.method} {path}: {e}",
            extra={
                "extra_data": {
                    "method": request.method,
                    "path": path,
                    "duration_s": round(duration, 4),
                    "error": str(e),
                }
            },
        )
        raise


async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Path-aware HTTPException handler.

    Gateway routes (/actions, /worker, /watchdog, /audit, /admin, /webhook) use
    {"error": {"code": ..., "message": ...}} response format.  RAG routes keep
    FastAPI's default {"detail": ...} format so existing test assertions are unchanged.
    """
    path = request.url.path
    if any(path.startswith(p) for p in _GATEWAY_PREFIXES):
        detail = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            body = detail
        else:
            body = {"error": {"code": "HTTP_ERROR", "message": str(detail)}}
        return JSONResponse(status_code=exc.status_code, content=body)
    # RAG routes: standard FastAPI {"detail": ...} format
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    LOGGER.exception(
        "unhandled_exception method=%s path=%s type=%s",
        request.method, request.url.path, type(exc).__name__,
    )
    return JSONResponse(
        status_code=500,
        content={"error": "internal_server_error", "message": "An unexpected error occurred."},
    )


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": "validation_error",
            "message": "Request validation failed.",
            "details": exc.errors(),
        },
    )


# ── Application factory ───────────────────────────────────────────────────────

def create_app(
    *,
    stack: Any = None,
    processor: Any = None,
    authenticator: Any = None,
    audit_logger: Any = None,
    audit_service: Any = None,
    audit_repository: Any = None,
    metrics_service: Any = None,
    skip_config_validation: bool = False,
) -> FastAPI:
    """
    Create and return a fully-configured FastAPI application.

    In production, called once at module level: ``app = create_app()``.
    In tests, call with injected stubs: ``create_app(stack=mock_stack, skip_config_validation=True)``.

    Args:
        stack:                  ProductionRuntime for the action gateway (injected in tests).
        processor:              WebhookProcessor (injected in tests).
        authenticator:          ApiKeyAuthenticator (injected in tests).
        audit_logger:           AuditLogger (injected in tests).
        audit_service:          AuditService (injected in tests).
        audit_repository:       AuditRepository (injected in tests).
        metrics_service:        MetricsService (injected in tests).
        skip_config_validation: Set True in tests to bypass startup security checks.
    """
    _docs_enabled = os.getenv("FASTAPI_DOCS_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }

    local_app = FastAPI(
        title="Fuma Docs Ingestion Service",
        version="1.0.0",
        lifespan=_build_lifespan(skip_config_validation=skip_config_validation),
        docs_url="/docs" if _docs_enabled else None,
        redoc_url="/redoc" if _docs_enabled else None,
        openapi_url="/openapi.json" if _docs_enabled else None,
    )

    # Stash injected values so the lifespan can use them (or build from env if None)
    local_app.state.stack            = stack
    local_app.state.processor        = processor
    local_app.state.authenticator    = authenticator
    local_app.state.audit_logger     = audit_logger
    local_app.state.audit_service    = audit_service
    local_app.state.audit_repository = audit_repository
    local_app.state.metrics_service  = metrics_service
    local_app.state.case_service     = None  # set during lifespan startup
    local_app.state.playbook_registry = None  # set during lifespan startup
    local_app.state.tool_registry     = None  # set during lifespan startup
    local_app.state.tool_executor        = None  # set during lifespan startup
    local_app.state.reasoning_engine     = None  # set during lifespan startup
    local_app.state.investigation_service = None  # set during lifespan startup

    # ── Middleware ────────────────────────────────────────────────────────────
    cors_origins = [
        o.strip()
        for o in os.getenv(
            "CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
        ).split(",")
        if o.strip()
    ]
    local_app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-API-Key", "X-Webhook-Token", "Authorization"],
    )

    # Starlette LIFO: last registered @middleware = outermost on inbound requests.
    # auth registered first → innermost of the two @middleware layers.
    local_app.middleware("http")(api_key_auth_middleware)

    # _log_requests_dispatch registered second → outermost of the two, wrapping auth.
    # CRITICAL: must be outer so that 401/429 short-circuit returns from auth middleware
    # are still visible to call_next here and recorded via record_request(). When auth
    # was outer, it returned JSONResponse without calling call_next, which made
    # _log_requests_dispatch unreachable and silently dropped all 401/429 from Prometheus.
    local_app.middleware("http")(_log_requests_dispatch)

    # RequestIdMiddleware via add_middleware → true outermost layer (wraps all @middleware).
    local_app.add_middleware(RequestIdMiddleware)

    # ── Exception handlers ────────────────────────────────────────────────────
    local_app.add_exception_handler(HTTPException, _http_exception_handler)
    local_app.add_exception_handler(Exception, _unhandled_exception_handler)
    local_app.add_exception_handler(RequestValidationError, _validation_exception_handler)

    # ── RAG routes ────────────────────────────────────────────────────────────
    local_app.include_router(_rag_router)

    # ── Case Engine routes (Sprint 2.15) ─────────────────────────────────────
    local_app.include_router(_case_engine_routes.router, tags=["Case Engine"])

    # ── Action gateway routes ─────────────────────────────────────────────────
    # These routes use per-route Depends(require_admin/approver/operator) auth.
    # The global api_key_auth_middleware skips them (see app/security.py _GATEWAY_PREFIXES).
    local_app.include_router(_gw_actions.router,         tags=["Actions"])
    local_app.include_router(_gw_worker.router,          tags=["Worker"])
    local_app.include_router(_gw_watchdog.router,        tags=["Watchdog"])
    local_app.include_router(_gw_audit.router,           tags=["Audit"])
    local_app.include_router(_gw_admin.router,           tags=["Admin"])
    local_app.include_router(_gw_gateway_admin.router,   tags=["Gateway Admin"])
    local_app.include_router(_gw_recovery_admin.router,  tags=["Recovery Admin"])
    local_app.include_router(_wf_admin_routes.router,    tags=["Workflow Admin"])
    local_app.include_router(_tool_admin_routes.router,          tags=["Tool Admin"])
    local_app.include_router(_investigation_admin_routes.router,       tags=["Investigation Admin"])
    local_app.include_router(_action_proposals_admin_routes.router,   tags=["Action Proposal Admin"])
    local_app.include_router(_execution_admin_routes.router,          tags=["Execution Admin"])
    local_app.include_router(_reasoning_admin_routes.router,          tags=["Reasoning Admin"])
    local_app.include_router(_clarification_admin_routes.router,      tags=["Clarification Admin"])
    local_app.include_router(_adapter_admin_routes.router,            tags=["Adapter Admin"])
    local_app.include_router(_tickets_routes.router,                  tags=["Tickets"])
    local_app.include_router(_gw_webhook.router,                 tags=["Webhooks"])
    # Sprint 2.28.1: Freshdesk Foundation Layer webhook receiver
    local_app.include_router(_fd_webhook_routes.router,          tags=["Freshdesk Webhooks"])

    # ── Gateway health and metrics (under /gateway prefix to avoid path conflicts) ──
    # /gateway/health, /gateway/health/live, /gateway/health/ready — gateway runtime health
    # /gateway/metrics — action gateway Prometheus metrics (from metrics_service)
    # /gateway/* bypasses the global rate limiter and auth middleware
    # (see app/security.py _GATEWAY_PREFIXES which includes "/gateway")
    local_app.include_router(_gw_health.router,   prefix="/gateway", tags=["Gateway Health"])
    local_app.include_router(_gw_metrics.router,  prefix="/gateway", tags=["Gateway Metrics"])

    return local_app


# ── Request / response models ─────────────────────────────────────────────────

class IngestRequest(BaseModel):
    full_reindex: bool = False
    repo_url: str | None = None
    ref: str | None = None
    file_path: str | None = None
    excel_sheet: str | None = None
    freshdesk_updated_since: str | None = None
    freshdesk_updated_until: str | None = None
    freshdesk_ticket_ids: list[int] | None = None
    freshdesk_apply_filters_with_ticket_ids: bool = False
    freshdesk_ticket_types: list[str] | None = None
    freshdesk_requester_ids: list[int] | None = None
    freshdesk_responder_ids: list[int] | None = None
    freshdesk_group_ids: list[int] | None = None
    freshdesk_statuses: list[str] | None = None
    freshdesk_priorities: list[str] | None = None


class QueryRequest(BaseModel):
    query_text: str
    match_count: int = Field(default=5, ge=1, le=50)
    match_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    source_thresholds: dict[str, float] | None = None
    source_types: list[str] | None = None
    tenant: str | None = None
    access_scope: str | None = None
    updated_at_from: str | None = None
    updated_at_to: str | None = None
    strict_latest_within_top_n: bool | None = None

    @field_validator("source_thresholds")
    @classmethod
    def validate_source_thresholds(cls, value: dict[str, float] | None) -> dict[str, float] | None:
        if value is None:
            return value
        cleaned: dict[str, float] = {}
        for source_type, threshold in value.items():
            key = source_type.strip().lower()
            if not key:
                raise ValueError("source_thresholds keys must be non-empty source type strings")
            if threshold < 0.0 or threshold > 1.0:
                raise ValueError(f"source_thresholds[{source_type}] must be between 0.0 and 1.0")
            cleaned[key] = float(threshold)
        return cleaned


class ChatRequest(BaseModel):
    query_text: str = Field(min_length=1)
    session_id: str | None = None
    match_count: int = Field(default=5, ge=1, le=20)
    match_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    source_thresholds: dict[str, float] | None = None
    source_types: list[str] | None = None
    tenant: str | None = None
    access_scope: str | None = None
    updated_at_from: str | None = None
    updated_at_to: str | None = None
    strict_latest_within_top_n: bool | None = None
    history_turns: int | None = Field(default=None, ge=0, le=20)
    persist_history: bool = True

    @field_validator("source_thresholds")
    @classmethod
    def validate_source_thresholds(cls, value: dict[str, float] | None) -> dict[str, float] | None:
        if value is None:
            return value
        cleaned: dict[str, float] = {}
        for source_type, threshold in value.items():
            key = source_type.strip().lower()
            if not key:
                raise ValueError("source_thresholds keys must be non-empty source type strings")
            if threshold < 0.0 or threshold > 1.0:
                raise ValueError(f"source_thresholds[{source_type}] must be between 0.0 and 1.0")
            cleaned[key] = float(threshold)
        return cleaned


class DraftCardModel(BaseModel):
    title: str | None = None
    summary: str | None = None
    content: str = ""
    tags: list[str] = Field(default_factory=list)
    tenant: str | None = None
    access_scope: str | None = None
    suggested_questions: list[str] = Field(default_factory=list)


class TrainChatRequest(BaseModel):
    query_text: str = Field(min_length=1)
    session_id: str | None = None
    draft: DraftCardModel | None = None
    tenant: str | None = None
    access_scope: str | None = None
    history_turns: int | None = Field(default=None, ge=0, le=20)
    persist_history: bool = True


class CommitCardRequest(BaseModel):
    session_id: str = Field(min_length=1)
    draft: DraftCardModel


class RagChatRequest(BaseModel):
    query_text: str = Field(min_length=1)
    client: str = Field(min_length=1)
    session_id: str | None = None
    top_k: int = Field(default=8, ge=1, le=20)
    similarity_threshold: float = Field(default=0.27, ge=0.0, le=1.0)
    history_turns: int = Field(default=6, ge=0, le=20)
    persist_history: bool = True


# ── Trivial read-only endpoints — no blocking I/O ─────────────────────────────

@_rag_router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@_rag_router.get("/health/live")
async def health_live() -> dict:
    """
    Liveness probe — returns 200 while the process is running.

    Used by Kubernetes/Docker to detect if the pod needs restarting.
    Never touches external systems.
    """
    return {
        "alive": True,
        "service": "kwikid-ai-ingest",
        "checked_at": datetime.now(tz=timezone.utc).isoformat(),
    }


@_rag_router.get("/health/ready")
async def health_ready() -> dict:
    """
    Readiness probe — returns 200 when the service can serve requests.

    Checks Supabase connectivity. Returns 503 with details if any check fails.
    """
    settings = get_settings()
    checks: dict[str, Any] = {}
    ok = True

    def _check_supabase() -> bool:
        store = VectorStore(
            supabase_url=settings.supabase_url,
            supabase_key=settings.supabase_key,
            table_name=settings.supabase_table,
            local_fallback_max_rows=settings.local_match_fallback_max_rows,
        )
        return store.healthcheck()

    try:
        checks["supabase"] = {"healthy": await asyncio.to_thread(_check_supabase)}
    except Exception as exc:
        LOGGER.error("health_ready check=supabase type=%s", type(exc).__name__, exc_info=True)
        checks["supabase"] = {"healthy": False, "error": "dependency_check_failed"}
        ok = False

    try:
        from api.health_providers import ConfigHealthProvider  # noqa: PLC0415
        _cfg = ConfigHealthProvider().check()
        checks["config"] = {"healthy": _cfg.healthy, "message": _cfg.message}
        if not _cfg.healthy:
            ok = False
    except Exception as exc:
        LOGGER.warning("health_ready check=config type=%s", type(exc).__name__)
        checks["config"] = {"healthy": True, "message": "Config check skipped"}

    if not all(v.get("healthy", False) for v in checks.values()):
        ok = False

    body: dict[str, Any] = {
        "ready": ok,
        "checks": checks,
        "checked_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    if not ok:
        raise HTTPException(status_code=503, detail=body)
    return body


@_rag_router.get("/freshdesk/filter-options")
def freshdesk_filter_options() -> dict[str, Any]:
    status_options = [{"value": label, "label": label.title()} for _, label in sorted(STATUS_MAP.items())]
    priority_options = [{"value": label, "label": label.title()} for _, label in sorted(PRIORITY_MAP.items())]
    return {
        "ticket_types": [],
        "statuses": status_options,
        "priorities": priority_options,
    }


@_rag_router.get("/metrics")
async def metrics_endpoint():
    # PROMETHEUS_ENABLED is informational only — the endpoint is always active so
    # Prometheus can scrape without requiring service restarts when enabling metrics.
    # When metrics are disabled or prometheus_client is not installed, return a valid
    # but empty Prometheus text body (Prometheus treats this as "target up, no data").
    result = get_metrics_response()
    if result is None:
        return Response(
            content=(
                "# KwikID AI Ingest Service\n"
                "# Metrics collection disabled. Set PROMETHEUS_ENABLED=true to enable.\n"
            ),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )
    data, content_type = result
    return Response(content=data, media_type=content_type)


# ── Readiness — async because it makes real network calls ─────────────────────

@_rag_router.get(
    "/ready",
    responses={503: {"description": "Dependencies not ready"}},
)
async def ready() -> dict[str, Any]:
    settings = get_settings()
    checks: dict[str, Any] = {}
    ok = True

    def _check_supabase() -> bool:
        store = VectorStore(
            supabase_url=settings.supabase_url,
            supabase_key=settings.supabase_key,
            table_name=settings.supabase_table,
            local_fallback_max_rows=settings.local_match_fallback_max_rows,
        )
        return store.healthcheck()

    def _check_embeddings() -> bool:
        embeddings = EmbeddingClient(
            provider=settings.embedding_provider,
            api_key=settings.embedding_api_key,
            model=settings.embedding_model,
            base_url=settings.embedding_base_url,
            timeout_s=settings.embedding_timeout_s,
            max_retries=settings.embedding_max_retries,
            retry_base_delay_s=settings.embedding_retry_base_delay_s,
        )
        return embeddings.healthcheck()

    try:
        checks["supabase"] = {"ok": await asyncio.to_thread(_check_supabase)}
    except Exception as exc:  # noqa: BLE001
        # Log full detail server-side; never expose internal error strings to callers.
        # /ready is unauthenticated — str(exc) could leak Supabase URLs / connection info.
        LOGGER.error("ready_check_failed check=supabase type=%s", type(exc).__name__, exc_info=True)
        checks["supabase"] = {"ok": False, "error": "dependency_check_failed"}
        ok = False

    try:
        checks["embeddings"] = {"ok": await asyncio.to_thread(_check_embeddings)}
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("ready_check_failed check=embeddings type=%s", type(exc).__name__, exc_info=True)
        checks["embeddings"] = {"ok": False, "error": "dependency_check_failed"}
        ok = False

    if not all(v.get("ok", False) for v in checks.values()):
        ok = False

    if not ok:
        raise HTTPException(status_code=503, detail={"status": "not_ready", "checks": checks})
    return {"status": "ready", "checks": checks}


# ── Ingest ────────────────────────────────────────────────────────────────────

@_rag_router.post(
    "/ingest",
    responses={
        500: {"description": "Ingestion failed"},
        207: {"description": "Ingestion completed with partial success"},
    },
)
async def ingest_docs(payload: IngestRequest) -> dict[str, Any]:
    request_id = str(uuid.uuid4())
    LOGGER.info("ingest_request request_id=%s file_path=%s", request_id, payload.file_path or "")
    settings = get_settings()
    try:
        result = await asyncio.to_thread(
            run_ingest,
            settings,
            repo_url=payload.repo_url,
            ref=payload.ref,
            full_reindex=payload.full_reindex,
            file_path=payload.file_path,
            freshdesk_updated_since=payload.freshdesk_updated_since,
            freshdesk_updated_until=payload.freshdesk_updated_until,
            freshdesk_ticket_ids=payload.freshdesk_ticket_ids,
            freshdesk_apply_filters_with_ticket_ids=payload.freshdesk_apply_filters_with_ticket_ids,
            freshdesk_ticket_types=payload.freshdesk_ticket_types,
            freshdesk_requester_ids=payload.freshdesk_requester_ids,
            freshdesk_responder_ids=payload.freshdesk_responder_ids,
            freshdesk_group_ids=payload.freshdesk_group_ids,
            freshdesk_statuses=payload.freshdesk_statuses,
            freshdesk_priorities=payload.freshdesk_priorities,
            excel_sheet=payload.excel_sheet,
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("ingest_unhandled request_id=%s", request_id)
        raise HTTPException(
            status_code=500,
            detail={
                "status": "failed",
                "message": str(exc),
                "error_type": type(exc).__name__,
                "errors": [str(exc)],
            },
        ) from exc

    out: dict[str, Any] = {
        "status": result.status,
        "files_scanned": result.files_scanned,
        "chunks_total": result.chunks_total,
        "chunks_created": result.chunks_created,
        "chunks_updated": result.chunks_updated,
        "chunks_skipped": result.chunks_skipped,
        "failed_batches": result.failed_batches,
        "duration_ms": result.duration_ms,
        "repo_commit_sha": result.repo_commit_sha,
        "errors": result.errors,
        "accepted_documents": result.accepted_documents,
        "quarantined_documents": result.quarantined_documents,
        "duplicate_documents": result.duplicate_documents,
        "quarantine_report_path": result.quarantine_report_path,
    }
    if result.excel is not None:
        out["excel"] = result.excel
    if result.status == "failed":
        LOGGER.error("ingest_failed request_id=%s errors=%d", request_id, len(result.errors))
        raise HTTPException(status_code=500, detail=out)
    if result.status == "partial_success":
        LOGGER.warning("ingest_partial request_id=%s errors=%d", request_id, len(result.errors))
        raise HTTPException(status_code=207, detail=out)
    LOGGER.info("ingest_success request_id=%s files_scanned=%s", request_id, result.files_scanned)
    return out


# ── Query ─────────────────────────────────────────────────────────────────────

@_rag_router.post("/query")
async def query_docs(payload: QueryRequest) -> dict[str, Any]:
    trace = tracer.start_trace(
        query=payload.query_text,
        tenant=payload.tenant,
        access_scope=payload.access_scope,
    )
    settings = get_settings()

    result = await asyncio.to_thread(
        run_query,
        settings,
        payload.query_text,
        match_count=payload.match_count,
        match_threshold=payload.match_threshold,
        source_thresholds=payload.source_thresholds,
        source_types=payload.source_types,
        tenant=payload.tenant,
        access_scope=payload.access_scope,
        updated_at_from=payload.updated_at_from,
        updated_at_to=payload.updated_at_to,
        strict_latest_within_top_n=payload.strict_latest_within_top_n,
    )

    query_hash = hashlib.sha256(payload.query_text.encode("utf-8")).hexdigest()[:16]
    LOGGER.info(
        "query_trace query_hash=%s filters=%s returned=%d insufficient=%s "
        "best_similarity=%s best_rerank=%s",
        query_hash,
        {
            "source_types": payload.source_types,
            "source_thresholds": payload.source_thresholds,
            "tenant": payload.tenant,
            "access_scope": payload.access_scope,
            "updated_at_from": payload.updated_at_from,
            "updated_at_to": payload.updated_at_to,
        },
        len(result.matches),
        result.insufficient_context,
        result.diagnostics.get("best_similarity"),
        result.diagnostics.get("best_rerank_score"),
    )

    record_retrieval_candidates("query_returned", len(result.matches))

    trace.query_hash = query_hash
    trace.retrieval_candidates_count = len(result.matches)
    trace.top_matches = [
        ChunkMetadata(
            id=str(m.get("id") if isinstance(m, dict) else getattr(m, "id", "unknown")),
            source_type=str(
                (m.get("metadata") or {}).get("source_type", "unknown")
                if isinstance(m, dict)
                else (getattr(m, "metadata", {}) or {}).get("source_type", "unknown")
            ),
            similarity=float(
                m.get("similarity", 0.0) if isinstance(m, dict) else getattr(m, "similarity", 0.0)
            ),
            rerank_score=(
                (m.get("metadata") or {}).get("rerank_score")
                if isinstance(m, dict)
                else (getattr(m, "metadata", {}) or {}).get("rerank_score")
            ),
            title=(
                (m.get("metadata") or {}).get("title")
                if isinstance(m, dict)
                else (getattr(m, "metadata", {}) or {}).get("title")
            ),
        )
        for m in result.matches
    ]
    trace.retrieval_latency_ms = float(result.diagnostics.get("duration_ms", 0.0))
    tracer.save_trace(trace)

    return {
        "matches": result.matches,
        "matches_by_source_type": result.matches_by_source_type,
        "insufficient_context": result.insufficient_context,
        "clarification": result.clarification,
        "diagnostics": result.diagnostics,
    }


# ── Chat ──────────────────────────────────────────────────────────────────────

@_rag_router.post("/chat")
async def chat_endpoint(payload: ChatRequest) -> dict[str, Any]:
    trace = tracer.start_trace(
        query=payload.query_text,
        session_id=payload.session_id,
        tenant=payload.tenant,
        access_scope=payload.access_scope,
    )
    settings = get_settings()
    try:
        result = await asyncio.to_thread(
            run_chat,
            settings,
            payload.query_text,
            session_id=payload.session_id,
            match_count=payload.match_count,
            match_threshold=payload.match_threshold,
            source_thresholds=payload.source_thresholds,
            source_types=payload.source_types,
            tenant=payload.tenant,
            access_scope=payload.access_scope,
            updated_at_from=payload.updated_at_from,
            updated_at_to=payload.updated_at_to,
            strict_latest_within_top_n=payload.strict_latest_within_top_n,
            chat_history_turns=payload.history_turns,
            persist_history=payload.persist_history,
            supabase_client=_get_supabase_client(settings),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"status": "bad_request", "error": str(exc)},
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail={"status": "upstream_error", "error": str(exc)},
        ) from exc

    query_hash = hashlib.sha256(payload.query_text.encode("utf-8")).hexdigest()[:16]
    LOGGER.info(
        "chat_trace query_hash=%s session_id=%s confidence=%s insufficient=%s citations=%d",
        query_hash, result.session_id, result.confidence,
        result.insufficient_context, len(result.citations),
    )

    trace.query_hash = query_hash
    trace.llm_response = result.answer
    trace.confidence_score = result.confidence if isinstance(result.confidence, (int, float)) else None
    trace.citations = result.citations
    trace.top_matches = [
        ChunkMetadata(
            id=str(m.get("id") if isinstance(m, dict) else getattr(m, "id", "unknown")),
            source_type=str(
                (m.get("metadata") or {}).get("source_type", "unknown")
                if isinstance(m, dict)
                else (getattr(m, "metadata", {}) or {}).get("source_type", "unknown")
            ),
            similarity=float(
                m.get("similarity", 0.0) if isinstance(m, dict) else getattr(m, "similarity", 0.0)
            ),
        )
        for m in result.matches
    ]
    trace.metadata = result.diagnostics
    tracer.save_trace(trace)

    return {
        "session_id": result.session_id,
        "message_id": result.message_id,
        "answer": result.answer,
        "confidence": result.confidence,
        "citations": result.citations,
        "follow_up_question": result.follow_up_question,
        "insufficient_context": result.insufficient_context,
        "matches": result.matches,
        "diagnostics": result.diagnostics,
    }


@_rag_router.get("/chat/suggestions")
async def chat_suggestions(
    limit: int = 6,
    tenant: str | None = None,
    access_scope: str | None = None,
) -> dict[str, Any]:
    if limit < 1 or limit > 20:
        raise HTTPException(
            status_code=400,
            detail={"status": "bad_request", "error": "limit must be between 1 and 20"},
        )
    settings = get_settings()
    try:
        suggestions = await asyncio.to_thread(
            get_suggestions,
            settings,
            tenant=tenant,
            access_scope=access_scope,
            limit=limit,
            supabase_client=_get_supabase_client(settings),
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("chat_suggestions failed")
        raise HTTPException(
            status_code=500,
            detail={"status": "error", "error": str(exc)},
        ) from exc
    return {"suggestions": suggestions}


# ── Train ─────────────────────────────────────────────────────────────────────

def _draft_model_to_dataclass(model: DraftCardModel | None) -> DraftKnowledgeCard:
    if model is None:
        return DraftKnowledgeCard()
    return DraftKnowledgeCard(
        title=(model.title or None),
        summary=(model.summary or None),
        content=model.content or "",
        tags=list(model.tags or []),
        tenant=(model.tenant or None),
        access_scope=(model.access_scope or None),
        suggested_questions=list(model.suggested_questions or []),
    )


@_rag_router.post("/train/chat")
async def train_chat(payload: TrainChatRequest) -> dict[str, Any]:
    settings = get_settings()
    previous_draft = _draft_model_to_dataclass(payload.draft)
    try:
        result = await asyncio.to_thread(
            run_train_chat,
            settings,
            query_text=payload.query_text,
            session_id=payload.session_id,
            previous_draft=previous_draft,
            tenant=payload.tenant,
            access_scope=payload.access_scope,
            history_turns=payload.history_turns,
            persist_history=payload.persist_history,
            supabase_client=_get_supabase_client(settings),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"status": "bad_request", "error": str(exc)},
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail={"status": "upstream_error", "error": str(exc)},
        ) from exc

    LOGGER.info(
        "train_trace session_id=%s confidence=%s word_count=%s",
        result.session_id, result.confidence, result.diagnostics.get("word_count"),
    )
    return {
        "session_id": result.session_id,
        "message_id": result.message_id,
        "assistant_reply": result.assistant_reply,
        "draft": result.draft.to_dict(),
        "follow_up_question": result.follow_up_question,
        "confidence": result.confidence,
        "diagnostics": result.diagnostics,
    }


@_rag_router.post("/train/commit")
async def train_commit(payload: CommitCardRequest) -> dict[str, Any]:
    settings = get_settings()
    draft = _draft_model_to_dataclass(payload.draft)
    try:
        result = await asyncio.to_thread(
            commit_knowledge_card,
            settings,
            session_id=payload.session_id,
            draft=draft,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"status": "bad_request", "error": str(exc)},
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail={"status": "upstream_error", "error": str(exc)},
        ) from exc
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("train_commit failed")
        raise HTTPException(
            status_code=500,
            detail={"status": "error", "error": str(exc)},
        ) from exc

    return {
        "card_id": result.card_id,
        "chunk_ids": result.chunk_ids,
        "chunks_created": result.chunks_created,
        "chunks_updated": result.chunks_updated,
        "chunks_skipped": result.chunks_skipped,
    }


@_rag_router.get("/train/cards")
async def train_cards_list(
    limit: int = 20,
    offset: int = 0,
    tenant: str | None = None,
    access_scope: str | None = None,
    status: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    if limit < 1 or limit > 100:
        raise HTTPException(
            status_code=400,
            detail={"status": "bad_request", "error": "limit must be between 1 and 100"},
        )
    if offset < 0:
        raise HTTPException(
            status_code=400,
            detail={"status": "bad_request", "error": "offset must be >= 0"},
        )
    if status is not None and status not in {"draft", "committed", "archived"}:
        raise HTTPException(
            status_code=400,
            detail={"status": "bad_request", "error": "status must be draft|committed|archived"},
        )

    settings = get_settings()
    try:
        cards, total = await asyncio.to_thread(
            list_knowledge_cards,
            settings,
            limit=limit,
            offset=offset,
            tenant=tenant,
            access_scope=access_scope,
            status=status,
            session_id=session_id,
            supabase_client=_get_supabase_client(settings),
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("train_cards_list failed")
        raise HTTPException(
            status_code=500,
            detail={"status": "error", "error": str(exc)},
        ) from exc

    return {
        "items": [
            {
                "id": card.id,
                "session_id": card.session_id,
                "title": card.title,
                "summary": card.summary,
                "tags": card.tags,
                "tenant": card.tenant,
                "access_scope": card.access_scope,
                "status": card.status,
                "suggested_questions": card.suggested_questions,
                "chunk_ids": card.chunk_ids,
                "content": card.content,
                "created_at": card.created_at,
                "updated_at": card.updated_at,
            }
            for card in cards
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@_rag_router.get("/train/cards/{card_id}")
async def train_cards_get(card_id: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        card = await asyncio.to_thread(get_knowledge_card, settings, card_id, supabase_client=_get_supabase_client(settings))
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("train_cards_get failed")
        raise HTTPException(
            status_code=500,
            detail={"status": "error", "error": str(exc)},
        ) from exc
    if card is None:
        raise HTTPException(
            status_code=404,
            detail={"status": "not_found", "error": f"card {card_id} not found"},
        )
    return {
        "id": card.id,
        "session_id": card.session_id,
        "title": card.title,
        "summary": card.summary,
        "tags": card.tags,
        "tenant": card.tenant,
        "access_scope": card.access_scope,
        "status": card.status,
        "suggested_questions": card.suggested_questions,
        "chunk_ids": card.chunk_ids,
        "content": card.content,
        "created_at": card.created_at,
        "updated_at": card.updated_at,
    }


@_rag_router.delete("/train/cards/{card_id}")
async def train_cards_delete(card_id: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        result = await asyncio.to_thread(delete_knowledge_card, settings, card_id, supabase_client=_get_supabase_client(settings))
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("train_cards_delete failed")
        raise HTTPException(
            status_code=500,
            detail={"status": "error", "error": str(exc)},
        ) from exc
    if result.get("status") == "missing":
        raise HTTPException(
            status_code=404,
            detail={"status": "not_found", "error": f"card {card_id} not found"},
        )
    return result


# ── B1 RAG chat ───────────────────────────────────────────────────────────────

def _build_chat_generator(
    app_settings: Any,
    rag_settings: Any,
    openai_api_key: str,
) -> tuple[Any, Any, Any]:
    """
    Build (embedder, generator, supabase) for B1 RAG pipeline.

    SPRINT0_FIX_SINGLETON: supabase and the underlying OpenAI embedding client
    are now module-level singletons — no new TCP/TLS connections per request.
    The returned embedder is a _ThreadSafeEmbedderWrapper whose close() is a no-op,
    so the callers' existing finally-blocks remain correct without any changes:
        if embedder is not None: await asyncio.to_thread(embedder.close)

    Selects HybridTicketRetriever when B1_HYBRID_RETRIEVAL_ENABLED=true,
    otherwise uses the pure-semantic TicketRetriever.
    """
    # SPRINT0_FIX_SINGLETON: reuse module-level singletons — eliminates ~2.4 s
    # TCP+TLS cold-start observed per request in the Sprint 0 benchmark.
    supabase = _get_supabase_client(app_settings)
    embedder = _get_embedder_wrapper(app_settings, rag_settings, openai_api_key)

    if _B1_HYBRID_ENABLED:
        from rag_engine.retrieval.hybrid_ticket_retriever import HybridTicketRetriever  # noqa: PLC0415
        retriever = HybridTicketRetriever(
            supabase_client=supabase,
            embedding_provider=embedder,
            ticket_chunks_table=rag_settings.ticket_chunks_table,
            sop_chunks_table=rag_settings.sop_chunks_table,
        )
    else:
        retriever = TicketRetriever(
            supabase_client=supabase,
            embedding_provider=embedder,
            ticket_chunks_table=rag_settings.ticket_chunks_table,
            sop_chunks_table=rag_settings.sop_chunks_table,
        )

    llm_client = B1LLMClient(
        api_key=app_settings.chat_api_key,
        model=app_settings.chat_model,
        base_url=app_settings.chat_base_url,
        timeout_s=app_settings.chat_timeout_s,
        max_retries=app_settings.chat_max_retries,
        retry_base_delay_s=app_settings.chat_retry_base_delay_s,
        temperature=app_settings.chat_temperature,
        max_output_tokens=app_settings.chat_max_output_tokens,
    )

    history_store = B1HistoryStore(supabase, table_name=app_settings.chat_history_table)

    generator = ChatGenerator(
        retriever=retriever,
        llm_client=llm_client,
        history_store=history_store,
        per_chunk_max_chars=app_settings.chat_context_chunk_max_chars,
    )

    return embedder, generator, supabase


@_rag_router.post("/rag/chat")
async def rag_chat(payload: RagChatRequest, request: Request) -> dict[str, Any]:
    """
    B1 RAG chat endpoint with tenant isolation and rate limiting.

    Uses HybridTicketRetriever (semantic + FTS) when B1_HYBRID_RETRIEVAL_ENABLED=true,
    otherwise pure-semantic TicketRetriever.

    Rate limited per IP: RAG_CHAT_RATE_LIMIT requests per 60 seconds.
    """
    # ── Rate limiting ─────────────────────────────────────────────────────────
    client_ip = request.client.host if request.client else "unknown"
    limiter = pick_limiter(get_limiters(), "/rag/chat")
    if not limiter.is_allowed(client_ip):
        record_rate_limit_rejection("/rag/chat")
        raise HTTPException(
            status_code=429,
            detail={"error": "rate_limited", "message": "Too many requests. Please slow down."},
        )

    # ── Query routing (gated by ENABLE_QUERY_ROUTER) ──────────────────────────
    route_result = _QUERY_ROUTER.classify(payload.query_text)

    app_settings = get_settings()
    rag_settings = get_rag_settings()
    openai_api_key = os.getenv("OPENAI_API_KEY", app_settings.chat_api_key).strip()

    try:
        generator = _get_generator(app_settings, rag_settings, openai_api_key)
        gen_request = GenerationRequest(
            query_text=payload.query_text,
            client=payload.client,
            session_id=payload.session_id,
            top_k=payload.top_k,
            similarity_threshold=payload.similarity_threshold,
            persist_history=payload.persist_history,
            history_turns=payload.history_turns,
            # ACTIVE_INDEX_VERSION is the single source of truth for retrieval.
            # B1_INDEX_VERSION (rag_settings.index_version) controls ingestion writes
            # and must NOT be used here — it would silently serve v1 after a v2 flip.
            index_version=app_settings.active_index_version,
        )
        result = await asyncio.to_thread(generator.generate, gen_request)

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"status": "bad_request", "error": str(exc)},
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail={"status": "upstream_error", "error": str(exc)},
        ) from exc

    LOGGER.info(
        "rag_chat client=%s session=%s confidence=%s chunks=%d insufficient=%s hybrid=%s route=%s",
        payload.client, result.session_id, result.confidence,
        len(result.chunks), result.insufficient_context, _B1_HYBRID_ENABLED,
        route_result.route if route_result.router_enabled else "router_disabled",
    )
    record_retrieval_candidates("rag_chat_chunks", len(result.chunks))

    diagnostics = result.diagnostics
    if route_result.router_enabled:
        diagnostics = {
            **diagnostics,
            "query_route": route_result.route,
            "routing_confidence": route_result.confidence,
            "retrieval_strategy": route_result.retrieval_strategy,
        }

    return {
        "session_id": result.session_id,
        "message_id": result.message_id,
        "answer": result.answer,
        "confidence": result.confidence,
        "confidence_score": result.confidence_score,
        "requires_human": result.requires_human,
        "citations": result.citations,
        "follow_up_question": result.follow_up_question,
        "insufficient_context": result.insufficient_context,
        "chunks": result.chunks,
        "diagnostics": diagnostics,
    }


@_rag_router.post("/rag/chat/stream")
async def rag_chat_stream(payload: RagChatRequest, request: Request) -> StreamingResponse:
    """
    B1 RAG streaming chat endpoint — SSE token-by-token delivery.

    Retrieval runs synchronously in a thread (~3 s); LLM answer tokens stream
    back immediately thereafter, eliminating the 30 s TTFB of the blocking path.

    Event types:
      {"type": "metadata", "session_id": ..., "confidence": ..., "requires_human": ...}
      {"type": "token", "content": "<delta>"}
      {"type": "done", "answer": "<full text>", "session_id": ..., "message_id": ...}
      {"type": "error", "message": "..."}

    Rate limited per IP: shared with /rag/chat (RAG_CHAT_RATE_LIMIT req/60 s).
    """
    client_ip = request.client.host if request.client else "unknown"
    limiter = pick_limiter(get_limiters(), "/rag/chat")
    if not limiter.is_allowed(client_ip):
        record_rate_limit_rejection("/rag/chat")
        raise HTTPException(
            status_code=429,
            detail={"error": "rate_limited", "message": "Too many requests. Please slow down."},
        )

    app_settings = get_settings()
    rag_settings = get_rag_settings()
    openai_api_key = os.getenv("OPENAI_API_KEY", app_settings.chat_api_key).strip()

    try:
        generator = _get_generator(app_settings, rag_settings, openai_api_key)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail={"error": str(exc)}) from exc

    gen_request = GenerationRequest(
        query_text=payload.query_text,
        client=payload.client,
        session_id=payload.session_id,
        top_k=payload.top_k,
        similarity_threshold=payload.similarity_threshold,
        persist_history=payload.persist_history,
        history_turns=payload.history_turns,
        index_version=app_settings.active_index_version,
    )

    return StreamingResponse(
        generator.stream_generate(gen_request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Freshdesk webhook helpers ─────────────────────────────────────────────────

async def _post_escalation_tcp_note(
    case: Any,
    cs: Any,
    ticket_id: str,
    reply_client: Any,
) -> None:
    """Post a Transfer Context Payload as a Freshdesk private note. Never raises.

    Called whenever a case enters ESCALATED state so the receiving agent has
    full diagnostic context without asking for it again.
    """
    if reply_client is None:
        LOGGER.warning(
            "case_engine.tcp_no_client ticket=%s — FD credentials not configured, skipping TCP note",
            ticket_id,
        )
        return
    try:
        tcp  = await asyncio.to_thread(cs.build_transfer_context, case)
        html = tcp.to_html_note()
        await asyncio.to_thread(reply_client.post_note, ticket_id, html, private=True)
        await asyncio.to_thread(
            cs.record_note_posted, case,
            note_type="transfer_context", confidence="escalated",
        )
        LOGGER.info(
            "case_engine.tcp_posted ticket=%s case=%s state=%s",
            ticket_id, case.case_id, case.current_state.value,
        )
    except Exception as exc:
        LOGGER.warning(
            "case_engine.tcp_post_failed ticket=%s case=%s error=%s",
            ticket_id, getattr(case, "case_id", "unknown"), exc,
        )


# ── Freshdesk webhook ─────────────────────────────────────────────────────────

@_rag_router.post("/freshdesk/webhook")
async def freshdesk_webhook(request: Request) -> dict[str, Any]:
    """
    Freshdesk webhook receiver.

    Rate limited: 60 requests per 60 seconds per IP.
    HMAC validation is enforced when FRESHDESK_WEBHOOK_SECRET is set.
    """
    # ── Rate limiting ─────────────────────────────────────────────────────────
    client_ip = request.client.host if request.client else "unknown"
    limiter = pick_limiter(get_limiters(), "/freshdesk/webhook")
    if not limiter.is_allowed(client_ip):
        record_rate_limit_rejection("/freshdesk/webhook")
        raise HTTPException(status_code=429, detail={"error": "rate_limited"})

    app_settings = get_settings()

    if not app_settings.freshdesk_webhook_enabled:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "webhook_disabled",
                "message": "Set FRESHDESK_WEBHOOK_ENABLED=true to enable.",
            },
        )

    # ── Read raw body (needed for HMAC before JSON parse) ─────────────────────
    raw_body = await request.body()

    # ── HMAC validation ───────────────────────────────────────────────────────
    if app_settings.freshdesk_webhook_secret:
        token = request.headers.get("X-Webhook-Token", "")
        if not verify_webhook_token(
            raw_body,
            provided_token=token,
            expected_secret=app_settings.freshdesk_webhook_secret,
            mode=app_settings.freshdesk_webhook_mode,
        ):
            reason = "missing_header" if not token else "token_mismatch"
            LOGGER.warning(
                "freshdesk.webhook.auth_failed: ip=%s header_present=%s "
                "reason=%s mode=%s",
                client_ip, bool(token), reason, app_settings.freshdesk_webhook_mode,
            )
            raise HTTPException(
                status_code=401,
                detail={"error": "invalid_webhook_token", "reason": reason},
            )

    # ── Parse JSON ────────────────────────────────────────────────────────────
    try:
        raw = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail={"error": "invalid_json"})

    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail={"error": "payload_must_be_object"})
    
    # ── STAGE 2 DIAGNOSTIC: log complete raw payload ──────────────────────────
    # Remove once payload shape is confirmed and parser is validated.
    LOGGER.info(
        "freshdesk_webhook: STAGE2_RAW_PAYLOAD headers=%s body=%s",
        json.dumps(dict(request.headers), indent=2),
        json.dumps(raw, indent=2),
    )
    # ─────────────────────────────────────────────────────────────────────────

    # ── Extract ticket info ───────────────────────────────────────────────────
    ticket = extract_ticket_info(raw)
    ticket_id = ticket.get("ticket_id", "")

    if not ticket_id:
        LOGGER.warning(
            "freshdesk.webhook: step=parse status=missing_ticket_id "
            "ip=%s payload_keys=%s",
            client_ip, list(raw.keys()),
        )
        return {"status": "skipped", "reason": "missing_ticket_id"}

    LOGGER.info(
        "freshdesk.webhook: step=parse status=ok ticket_id=%s "
        "subject=%r cf_clients=%r tags=%s ip=%s",
        ticket_id,
        ticket.get("subject", "")[:60],
        ticket.get("custom_fields", {}).get("cf_clients", ""),
        ticket.get("tags"),
        client_ip,
    )

    # ── Resolve tenant ────────────────────────────────────────────────────────
    client = resolve_tenant(
        ticket,
        tag_prefix=app_settings.freshdesk_webhook_tenant_tag_prefix,
        default_client=app_settings.freshdesk_webhook_default_client,
    )

    if not client:
        LOGGER.warning(
            "freshdesk.webhook: step=tenant_resolution status=failed "
            "ticket_id=%s cf_clients=%r email_domain=%s tags=%s ip=%s",
            ticket_id,
            ticket.get("custom_fields", {}).get("cf_clients", ""),
            ticket.get("requester_email", "").split("@")[-1]
            if "@" in ticket.get("requester_email", "") else "none",
            ticket.get("tags"),
            client_ip,
        )
        return {"status": "skipped", "reason": "no_tenant", "ticket_id": ticket_id}

    LOGGER.info(
        "freshdesk.webhook: step=tenant_resolution status=ok ticket_id=%s tenant=%s",
        ticket_id, client,
    )

    # ── Build query text ──────────────────────────────────────────────────────
    query_text = build_query_text(ticket)
    if not query_text.strip():
        LOGGER.warning(
            "freshdesk.webhook: step=build_query status=empty_query ticket_id=%s tenant=%s",
            ticket_id, client,
        )
        return {"status": "skipped", "reason": "empty_query", "ticket_id": ticket_id}

    LOGGER.info(
        "freshdesk.webhook: step=build_query status=ok ticket_id=%s "
        "tenant=%s query_chars=%d",
        ticket_id, client, len(query_text),
    )

    # ── Sprint 1.1: Aadhaar masking at ingress (RBI V-CIP requirement) ───────
    query_text = mask_aadhaar(query_text)

    # ── Sprint 1.1: Initialise FreshdeskReplyClient early for TCP note posting ─
    _reply_client = None
    if app_settings.freshdesk_domain and app_settings.freshdesk_api_key:
        _reply_client = FreshdeskReplyClient(
            domain=app_settings.freshdesk_domain,
            api_key=app_settings.freshdesk_api_key,
        )

    # ── Sprint 1: Open/classify case (Phase 1 safety: errors are isolated) ───
    _case = None
    _tcp_posted = False
    try:
        _cs = _get_case_service()
        _case = await asyncio.to_thread(_cs.open_case, ticket_id, client)
        await asyncio.to_thread(_cs.classify_case, _case, query_text)
        LOGGER.info(
            "freshdesk.webhook: step=case_created status=ok "
            "ticket_id=%s case_id=%s case_state=%s topic=%s tenant=%s",
            ticket_id, _case.case_id, _case.current_state.value, _case.topic, client,
        )
    except Exception as _ce_exc:
        LOGGER.warning(
            "case_engine.open_failed ticket=%s error=%s — Phase 1 continues",
            ticket_id, _ce_exc,
        )

    # ── Sprint 1.1: Post TCP note if classification escalated the case ────────
    if _case is not None and _case.current_state == CaseState.ESCALATED and not _tcp_posted:
        try:
            await _post_escalation_tcp_note(_case, _get_case_service(), ticket_id, _reply_client)
            _tcp_posted = True
        except Exception as _tcp_exc:
            LOGGER.warning(
                "case_engine.tcp_outer_failed ticket=%s error=%s", ticket_id, _tcp_exc,
            )

    # ── Run RAG pipeline ──────────────────────────────────────────────────────
    LOGGER.info(
        "freshdesk.webhook: step=knowledge_retrieval status=started "
        "ticket_id=%s tenant=%s",
        ticket_id, client,
    )
    rag_settings = get_rag_settings()
    openai_api_key = os.getenv("OPENAI_API_KEY", app_settings.chat_api_key).strip()

    try:
        generator = _get_generator(app_settings, rag_settings, openai_api_key)
        gen_request = GenerationRequest(
            query_text=query_text,
            client=client,
            session_id=f"fd-{ticket_id}",
            top_k=8,
            similarity_threshold=0.27,
            persist_history=False,
            history_turns=0,
            index_version=app_settings.active_index_version,
        )
        result = await asyncio.to_thread(generator.generate, gen_request)
        LOGGER.info(
            "freshdesk.webhook: step=response_generated status=ok "
            "ticket_id=%s tenant=%s confidence=%s chunks=%d requires_human=%s",
            ticket_id, client, result.confidence, len(result.chunks),
            result.requires_human,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"status": "bad_request", "error": str(exc), "ticket_id": ticket_id},
        ) from exc
    except RuntimeError as exc:
        LOGGER.exception(
            "freshdesk.webhook: step=knowledge_retrieval status=failed "
            "ticket_id=%s tenant=%s",
            ticket_id, client,
        )
        raise HTTPException(
            status_code=502,
            detail={"status": "rag_error", "error": str(exc), "ticket_id": ticket_id},
        ) from exc

    # ── Sprint 1: Evaluate RAG result ─────────────────────────────────────────
    if _case is not None:
        try:
            _cs = _get_case_service()
            await asyncio.to_thread(
                _cs.evaluate_rag_result,
                _case,
                match_type=result.diagnostics.get("match_type", "unknown"),
                confidence=str(result.confidence),
                requires_human=result.requires_human,
                chunks_count=len(result.chunks),
                ticket_text=query_text,
                cited_sop_ids=result.citations or [],
            )
        except Exception as _ce_exc:
            LOGGER.warning(
                "case_engine.evaluate_failed ticket=%s error=%s — Phase 1 continues",
                ticket_id, _ce_exc,
            )

    # ── Sprint 2.12: Query router shadow mode — classify without affecting routing ─
    try:
        _router_result = _QUERY_ROUTER.classify(query_text)
        LOGGER.info(
            "query_router_shadow: ticket=%s route=%s confidence=%.3f signals=%s",
            ticket_id, _router_result.route, _router_result.confidence,
            _router_result.matched_signals[:3],
        )
    except Exception as _qr_exc:
        LOGGER.debug(
            "query_router_shadow: classify_failed ticket=%s error=%s", ticket_id, _qr_exc,
        )

    # ── Sprint 1.1: Post TCP note if RAG evaluation escalated the case ───────
    if _case is not None and _case.current_state == CaseState.ESCALATED and not _tcp_posted:
        try:
            await _post_escalation_tcp_note(_case, _get_case_service(), ticket_id, _reply_client)
            _tcp_posted = True
        except Exception as _tcp_exc:
            LOGGER.warning(
                "case_engine.tcp_outer_failed ticket=%s error=%s", ticket_id, _tcp_exc,
            )

    # ── Confidence gate ───────────────────────────────────────────────────────
    if result.requires_human:
        LOGGER.info(
            "freshdesk_webhook: requires_human ticket=%s client=%s confidence=%s",
            ticket_id, client, result.confidence,
        )
        return {
            "status": "skipped",
            "reason": "requires_human_review",
            "ticket_id": ticket_id,
            "confidence": result.confidence,
            "confidence_score": result.confidence_score,
        }

    if not meets_confidence_threshold(result.confidence, app_settings.freshdesk_webhook_min_confidence):
        LOGGER.info(
            "freshdesk_webhook: below_threshold confidence=%s min=%s ticket=%s client=%s",
            result.confidence, app_settings.freshdesk_webhook_min_confidence, ticket_id, client,
        )
        return {
            "status": "skipped",
            "reason": "below_confidence_threshold",
            "ticket_id": ticket_id,
            "confidence": result.confidence,
        }

    # ── Post to Freshdesk ─────────────────────────────────────────────────────
    if not app_settings.freshdesk_domain or not app_settings.freshdesk_api_key:
        LOGGER.error("freshdesk_webhook: credentials_not_configured ticket=%s", ticket_id)
        raise HTTPException(
            status_code=500,
            detail={"status": "config_error", "error": "Freshdesk credentials not configured"},
        )

    # Format note body (needed by both gateway and direct paths)
    body_html = format_note_html(
        result.answer,
        confidence=result.confidence,
        citations=result.citations,
        ticket_id=ticket_id,
        client=client,
    )

    LOGGER.info(
        "freshdesk.webhook: step=freshdesk_update status=started "
        "ticket_id=%s tenant=%s",
        ticket_id, client,
    )
    if _ACTION_GATEWAY_ENABLED:
        # Sprint 2.12: Gateway path — propose SAFE action; worker executes asynchronously
        _gateway_case = _case if _case is not None else build_synthetic_case(ticket_id, client)
        _stack = getattr(request.app.state, "stack", None)
        _action_id = await asyncio.to_thread(
            propose_rag_note_action,
            case=_gateway_case,
            body_html=body_html,
            stack=_stack,
            ticket_id=ticket_id,
            client=client,
        )
        action = "gateway_note_proposed"
        LOGGER.info(
            "freshdesk.webhook: step=freshdesk_update status=ok "
            "ticket_id=%s tenant=%s path=gateway action_id=%s",
            ticket_id, client, _action_id,
        )
    else:
        # Direct path — synchronous post via FreshdeskReplyClient
        reply_client = _reply_client or FreshdeskReplyClient(
            domain=app_settings.freshdesk_domain,
            api_key=app_settings.freshdesk_api_key,
        )
        try:
            if app_settings.freshdesk_webhook_reply_as_note:
                await asyncio.to_thread(reply_client.post_note, ticket_id, body_html, private=True)
                action = "private_note_posted"
            else:
                body_html = format_reply_html(result.answer)
                await asyncio.to_thread(reply_client.post_reply, ticket_id, body_html)
                action = "public_reply_posted"
            LOGGER.info(
                "freshdesk.webhook: step=freshdesk_update status=ok "
                "ticket_id=%s tenant=%s path=direct action=%s",
                ticket_id, client, action,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception(
                "freshdesk.webhook: step=freshdesk_update status=failed "
                "ticket_id=%s tenant=%s",
                ticket_id, client,
            )
            raise HTTPException(
                status_code=502,
                detail={"status": "freshdesk_api_error", "error": str(exc), "ticket_id": ticket_id},
            ) from exc

    LOGGER.info(
        "freshdesk.webhook: pipeline_complete ticket_id=%s tenant=%s "
        "action=%s confidence=%s chunks=%d",
        ticket_id, client, action, result.confidence, len(result.chunks),
    )

    # ── Sprint 1: Audit note posting and resolve case ──────────────────────────
    if _case is not None:
        try:
            _cs = _get_case_service()
            await asyncio.to_thread(
                _cs.record_note_posted, _case,
                note_type=action, confidence=str(result.confidence),
            )
            await asyncio.to_thread(_cs.resolve_case, _case, reason="freshdesk_note_posted")
            LOGGER.info(
                "case_engine.resolved ticket=%s case=%s state=%s",
                ticket_id, _case.case_id, _case.current_state.value,
            )
        except Exception as _ce_exc:
            LOGGER.warning(
                "case_engine.resolve_failed ticket=%s error=%s",
                ticket_id, _ce_exc,
            )

    return {
        "status": "ok",
        "action": action,
        "ticket_id": ticket_id,
        "client": client,
        "confidence": result.confidence,
        "session_id": result.session_id,
        "message_id": result.message_id,
    }


# ── Feedback ──────────────────────────────────────────────────────────────────

@_rag_router.post("/feedback", response_model=FeedbackResponse)
async def post_feedback(request: FeedbackRequest) -> FeedbackResponse:
    """
    Record human agent feedback on an AI draft.

    Actions:
      APPROVED  — accepted as-is; may be ingested as VERIFIED_REPLY
      EDITED    — modified draft; edited version may be ingested
      REJECTED  — rejected entirely; NOT ingested
      ESCALATED — requires human escalation; excluded from future auto-retrieval
    """
    app_settings = get_settings()
    rag_settings = get_rag_settings()
    supabase = _get_supabase_client(app_settings)  # SPRINT0_FIX_SINGLETON
    openai_key = app_settings.chat_api_key or os.getenv("OPENAI_API_KEY", "")
    embedder = _get_embedder_wrapper(app_settings, rag_settings, openai_key)  # SPRINT0_FIX_SINGLETON
    ingester = FeedbackIngester(
        supabase,
        feedback_logs_table=rag_settings.feedback_logs_table,
    )

    from rag_engine.ingestion.knowledge_pipeline import KnowledgePipeline  # noqa: PLC0415
    knowledge_pipeline = KnowledgePipeline(rag_settings, supabase, embedder)
    review_queue = ReviewQueueManager(
        supabase,
        review_queue_table=rag_settings.review_queue_table,
        knowledge_pipeline=knowledge_pipeline,
    )

    return await handle_feedback(
        request,
        feedback_ingester=ingester,
        review_queue=review_queue,
    )


# ── Production module-level instance ─────────────────────────────────────────
# uvicorn serves this: ``uvicorn app.main:app --host 0.0.0.0 --port 8000``
app = create_app()
