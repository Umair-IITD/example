from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value for {name}: {raw}")


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    log_level: str
    repo_url: str
    repo_branch: str
    repo_local_path: str
    docs_glob: str
    json_data_path: str
    excel_data_path: str
    excel_ingest_all_sheets: bool
    embedding_provider: str
    embedding_api_key: str
    embedding_model: str
    embedding_base_url: str
    embedding_dimensions: int
    embedding_batch_size: int
    min_chunk_words: int
    target_chunk_words: int
    max_chunk_words: int
    chunk_overlap_words: int
    chunk_strategy: str
    chunk_max_chars: int
    chunk_min_orphan_words: int
    min_document_chars: int
    ingest_reports_path: str
    parser_version: str
    metadata_tenant: str | None
    metadata_access_scope: str | None
    ingest_max_batch_errors: int
    local_match_fallback_max_rows: int
    git_command_timeout_s: int
    embedding_timeout_s: int
    embedding_max_retries: int
    embedding_retry_base_delay_s: float
    supabase_url: str
    supabase_key: str
    supabase_table: str
    write_index_version: str
    active_index_version: str
    rerank_enabled: bool
    strict_latest_within_top_n: bool
    rerank_candidate_multiplier: int
    confidence_min_similarity: float
    confidence_min_rerank: float
    query_source_thresholds: dict[str, float]
    hybrid_retrieval_enabled: bool
    freshdesk_enabled: bool
    freshdesk_domain: str
    freshdesk_api_key: str
    freshdesk_updated_since: str | None
    freshdesk_page_size: int
    freshdesk_max_pages: int
    freshdesk_max_retries: int
    freshdesk_retry_base_delay_s: float
    freshdesk_min_wait_on_429_s: float
    freshdesk_request_spacing_s: float
    chat_api_key: str
    chat_model: str
    chat_base_url: str
    chat_timeout_s: int
    chat_max_retries: int
    chat_retry_base_delay_s: float
    chat_temperature: float
    chat_max_output_tokens: int
    chat_context_chunk_max_chars: int
    chat_history_turns: int
    chat_history_table: str
    # ── Phase B3: Freshdesk webhook (inbound) ─────────────────────────────────
    freshdesk_webhook_enabled: bool       # master switch — false until B1 is stable
    freshdesk_webhook_secret: str | None
    freshdesk_webhook_default_client: str | None
    freshdesk_webhook_reply_as_note: bool
    freshdesk_webhook_min_confidence: str
    freshdesk_webhook_tenant_tag_prefix: str
    # When true: startup FAILS if webhook is enabled but no HMAC secret is set.
    # When false (default): startup emits a warning and allows unauthenticated webhooks.
    freshdesk_webhook_enforce_hmac: bool

    # ── Phase B2: Redis rate limiting ─────────────────────────────────────────
    redis_rate_limit_enabled: bool     # disabled by default — in-process fallback used
    redis_url: str                     # redis://localhost:6379
    rag_chat_rate_limit: int           # max requests per 60s for /rag/chat

    # ── Phase B2: Prometheus metrics ─────────────────────────────────────────
    prometheus_enabled: bool           # disabled by default

    # ── Phase B1/B3: Hybrid retrieval ────────────────────────────────────────
    b1_hybrid_retrieval_enabled: bool  # enables HybridTicketRetriever (FTS + semantic)


def _validate_embedding_settings(settings: Settings) -> None:
    if settings.embedding_provider not in {"ollama", "openai"}:
        raise ValueError("EMBEDDING_PROVIDER must be either 'ollama' or 'openai'")
    if settings.embedding_provider == "ollama" and "api.openai.com" in settings.embedding_base_url:
        raise ValueError("EMBEDDING_PROVIDER=ollama cannot use OpenAI base URL. Set EMBEDDING_BASE_URL=http://localhost:11434")
    if settings.embedding_provider == "openai" and not settings.embedding_api_key:
        raise ValueError("EMBEDDING_API_KEY is required when EMBEDDING_PROVIDER=openai")
    if settings.embedding_timeout_s <= 0:
        raise ValueError("EMBEDDING_TIMEOUT_S must be > 0")
    if settings.embedding_max_retries < 0:
        raise ValueError("EMBEDDING_MAX_RETRIES must be >= 0")
    if settings.embedding_retry_base_delay_s <= 0:
        raise ValueError("EMBEDDING_RETRY_BASE_DELAY_S must be > 0")


def _validate_runtime_settings(settings: Settings) -> None:
    if settings.ingest_max_batch_errors < 0:
        raise ValueError("INGEST_MAX_BATCH_ERRORS must be >= 0")
    if settings.local_match_fallback_max_rows < 1:
        raise ValueError("LOCAL_MATCH_FALLBACK_MAX_ROWS must be >= 1")
    if settings.git_command_timeout_s <= 0:
        raise ValueError("GIT_COMMAND_TIMEOUT_S must be > 0")
    if settings.chunk_strategy not in {"auto", "paragraph", "heading_aware", "record_aware"}:
        raise ValueError("CHUNK_STRATEGY must be one of auto|paragraph|heading_aware|record_aware")
    if settings.chunk_max_chars < 500:
        raise ValueError("CHUNK_MAX_CHARS must be >= 500")
    if settings.chunk_min_orphan_words < 0:
        raise ValueError("CHUNK_MIN_ORPHAN_WORDS must be >= 0")
    if settings.min_document_chars < 1:
        raise ValueError("MIN_DOCUMENT_CHARS must be >= 1")
    if settings.rerank_candidate_multiplier < 1:
        raise ValueError("RERANK_CANDIDATE_MULTIPLIER must be >= 1")
    for source_type, threshold in settings.query_source_thresholds.items():
        if threshold < 0.0 or threshold > 1.0:
            raise ValueError(f"QUERY_SOURCE_THRESHOLD_{source_type.upper()} must be between 0.0 and 1.0")


def _validate_chat_settings(settings: Settings) -> None:
    if settings.chat_timeout_s <= 0:
        raise ValueError("CHAT_TIMEOUT_S must be > 0")
    if settings.chat_max_retries < 0:
        raise ValueError("CHAT_MAX_RETRIES must be >= 0")
    if settings.chat_retry_base_delay_s <= 0:
        raise ValueError("CHAT_RETRY_BASE_DELAY_S must be > 0")
    if settings.chat_temperature < 0.0 or settings.chat_temperature > 2.0:
        raise ValueError("CHAT_TEMPERATURE must be between 0.0 and 2.0")
    if settings.chat_max_output_tokens < 16:
        raise ValueError("CHAT_MAX_OUTPUT_TOKENS must be >= 16")
    if settings.chat_context_chunk_max_chars < 200:
        raise ValueError("CHAT_CONTEXT_CHUNK_MAX_CHARS must be >= 200")
    if settings.chat_history_turns < 0:
        raise ValueError("CHAT_HISTORY_TURNS must be >= 0")
    if not settings.chat_history_table.strip():
        raise ValueError("CHAT_HISTORY_TABLE must be non-empty")


def _validate_freshdesk_settings(settings: Settings) -> None:
    if settings.freshdesk_enabled:
        if not settings.freshdesk_domain:
            raise ValueError("FRESHDESK_DOMAIN is required when FRESHDESK_ENABLED=true")
        if not settings.freshdesk_api_key:
            raise ValueError("FRESHDESK_API_KEY is required when FRESHDESK_ENABLED=true")
    if settings.freshdesk_page_size < 1 or settings.freshdesk_page_size > 100:
        raise ValueError("FRESHDESK_PAGE_SIZE must be between 1 and 100")
    if settings.freshdesk_max_pages < 1:
        raise ValueError("FRESHDESK_MAX_PAGES must be >= 1")
    if settings.freshdesk_max_retries < 0:
        raise ValueError("FRESHDESK_MAX_RETRIES must be >= 0")
    if settings.freshdesk_retry_base_delay_s <= 0:
        raise ValueError("FRESHDESK_RETRY_BASE_DELAY_S must be > 0")
    if settings.freshdesk_min_wait_on_429_s < 0:
        raise ValueError("FRESHDESK_MIN_WAIT_ON_429_S must be >= 0")
    if settings.freshdesk_request_spacing_s < 0:
        raise ValueError("FRESHDESK_REQUEST_SPACING_S must be >= 0")
    if settings.freshdesk_webhook_min_confidence not in {"low", "medium", "high"}:
        raise ValueError("FRESHDESK_WEBHOOK_MIN_CONFIDENCE must be low|medium|high")
    if not settings.freshdesk_webhook_tenant_tag_prefix:
        raise ValueError("FRESHDESK_WEBHOOK_TENANT_TAG_PREFIX must be non-empty")


def get_settings() -> Settings:
    provider = os.getenv("EMBEDDING_PROVIDER", "openai").strip().lower()
    if not provider:
        raise ValueError(
            "EMBEDDING_PROVIDER is required. Set it to 'openai' or 'ollama'. "
            "There is no safe default — the provider must match the model used to index your documents."
        )
    model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    if provider == "openai":
        base_url = os.getenv("EMBEDDING_BASE_URL", os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
        api_key = os.getenv("EMBEDDING_API_KEY", os.getenv("OPENAI_API_KEY", ""))
    else:
        base_url = os.getenv("EMBEDDING_BASE_URL", os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
        api_key = os.getenv("EMBEDDING_API_KEY", "")

    settings = Settings(
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        log_level=os.getenv("LOG_LEVEL", "info"),
        repo_url=os.getenv("FUMADOCS_REPO_URL", "git@bitbucket.org:team360noscope/kwikid-docs-internal.git"),
        repo_branch=os.getenv("FUMADOCS_REPO_BRANCH", "main"),
        repo_local_path=os.getenv("FUMADOCS_LOCAL_PATH", "./data/fumadocs_repo"),
        docs_glob=os.getenv("DOCS_GLOB", "**/*.{md,mdx,json}"),
        json_data_path=os.getenv("JSON_DATA_PATH", "./data/kwikid"),
        excel_data_path=os.getenv("EXCEL_DATA_PATH", "./data/excel"),
        excel_ingest_all_sheets=_env_bool("EXCEL_INGEST_ALL_SHEETS", default=False),
        embedding_provider=provider,
        embedding_api_key=api_key,
        embedding_model=model,
        embedding_base_url=base_url,
        embedding_dimensions=int(os.getenv("EMBEDDING_DIMENSIONS", "1536")),
        embedding_batch_size=int(os.getenv("EMBEDDING_BATCH_SIZE", "64")),
        min_chunk_words=int(os.getenv("MIN_CHUNK_WORDS", "200")),
        target_chunk_words=int(os.getenv("TARGET_CHUNK_WORDS", "350")),
        max_chunk_words=int(os.getenv("MAX_CHUNK_WORDS", "500")),
        chunk_overlap_words=int(os.getenv("CHUNK_OVERLAP_WORDS", "50")),
        chunk_strategy=os.getenv("CHUNK_STRATEGY", "auto").strip().lower(),
        chunk_max_chars=int(os.getenv("CHUNK_MAX_CHARS", "6000")),
        chunk_min_orphan_words=int(os.getenv("CHUNK_MIN_ORPHAN_WORDS", "50")),
        min_document_chars=int(os.getenv("MIN_DOCUMENT_CHARS", "60")),
        ingest_reports_path=os.getenv("INGEST_REPORTS_PATH", "./data/reports"),
        parser_version=os.getenv("PARSER_VERSION", "v1"),
        metadata_tenant=(os.getenv("METADATA_TENANT") or "").strip() or None,
        metadata_access_scope=(os.getenv("METADATA_ACCESS_SCOPE") or "").strip() or None,
        # Allow a few bad batches before failing the run; set 0 to fail on first batch error.
        ingest_max_batch_errors=int(os.getenv("INGEST_MAX_BATCH_ERRORS", "3")),
        local_match_fallback_max_rows=int(os.getenv("LOCAL_MATCH_FALLBACK_MAX_ROWS", "5000")),
        git_command_timeout_s=int(os.getenv("GIT_COMMAND_TIMEOUT_S", "120")),
        embedding_timeout_s=int(os.getenv("EMBEDDING_TIMEOUT_S", "60")),
        embedding_max_retries=int(os.getenv("EMBEDDING_MAX_RETRIES", "3")),
        embedding_retry_base_delay_s=float(os.getenv("EMBEDDING_RETRY_BASE_DELAY_S", "0.8")),
        supabase_url=_require_env("SUPABASE_URL"),
        supabase_key=_require_env("SUPABASE_KEY"),
        supabase_table=os.getenv("TABLE_NAME", "documents"),
        write_index_version=os.getenv("WRITE_INDEX_VERSION", "v1"),
        active_index_version=os.getenv("ACTIVE_INDEX_VERSION", "v1"),
        rerank_enabled=_env_bool("RERANK_ENABLED", default=True),
        strict_latest_within_top_n=_env_bool("STRICT_LATEST_WITHIN_TOP_N", default=False),
        rerank_candidate_multiplier=int(os.getenv("RERANK_CANDIDATE_MULTIPLIER", "4")),
        confidence_min_similarity=float(os.getenv("CONFIDENCE_MIN_SIMILARITY", "0.2")),
        confidence_min_rerank=float(os.getenv("CONFIDENCE_MIN_RERANK", "0.05")),
        hybrid_retrieval_enabled=_env_bool("HYBRID_RETRIEVAL_ENABLED", default=False),
        query_source_thresholds={
            "freshdesk": float(os.getenv("QUERY_SOURCE_THRESHOLD_FRESHDESK", "0.20")),
            "md": float(os.getenv("QUERY_SOURCE_THRESHOLD_MD", "0.20")),
            "json": float(os.getenv("QUERY_SOURCE_THRESHOLD_JSON", "0.20")),
            "excel": float(os.getenv("QUERY_SOURCE_THRESHOLD_EXCEL", "0.20")),
            "manual": float(os.getenv("QUERY_SOURCE_THRESHOLD_MANUAL", "0.20")),
        },
        freshdesk_enabled=_env_bool("FRESHDESK_ENABLED", default=False),
        freshdesk_domain=os.getenv("FRESHDESK_DOMAIN", "").strip(),
        freshdesk_api_key=os.getenv("FRESHDESK_API_KEY", "").strip(),
        freshdesk_updated_since=(os.getenv("FRESHDESK_UPDATED_SINCE") or "").strip() or None,
        freshdesk_page_size=int(os.getenv("FRESHDESK_PAGE_SIZE", "100")),
        freshdesk_max_pages=int(os.getenv("FRESHDESK_MAX_PAGES", "100")),
        freshdesk_max_retries=int(os.getenv("FRESHDESK_MAX_RETRIES", "6")),
        freshdesk_retry_base_delay_s=float(os.getenv("FRESHDESK_RETRY_BASE_DELAY_S", "1.0")),
        freshdesk_min_wait_on_429_s=float(os.getenv("FRESHDESK_MIN_WAIT_ON_429_S", "25.0")),
        freshdesk_request_spacing_s=float(os.getenv("FRESHDESK_REQUEST_SPACING_S", "3.5")),
        chat_api_key=os.getenv("OPENAI_CHAT_API_KEY", os.getenv("OPENAI_API_KEY", "")).strip(),
        chat_model=os.getenv("OPENAI_CHAT_MODEL", "gpt-4.1-mini").strip(),
        chat_base_url=os.getenv("OPENAI_CHAT_BASE_URL", os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).strip(),
        chat_timeout_s=int(os.getenv("CHAT_TIMEOUT_S", "60")),
        chat_max_retries=int(os.getenv("CHAT_MAX_RETRIES", "3")),
        chat_retry_base_delay_s=float(os.getenv("CHAT_RETRY_BASE_DELAY_S", "0.8")),
        chat_temperature=float(os.getenv("CHAT_TEMPERATURE", "0.2")),
        chat_max_output_tokens=int(os.getenv("CHAT_MAX_OUTPUT_TOKENS", "800")),
        chat_context_chunk_max_chars=int(os.getenv("CHAT_CONTEXT_CHUNK_MAX_CHARS", "3500")),
        chat_history_turns=int(os.getenv("CHAT_HISTORY_TURNS", "6")),
        chat_history_table=os.getenv("CHAT_HISTORY_TABLE", "chat_messages"),
        freshdesk_webhook_enabled=_env_bool("FRESHDESK_WEBHOOK_ENABLED", default=False),
        freshdesk_webhook_secret=(os.getenv("FRESHDESK_WEBHOOK_SECRET") or "").strip() or None,
        freshdesk_webhook_default_client=(os.getenv("FRESHDESK_WEBHOOK_DEFAULT_CLIENT") or "").strip() or None,
        freshdesk_webhook_reply_as_note=_env_bool("FRESHDESK_WEBHOOK_REPLY_AS_NOTE", default=True),
        freshdesk_webhook_min_confidence=os.getenv("FRESHDESK_WEBHOOK_MIN_CONFIDENCE", "low").strip().lower(),
        freshdesk_webhook_tenant_tag_prefix=os.getenv("FRESHDESK_WEBHOOK_TENANT_TAG_PREFIX", "client:").strip(),
        freshdesk_webhook_enforce_hmac=_env_bool("FRESHDESK_WEBHOOK_ENFORCE_HMAC", default=False),
        # Redis
        redis_rate_limit_enabled=_env_bool("REDIS_RATE_LIMIT_ENABLED", default=False),
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379").strip(),
        rag_chat_rate_limit=int(os.getenv("RAG_CHAT_RATE_LIMIT", "20")),
        # Prometheus
        prometheus_enabled=_env_bool("PROMETHEUS_ENABLED", default=False),
        # Hybrid retrieval
        b1_hybrid_retrieval_enabled=_env_bool("B1_HYBRID_RETRIEVAL_ENABLED", default=False),
    )
    _validate_embedding_settings(settings)
    _validate_freshdesk_settings(settings)
    _validate_runtime_settings(settings)
    _validate_chat_settings(settings)
    return settings

