"""
rag_engine/config/rag_settings.py

Phase B1 configuration.
All values have safe defaults and are overridable via environment variables.

New in this revision:
  - Separate embedding timeouts: B1_EMBEDDING_CONNECT_TIMEOUT_S, B1_EMBEDDING_READ_TIMEOUT_S,
    B1_EMBEDDING_WRITE_TIMEOUT_S, B1_EMBEDDING_POOL_TIMEOUT_S
  - Circuit breaker: B1_EMBEDDING_CB_THRESHOLD, B1_EMBEDDING_CB_COOLDOWN_S
  - Increased default max_retries: 6 (was 5)
  - Increased default retry_max_delay: 60s (was 30s)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_str(name: str, default: str) -> str:
    val = os.getenv(name, default)
    return val.strip() if val else default


@dataclass(frozen=True)
class RagEngineSettings:
    # ── Database table names ───────────────────────────────────────────────────
    ticket_documents_table: str
    ticket_chunks_table:    str
    sop_library_table:      str
    sop_chunks_table:       str
    ingestion_logs_table:   str
    ingestion_errors_table: str
    feedback_logs_table:    str

    # ── Data paths ─────────────────────────────────────────────────────────────
    processed_data_dir: Path
    reports_dir:        Path
    index_version:      str

    # ── Chunking ───────────────────────────────────────────────────────────────
    ticket_min_chunk_words: int
    ticket_max_chunk_words: int
    ticket_overlap_words:   int
    min_query_body_chars:   int

    # ── Token-aware chunking ───────────────────────────────────────────────────
    embedding_model:          str    # model name for tiktoken (matches OpenAI model)
    embedding_max_input_tokens: int  # hard cap — no chunk may exceed this before embedding
    chunk_target_tokens:       int   # target tokens per chunk (tiktoken-based splitting)
    chunk_overlap_tokens:      int   # token overlap between adjacent chunks

    # ── Embedding: retry ───────────────────────────────────────────────────────
    embedding_batch_size:           int
    embedding_max_retries:          int
    embedding_retry_base_delay_s:   float
    embedding_retry_max_delay_s:    float
    embedding_dimensions:           int

    # ── Embedding: timeouts (all separate) ────────────────────────────────────
    embedding_connect_timeout_s: float   # DNS + TCP handshake
    embedding_read_timeout_s:    float   # waiting for first response byte
    embedding_write_timeout_s:   float   # sending request body
    embedding_pool_timeout_s:    float   # waiting for a connection from the pool

    # ── Embedding: circuit breaker ────────────────────────────────────────────
    embedding_circuit_breaker_threshold:  int    # consecutive batch failures before cooldown
    embedding_circuit_breaker_cooldown_s: float  # seconds to pause during cooldown

    # ── Retrieval ──────────────────────────────────────────────────────────────
    default_top_k:                  int
    default_similarity_threshold:   float
    sop_similarity_boost:           float
    exclude_escalation_from_auto:   bool

    # ── Ingestion limits ───────────────────────────────────────────────────────
    max_batch_errors: int
    upsert_batch_size: int

    # ── Observability ──────────────────────────────────────────────────────────
    log_level:               str
    log_to_file:             bool
    metrics_flush_interval_s: int


def get_rag_settings() -> RagEngineSettings:
    _service_root    = Path(__file__).parent.parent.parent
    _default_data    = _service_root / "data" / "processed"
    _default_reports = _service_root / "data" / "reports"

    return RagEngineSettings(
        # Table names
        ticket_documents_table=_env_str("B1_TICKET_DOCUMENTS_TABLE", "rag_ticket_documents"),
        ticket_chunks_table   =_env_str("B1_TICKET_CHUNKS_TABLE",    "rag_ticket_chunks"),
        sop_library_table     =_env_str("B1_SOP_LIBRARY_TABLE",      "rag_sop_library"),
        sop_chunks_table      =_env_str("B1_SOP_CHUNKS_TABLE",       "rag_sop_chunks"),
        ingestion_logs_table  =_env_str("B1_INGESTION_LOGS_TABLE",   "rag_ingestion_logs"),
        ingestion_errors_table=_env_str("B1_INGESTION_ERRORS_TABLE", "rag_ingestion_errors"),
        feedback_logs_table   =_env_str("B1_FEEDBACK_LOGS_TABLE",    "rag_feedback_logs"),

        # Data paths
        processed_data_dir=Path(os.getenv("B1_PROCESSED_DATA_DIR", str(_default_data))),
        reports_dir       =Path(os.getenv("B1_REPORTS_DIR",        str(_default_reports))),
        index_version     =_env_str("B1_INDEX_VERSION", "v1"),

        # Chunking (word-based — legacy, used as fallback when tiktoken unavailable)
        ticket_min_chunk_words=_env_int("B1_TICKET_MIN_CHUNK_WORDS", 30),
        ticket_max_chunk_words=_env_int("B1_TICKET_MAX_CHUNK_WORDS", 400),
        ticket_overlap_words  =_env_int("B1_TICKET_OVERLAP_WORDS",   20),
        min_query_body_chars  =_env_int("B1_MIN_QUERY_BODY_CHARS",   80),

        # Token-aware chunking
        embedding_model           =_env_str("EMBEDDING_MODEL",              "text-embedding-3-small"),
        embedding_max_input_tokens=_env_int("EMBEDDING_MAX_INPUT_TOKENS",   7000),
        chunk_target_tokens       =_env_int("CHUNK_TARGET_TOKENS",          1200),
        chunk_overlap_tokens      =_env_int("CHUNK_OVERLAP_TOKENS",         150),

        # Embedding: retry
        embedding_batch_size        =_env_int  ("B1_EMBEDDING_BATCH_SIZE",         64),
        embedding_max_retries       =_env_int  ("B1_EMBEDDING_MAX_RETRIES",         6),
        embedding_retry_base_delay_s=_env_float("B1_EMBEDDING_RETRY_BASE_DELAY_S", 1.0),
        embedding_retry_max_delay_s =_env_float("B1_EMBEDDING_RETRY_MAX_DELAY_S",  60.0),
        embedding_dimensions        =_env_int  ("EMBEDDING_DIMENSIONS",            1536),

        # Embedding: timeouts
        embedding_connect_timeout_s=_env_float("B1_EMBEDDING_CONNECT_TIMEOUT_S", 10.0),
        embedding_read_timeout_s   =_env_float("B1_EMBEDDING_READ_TIMEOUT_S",    90.0),
        embedding_write_timeout_s  =_env_float("B1_EMBEDDING_WRITE_TIMEOUT_S",   30.0),
        embedding_pool_timeout_s   =_env_float("B1_EMBEDDING_POOL_TIMEOUT_S",    10.0),

        # Embedding: circuit breaker
        embedding_circuit_breaker_threshold =_env_int  ("B1_EMBEDDING_CB_THRESHOLD",  3),
        embedding_circuit_breaker_cooldown_s=_env_float("B1_EMBEDDING_CB_COOLDOWN_S", 60.0),

        # Retrieval
        default_top_k                =_env_int  ("B1_DEFAULT_TOP_K",                 10),
        default_similarity_threshold =_env_float("B1_DEFAULT_SIMILARITY_THRESHOLD",  0.27),
        sop_similarity_boost         =_env_float("B1_SOP_SIMILARITY_BOOST",          0.15),
        exclude_escalation_from_auto =_env_bool ("B1_EXCLUDE_ESCALATION_FROM_AUTO",  True),

        # Ingestion limits
        max_batch_errors =_env_int("B1_MAX_BATCH_ERRORS",  10),
        upsert_batch_size=_env_int("B1_UPSERT_BATCH_SIZE", 50),

        # Observability
        log_level               =_env_str ("B1_LOG_LEVEL",               os.getenv("LOG_LEVEL", "INFO")),
        log_to_file             =_env_bool("B1_LOG_TO_FILE",             True),
        metrics_flush_interval_s=_env_int ("B1_METRICS_FLUSH_INTERVAL_S", 60),
    )
