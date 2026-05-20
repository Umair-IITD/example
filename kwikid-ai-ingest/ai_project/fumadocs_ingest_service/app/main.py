from __future__ import annotations

import logging
import hashlib
import os
import uuid
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from app.security import (
    api_key_auth_middleware,
    initialize as security_initialize,
    load_api_keys,
    validate_startup_security,
)

from observability import logger, tracer, ChunkMetadata, LLMResponseMetadata

import json

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


_LOGGER_PRE = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup: validate security config and cache API keys. Shutdown: log."""
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

    # Warn if Freshdesk webhook is enabled without a secret — any caller can trigger it.
    webhook_enabled = os.getenv("FRESHDESK_WEBHOOK_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }
    webhook_secret = os.getenv("FRESHDESK_WEBHOOK_SECRET", "").strip()
    if webhook_enabled and not webhook_secret:
        _LOGGER_PRE.warning(
            "SECURITY_WARNING: FRESHDESK_WEBHOOK_ENABLED=true but FRESHDESK_WEBHOOK_SECRET "
            "is not set. The webhook endpoint accepts requests from any caller without "
            "authentication. Set FRESHDESK_WEBHOOK_SECRET to enable HMAC-SHA256 validation."
        )

    # Warn about dev-only CORS if non-localhost origins are absent
    cors_origins = [o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    if not cors_origins:
        _LOGGER_PRE.info("cors_origins: using default localhost-only origins (dev mode)")

    yield
    _LOGGER_PRE.info("service_shutdown")


_docs_enabled = os.getenv("FASTAPI_DOCS_ENABLED", "false").strip().lower() in {
    "1", "true", "yes", "on"
}

app = FastAPI(
    title="Fuma Docs Ingestion Service",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)
LOGGER = logging.getLogger(__name__)

# CORS origins are configurable via CORS_ALLOWED_ORIGINS (comma-separated).
# Default: localhost:3000 only — suitable for local dev.
# Production: set CORS_ALLOWED_ORIGINS to your actual frontend domain(s).
_cors_origins_raw = os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
_cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "X-Webhook-Token", "Authorization"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    try:
        response = await call_next(request)
        duration = time.time() - start_time
        logger.info(
            f"Handled {request.method} {request.url.path}",
            extra={
                "extra_data": {
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_s": round(duration, 4),
                    "client_ip": request.client.host if request.client else None
                }
            }
        )
        return response
    except Exception as e:
        duration = time.time() - start_time
        logger.error(
            f"Failed {request.method} {request.url.path}: {e}",
            extra={
                "extra_data": {
                    "method": request.method,
                    "path": request.url.path,
                    "duration_s": round(duration, 4),
                    "error": str(e)
                }
            }
        )
        raise


# Security middleware is registered AFTER log_requests so it is outermost
# (Starlette LIFO: last registered = first to process inbound requests).
app.middleware("http")(api_key_auth_middleware)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    LOGGER.exception(
        "unhandled_exception method=%s path=%s type=%s",
        request.method, request.url.path, type(exc).__name__,
    )
    return JSONResponse(
        status_code=500,
        content={"error": "internal_server_error", "message": "An unexpected error occurred."},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
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
    client: str = Field(min_length=1)        # REQUIRED: tenant slug (unity_bank, rbl_bank, …)
    session_id: str | None = None
    top_k: int = Field(default=8, ge=1, le=20)
    similarity_threshold: float = Field(default=0.27, ge=0.0, le=1.0)
    history_turns: int = Field(default=6, ge=0, le=20)
    persist_history: bool = True


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/freshdesk/filter-options")
def freshdesk_filter_options() -> dict[str, Any]:
    status_options = [{"value": label, "label": label.title()} for _, label in sorted(STATUS_MAP.items())]
    priority_options = [{"value": label, "label": label.title()} for _, label in sorted(PRIORITY_MAP.items())]
    return {
        "ticket_types": [],
        "statuses": status_options,
        "priorities": priority_options,
    }


@app.get(
    "/ready",
    responses={
        503: {
            "description": "Dependencies are not ready",
            "content": {"application/json": {"example": {"status": "not_ready", "checks": {}}}},
        }
    },
)
def ready() -> dict[str, Any]:
    settings = get_settings()
    checks: dict[str, Any] = {}
    ok = True

    try:
        store = VectorStore(
            supabase_url=settings.supabase_url,
            supabase_key=settings.supabase_key,
            table_name=settings.supabase_table,
            local_fallback_max_rows=settings.local_match_fallback_max_rows,
        )
        checks["supabase"] = {"ok": store.healthcheck()}
    except Exception as exc:  # noqa: BLE001
        checks["supabase"] = {"ok": False, "error": str(exc)}
        ok = False

    try:
        embeddings = EmbeddingClient(
            provider=settings.embedding_provider,
            api_key=settings.embedding_api_key,
            model=settings.embedding_model,
            base_url=settings.embedding_base_url,
            timeout_s=settings.embedding_timeout_s,
            max_retries=settings.embedding_max_retries,
            retry_base_delay_s=settings.embedding_retry_base_delay_s,
        )
        checks["embeddings"] = {"ok": embeddings.healthcheck()}
    except Exception as exc:  # noqa: BLE001
        checks["embeddings"] = {"ok": False, "error": str(exc)}
        ok = False

    if not checks.get("supabase", {}).get("ok", False) or not checks.get("embeddings", {}).get("ok", False):
        ok = False

    if not ok:
        raise HTTPException(status_code=503, detail={"status": "not_ready", "checks": checks})
    return {"status": "ready", "checks": checks}


@app.post(
    "/ingest",
    responses={
        500: {"description": "Ingestion failed"},
        207: {"description": "Ingestion completed with partial success"},
    },
)
def ingest_docs(payload: IngestRequest) -> dict[str, Any]:
    request_id = str(uuid.uuid4())
    LOGGER.info("ingest_request request_id=%s file_path=%s", request_id, payload.file_path or "")
    settings = get_settings()
    try:
        result = run_ingest(
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
        LOGGER.error("ingest_failed request_id=%s errors=%s", request_id, len(result.errors))
        raise HTTPException(status_code=500, detail=out)
    if result.status == "partial_success":
        LOGGER.warning("ingest_partial request_id=%s errors=%s", request_id, len(result.errors))
        raise HTTPException(status_code=207, detail=out)
    LOGGER.info("ingest_success request_id=%s files_scanned=%s", request_id, result.files_scanned)
    return out


@app.post("/query")
def query_docs(payload: QueryRequest) -> dict[str, Any]:
    trace = tracer.start_trace(
        query=payload.query_text,
        tenant=payload.tenant,
        access_scope=payload.access_scope
    )
    settings = get_settings()
    result = run_query(
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
        "query_trace query_hash=%s filters=%s returned=%s insufficient=%s best_similarity=%s best_rerank=%s",
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
    
    # Update and save trace
    trace.query_hash = query_hash
    trace.retrieval_candidates_count = len(result.matches)
    trace.top_matches = [
        ChunkMetadata(
            id=str(m.get("id") if isinstance(m, dict) else getattr(m, "id", "unknown")),
            source_type=str((m.get("metadata") or {}).get("source_type", "unknown") if isinstance(m, dict) else (getattr(m, "metadata", {}) or {}).get("source_type", "unknown")),
            similarity=float(m.get("similarity", 0.0) if isinstance(m, dict) else getattr(m, "similarity", 0.0)),
            rerank_score=(m.get("metadata") or {}).get("rerank_score") if isinstance(m, dict) else (getattr(m, "metadata", {}) or {}).get("rerank_score"),
            title=(m.get("metadata") or {}).get("title") if isinstance(m, dict) else (getattr(m, "metadata", {}) or {}).get("title")
        ) for m in result.matches
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


@app.post("/chat")
def chat_endpoint(payload: ChatRequest) -> dict[str, Any]:
    trace = tracer.start_trace(
        query=payload.query_text,
        session_id=payload.session_id,
        tenant=payload.tenant,
        access_scope=payload.access_scope
    )
    settings = get_settings()
    try:
        result = run_chat(
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
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"status": "bad_request", "error": str(exc)}) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail={"status": "upstream_error", "error": str(exc)}) from exc

    query_hash = hashlib.sha256(payload.query_text.encode("utf-8")).hexdigest()[:16]
    LOGGER.info(
        "chat_trace query_hash=%s session_id=%s confidence=%s insufficient=%s citations=%s",
        query_hash,
        result.session_id,
        result.confidence,
        result.insufficient_context,
        len(result.citations),
    )
    
    # Update and save trace
    trace.query_hash = query_hash
    trace.llm_response = result.answer
    trace.confidence_score = result.confidence if isinstance(result.confidence, (int, float)) else None
    trace.citations = result.citations
    trace.top_matches = [
        ChunkMetadata(
            id=str(m.get("id") if isinstance(m, dict) else getattr(m, "id", "unknown")),
            source_type=str((m.get("metadata") or {}).get("source_type", "unknown") if isinstance(m, dict) else (getattr(m, "metadata", {}) or {}).get("source_type", "unknown")),
            similarity=float(m.get("similarity", 0.0) if isinstance(m, dict) else getattr(m, "similarity", 0.0))
        ) for m in result.matches
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


@app.get("/chat/suggestions")
def chat_suggestions(
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
        suggestions = get_suggestions(
            settings,
            tenant=tenant,
            access_scope=access_scope,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("chat_suggestions failed")
        raise HTTPException(
            status_code=500,
            detail={"status": "error", "error": str(exc)},
        ) from exc
    return {"suggestions": suggestions}


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


@app.post("/train/chat")
def train_chat(payload: TrainChatRequest) -> dict[str, Any]:
    settings = get_settings()
    previous_draft = _draft_model_to_dataclass(payload.draft)
    try:
        result = run_train_chat(
            settings,
            query_text=payload.query_text,
            session_id=payload.session_id,
            previous_draft=previous_draft,
            tenant=payload.tenant,
            access_scope=payload.access_scope,
            history_turns=payload.history_turns,
            persist_history=payload.persist_history,
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
        result.session_id,
        result.confidence,
        result.diagnostics.get("word_count"),
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


@app.post("/train/commit")
def train_commit(payload: CommitCardRequest) -> dict[str, Any]:
    settings = get_settings()
    draft = _draft_model_to_dataclass(payload.draft)
    try:
        result = commit_knowledge_card(
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


@app.get("/train/cards")
def train_cards_list(
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
        cards, total = list_knowledge_cards(
            settings,
            limit=limit,
            offset=offset,
            tenant=tenant,
            access_scope=access_scope,
            status=status,
            session_id=session_id,
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


@app.get("/train/cards/{card_id}")
def train_cards_get(card_id: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        card = get_knowledge_card(settings, card_id)
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


@app.delete("/train/cards/{card_id}")
def train_cards_delete(card_id: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        result = delete_knowledge_card(settings, card_id)
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


def _build_chat_generator(
    app_settings: Any,
    rag_settings: Any,
    openai_api_key: str,
) -> tuple[Any, Any, Any]:
    """Return (embedder, generator, supabase) — caller must call embedder.close()."""
    supabase = create_client(app_settings.supabase_url, app_settings.supabase_key)

    embedder = OpenAIEmbeddingProvider(
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


@app.post("/rag/chat")
def rag_chat(payload: RagChatRequest) -> dict[str, Any]:
    """
    B1 RAG chat endpoint.

    Uses the Phase B1 vector tables (rag_ticket_chunks, rag_sop_chunks) for
    tenant-isolated retrieval, then generates a structured response via LLM.

    The `client` field is mandatory — it determines which tenant's data is searched.
    Requires B1 SQL migrations and data ingestion to have been run.
    """
    app_settings = get_settings()
    rag_settings = get_rag_settings()

    openai_api_key = os.getenv("OPENAI_API_KEY", app_settings.chat_api_key).strip()
    embedder = None

    try:
        embedder, generator, _supabase = _build_chat_generator(
            app_settings, rag_settings, openai_api_key
        )

        result = generator.generate(
            GenerationRequest(
                query_text=payload.query_text,
                client=payload.client,
                session_id=payload.session_id,
                top_k=payload.top_k,
                similarity_threshold=payload.similarity_threshold,
                persist_history=payload.persist_history,
                history_turns=payload.history_turns,
                index_version=rag_settings.index_version,
            )
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

    finally:
        if embedder is not None:
            try:
                embedder.close()
            except Exception:  # noqa: BLE001
                pass

    LOGGER.info(
        "rag_chat client=%s session=%s confidence=%s chunks=%d insufficient=%s",
        payload.client,
        result.session_id,
        result.confidence,
        len(result.chunks),
        result.insufficient_context,
    )

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
        "diagnostics": result.diagnostics,
    }


@app.post("/freshdesk/webhook")
async def freshdesk_webhook(request: Request) -> dict[str, Any]:
    """
    Phase B3: Freshdesk webhook receiver.

    When a new/updated ticket triggers a Freshdesk automation rule, this endpoint:
      1. Validates the optional X-Webhook-Token header
      2. Extracts ticket info from the payload
      3. Resolves the tenant (client slug) from tags / custom fields / default
      4. Runs the B1 RAG pipeline to generate an AI draft reply
      5. Posts the draft back to Freshdesk as a private note (default) or public reply

    Environment variables:
      FRESHDESK_WEBHOOK_SECRET           — HMAC-SHA256 secret for token validation (optional)
      FRESHDESK_WEBHOOK_DEFAULT_CLIENT   — fallback tenant slug when none can be resolved
      FRESHDESK_WEBHOOK_REPLY_AS_NOTE    — true (default) = private note, false = public reply
      FRESHDESK_WEBHOOK_MIN_CONFIDENCE   — low|medium|high (default: low)
      FRESHDESK_WEBHOOK_TENANT_TAG_PREFIX — tag prefix for tenant detection (default: "client:")

    The endpoint always returns 200 so Freshdesk does not retry on business-logic skips.
    5xx is returned only on unexpected upstream failures.
    """
    app_settings = get_settings()

    if not app_settings.freshdesk_webhook_enabled:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "webhook_disabled",
                "message": "Freshdesk webhook is disabled. Set FRESHDESK_WEBHOOK_ENABLED=true to enable.",
            },
        )

    # ── 1. Read raw body (needed before JSON parse for signature check) ─────────
    raw_body = await request.body()

    # ── 2. Optional token verification ─────────────────────────────────────────
    if app_settings.freshdesk_webhook_secret:
        token = request.headers.get("X-Webhook-Token", "")
        if not verify_webhook_token(
            raw_body,
            provided_token=token,
            expected_secret=app_settings.freshdesk_webhook_secret,
        ):
            raise HTTPException(status_code=401, detail={"error": "invalid_webhook_token"})

    # ── 3. Parse JSON ───────────────────────────────────────────────────────────
    try:
        raw = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail={"error": "invalid_json"})

    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail={"error": "payload_must_be_object"})

    # ── 4. Extract ticket info ──────────────────────────────────────────────────
    ticket = extract_ticket_info(raw)
    ticket_id = ticket.get("ticket_id", "")

    if not ticket_id:
        LOGGER.warning("freshdesk_webhook: missing ticket_id in payload keys=%s", list(raw.keys()))
        return {"status": "skipped", "reason": "missing_ticket_id"}

    # ── 5. Resolve tenant ───────────────────────────────────────────────────────
    client = resolve_tenant(
        ticket,
        tag_prefix=app_settings.freshdesk_webhook_tenant_tag_prefix,
        default_client=app_settings.freshdesk_webhook_default_client,
    )

    if not client:
        LOGGER.warning("freshdesk_webhook: no tenant resolved ticket=%s tags=%s", ticket_id, ticket.get("tags"))
        return {"status": "skipped", "reason": "no_tenant", "ticket_id": ticket_id}

    # ── 6. Build query text ─────────────────────────────────────────────────────
    query_text = build_query_text(ticket)
    if not query_text.strip():
        LOGGER.warning("freshdesk_webhook: empty query ticket=%s", ticket_id)
        return {"status": "skipped", "reason": "empty_query", "ticket_id": ticket_id}

    # ── 7. Run RAG pipeline ─────────────────────────────────────────────────────
    rag_settings = get_rag_settings()
    openai_api_key = os.getenv("OPENAI_API_KEY", app_settings.chat_api_key).strip()
    embedder = None

    try:
        embedder, generator, _supabase = _build_chat_generator(
            app_settings, rag_settings, openai_api_key
        )
        result = generator.generate(
            GenerationRequest(
                query_text=query_text,
                client=client,
                session_id=f"fd-{ticket_id}",
                top_k=8,
                similarity_threshold=0.27,
                persist_history=False,
                history_turns=0,
                index_version=rag_settings.index_version,
            )
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"status": "bad_request", "error": str(exc), "ticket_id": ticket_id},
        ) from exc
    except RuntimeError as exc:
        LOGGER.exception("freshdesk_webhook: RAG pipeline failed ticket=%s", ticket_id)
        raise HTTPException(
            status_code=502,
            detail={"status": "rag_error", "error": str(exc), "ticket_id": ticket_id},
        ) from exc
    finally:
        if embedder is not None:
            try:
                embedder.close()
            except Exception:  # noqa: BLE001
                pass

    # ── 8. Confidence gate ──────────────────────────────────────────────────────
    if result.requires_human:
        LOGGER.info(
            "freshdesk_webhook: requires_human=True — skipping auto-reply ticket=%s client=%s confidence=%s",
            ticket_id,
            client,
            result.confidence,
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
            "freshdesk_webhook: below threshold confidence=%s min=%s ticket=%s client=%s",
            result.confidence,
            app_settings.freshdesk_webhook_min_confidence,
            ticket_id,
            client,
        )
        return {
            "status": "skipped",
            "reason": "below_confidence_threshold",
            "ticket_id": ticket_id,
            "confidence": result.confidence,
        }

    # ── 9. Post to Freshdesk ────────────────────────────────────────────────────
    if not app_settings.freshdesk_domain or not app_settings.freshdesk_api_key:
        LOGGER.error("freshdesk_webhook: FRESHDESK_DOMAIN / FRESHDESK_API_KEY not configured")
        raise HTTPException(
            status_code=500,
            detail={"status": "config_error", "error": "Freshdesk credentials not configured"},
        )

    reply_client = FreshdeskReplyClient(
        domain=app_settings.freshdesk_domain,
        api_key=app_settings.freshdesk_api_key,
    )

    try:
        if app_settings.freshdesk_webhook_reply_as_note:
            body_html = format_note_html(
                result.answer,
                confidence=result.confidence,
                citations=result.citations,
                ticket_id=ticket_id,
                client=client,
            )
            reply_client.post_note(ticket_id, body_html, private=True)
            action = "private_note_posted"
        else:
            body_html = format_reply_html(result.answer)
            reply_client.post_reply(ticket_id, body_html)
            action = "public_reply_posted"
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("freshdesk_webhook: Freshdesk API call failed ticket=%s", ticket_id)
        raise HTTPException(
            status_code=502,
            detail={"status": "freshdesk_api_error", "error": str(exc), "ticket_id": ticket_id},
        ) from exc

    LOGGER.info(
        "freshdesk_webhook: action=%s ticket=%s client=%s confidence=%s chunks=%d",
        action,
        ticket_id,
        client,
        result.confidence,
        len(result.chunks),
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


# ─────────────────────────────────────────────────────────────────────────────
# Phase B3: Feedback endpoint
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/feedback", response_model=FeedbackResponse)
async def post_feedback(request: FeedbackRequest) -> FeedbackResponse:
    """
    Record human agent feedback on an AI draft.

    Actions:
      APPROVED  — agent accepted the draft as-is; may be ingested as VERIFIED_REPLY
      EDITED    — agent modified the draft; edited version may be ingested
      REJECTED  — agent rejected the draft entirely; NOT ingested
      ESCALATED — ticket requires human escalation; excluded from future auto-retrieval

    Auth: X-API-Key header required (same as all other protected endpoints).
    """
    app_settings = get_settings()   # validated — raises if SUPABASE_URL/KEY missing
    rag_settings = get_rag_settings()
    supabase     = create_client(app_settings.supabase_url, app_settings.supabase_key)
    openai_key   = app_settings.chat_api_key or os.getenv("OPENAI_API_KEY", "")
    openai_base  = app_settings.chat_base_url

    embedder = OpenAIEmbeddingProvider(api_key=openai_key, base_url=openai_base)
    try:
        ingester = FeedbackIngester(supabase, feedback_logs_table=rag_settings.feedback_logs_table)

        # Build ReviewQueueManager with optional KnowledgePipeline
        from rag_engine.ingestion.knowledge_pipeline import KnowledgePipeline
        knowledge_pipeline = KnowledgePipeline(rag_settings, supabase, embedder)
        review_queue = ReviewQueueManager(
            supabase,
            review_queue_table = rag_settings.review_queue_table,
            knowledge_pipeline = knowledge_pipeline,
        )

        return await handle_feedback(
            request,
            feedback_ingester = ingester,
            review_queue      = review_queue,
        )
    finally:
        try:
            embedder.close()
        except Exception:  # noqa: BLE001
            pass
