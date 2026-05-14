from __future__ import annotations

import argparse
import hashlib
import json
import logging
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Any, Iterable, Literal


from app.chunker import Chunk, SourceDocument, chunk_document
from app.config import Settings, get_settings
from app.embeddings import EmbeddingClient
from app.git_sync import sync_repo
from app.parser_excel import parse_excel_rows
from app.parser_freshdesk import FreshdeskTicketFilters, parse_freshdesk_tickets
from app.parser_json import parse_stackoverflow_json_files
from app.parser_md import parse_markdown_files
from app.vector_store import StoreResult, VectorStore

LOGGER = logging.getLogger(__name__)


def _local_ingest_path_skips_git_sync(file_path: str | None) -> bool:
    """
    When file_path is an existing absolute file or directory (e.g. UI upload path),
    we can run ingest without `git pull` on the Fumadocs mirror.
    This avoids ECONNRESET / 500 when the remote doc repo is unavailable or out of sync.
    """
    if not (file_path and str(file_path).strip()):
        return False
    try:
        p = Path(str(file_path).strip()).expanduser()
        if not p.is_absolute():
            return False
        if not p.exists():
            return False
        return p.is_file() or p.is_dir()
    except OSError:
        return False


@dataclass(frozen=True)
class IngestResult:
    status: Literal["success", "partial_success", "failed"]
    files_scanned: int
    chunks_total: int
    chunks_created: int
    chunks_updated: int
    chunks_skipped: int
    failed_batches: int
    duration_ms: int
    repo_commit_sha: str
    errors: list[str]
    quarantined_documents: int = 0
    duplicate_documents: int = 0
    accepted_documents: int = 0
    quarantine_report_path: str | None = None
    excel: dict[str, Any] | None = None


@dataclass(frozen=True)
class IngestRequestOptions:
    repo_url: str | None = None
    ref: str | None = None
    full_reindex: bool = False
    file_path: str | None = None
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
    excel_sheet: str | None = None


def _resolve_ingest_options(options: IngestRequestOptions | None, legacy_kwargs: dict[str, Any]) -> IngestRequestOptions:
    if options is not None:
        return options
    return IngestRequestOptions(
        repo_url=legacy_kwargs.get("repo_url"),
        ref=legacy_kwargs.get("ref"),
        full_reindex=bool(legacy_kwargs.get("full_reindex", False)),
        file_path=legacy_kwargs.get("file_path"),
        freshdesk_updated_since=legacy_kwargs.get("freshdesk_updated_since"),
        freshdesk_updated_until=legacy_kwargs.get("freshdesk_updated_until"),
        freshdesk_ticket_ids=legacy_kwargs.get("freshdesk_ticket_ids"),
        freshdesk_apply_filters_with_ticket_ids=bool(
            legacy_kwargs.get("freshdesk_apply_filters_with_ticket_ids", False)
        ),
        freshdesk_ticket_types=legacy_kwargs.get("freshdesk_ticket_types"),
        freshdesk_requester_ids=legacy_kwargs.get("freshdesk_requester_ids"),
        freshdesk_responder_ids=legacy_kwargs.get("freshdesk_responder_ids"),
        freshdesk_group_ids=legacy_kwargs.get("freshdesk_group_ids"),
        freshdesk_statuses=legacy_kwargs.get("freshdesk_statuses"),
        freshdesk_priorities=legacy_kwargs.get("freshdesk_priorities"),
        excel_sheet=legacy_kwargs.get("excel_sheet"),
    )


def _determine_status(failed_batches: int, max_batch_errors: int, errors: list[str]) -> Literal["success", "partial_success", "failed"]:
    if failed_batches > max_batch_errors:
        return "failed"
    if errors:
        return "partial_success"
    return "success"


def _is_transient_upsert_error(exc: Exception) -> bool:
    text = str(exc).lower()
    transient_markers = (
        " 522",
        "error code 522",
        "connection timed out",
        "timeout",
        "temporar",
        "rate limit",
        "connection reset",
        "econnreset",
        "cloudflare",
    )
    return any(marker in text for marker in transient_markers)


def _parse_freshdesk_documents(settings: Settings, request: IngestRequestOptions, errors: list[str]) -> list[Any]:
    is_freshdesk_requested = _freshdesk_requested(
        freshdesk_updated_since=request.freshdesk_updated_since,
        freshdesk_updated_until=request.freshdesk_updated_until,
        freshdesk_ticket_ids=request.freshdesk_ticket_ids,
        freshdesk_ticket_types=request.freshdesk_ticket_types,
        freshdesk_requester_ids=request.freshdesk_requester_ids,
        freshdesk_responder_ids=request.freshdesk_responder_ids,
        freshdesk_group_ids=request.freshdesk_group_ids,
        freshdesk_statuses=request.freshdesk_statuses,
        freshdesk_priorities=request.freshdesk_priorities,
    )
    if not settings.freshdesk_enabled:
        LOGGER.info("ingest_parse freshdesk_skipped reason=disabled")
        return []
    if not is_freshdesk_requested:
        LOGGER.info("ingest_parse freshdesk_skipped reason=not_requested")
        return []

    LOGGER.info("ingest_parse mode=freshdesk updated_since=%s", request.freshdesk_updated_since or settings.freshdesk_updated_since or "")
    freshdesk_filters = FreshdeskTicketFilters(
        updated_until=request.freshdesk_updated_until,
        ticket_types=request.freshdesk_ticket_types,
        requester_ids=request.freshdesk_requester_ids,
        responder_ids=request.freshdesk_responder_ids,
        group_ids=request.freshdesk_group_ids,
        statuses=request.freshdesk_statuses,
        priorities=request.freshdesk_priorities,
    )
    try:
        freshdesk_docs = parse_freshdesk_tickets(
            domain=settings.freshdesk_domain,
            api_key=settings.freshdesk_api_key,
            updated_since=request.freshdesk_updated_since or settings.freshdesk_updated_since,
            ticket_ids=request.freshdesk_ticket_ids,
            filters=freshdesk_filters,
            apply_filters_with_ticket_ids=request.freshdesk_apply_filters_with_ticket_ids,
            page_size=settings.freshdesk_page_size,
            max_pages=settings.freshdesk_max_pages,
            max_retries=settings.freshdesk_max_retries,
            retry_base_delay_s=settings.freshdesk_retry_base_delay_s,
            min_wait_on_429_s=settings.freshdesk_min_wait_on_429_s,
            request_spacing_s=settings.freshdesk_request_spacing_s,
        )
        LOGGER.info("ingest_parse freshdesk_done documents=%s", len(freshdesk_docs))
        return freshdesk_docs
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Freshdesk parse failed: {exc}")
        LOGGER.exception("Freshdesk parse failed")
        return []


def _requested_file_types(file_path: str | None) -> tuple[bool, bool, bool]:
    """Return (want_md, want_json, want_excel) based on file extension."""
    if not file_path:
        return True, True, False
    file_ext = Path(file_path).suffix.lower()
    if file_ext in {".xlsx", ".xls", ".csv"}:
        return False, False, True
    if file_ext == ".json":
        return False, True, False
    if file_ext == ".zip":
        return False, True, False
    if file_ext in {".md", ".mdx", ".markdown"}:
        return True, False, False
    return True, True, False


def _freshdesk_requested(
    *,
    freshdesk_updated_since: str | None,
    freshdesk_updated_until: str | None,
    freshdesk_ticket_ids: list[int] | None,
    freshdesk_ticket_types: list[str] | None,
    freshdesk_requester_ids: list[int] | None,
    freshdesk_responder_ids: list[int] | None,
    freshdesk_group_ids: list[int] | None,
    freshdesk_statuses: list[str] | None,
    freshdesk_priorities: list[str] | None,
) -> bool:
    """Freshdesk should run only when explicitly requested in request body."""
    ids_requested = bool(freshdesk_ticket_ids)
    return ids_requested or any(
        v is not None
        for v in [
            freshdesk_updated_since,
            freshdesk_updated_until,
            freshdesk_ticket_types,
            freshdesk_requester_ids,
            freshdesk_responder_ids,
            freshdesk_group_ids,
            freshdesk_statuses,
            freshdesk_priorities,
        ]
    )


def _prepare_source_documents(
    *,
    repo_path: Path,
    json_root: Path,
    excel_root: Path,
    file_path: str | None,
    excel_sheet: str | None,
    ingest_all_excel_sheets: bool,
    docs_glob: str,
    freshdesk_only: bool = False,
) -> tuple[list[Any], list[Any], list[Any], dict[str, Any] | None, list[str]]:
    if freshdesk_only:
        LOGGER.info(
            "ingest_prepare_sources freshdesk_only=1 skipping local files (markdown/json/excel) repo_path=%s",
            repo_path,
        )
        return [], [], [], None, []
    want_md, want_json, want_excel = _requested_file_types(file_path)

    markdown_docs: list[Any] = []
    json_docs: list[Any] = []
    excel_docs: list[Any] = []
    excel_summary: dict[str, Any] | None = None
    parse_errors: list[str] = []

    LOGGER.info(
        "ingest_prepare_sources want_md=%s want_json=%s want_excel=%s repo_path=%s json_root=%s excel_root=%s "
        "file_path=%s docs_glob=%s excel_sheet=%s ingest_all_excel_sheets=%s",
        want_md,
        want_json,
        want_excel,
        repo_path,
        json_root,
        excel_root,
        file_path or "",
        docs_glob,
        excel_sheet or "",
        ingest_all_excel_sheets,
    )

    if want_md:
        try:
            LOGGER.info("ingest_parse mode=markdown file_path=%s docs_glob=%s", file_path or "", docs_glob)
            markdown_docs = parse_markdown_files(
                repo_path,
                only_file_path=file_path,
                docs_glob=docs_glob,
            )
            LOGGER.info("ingest_parse markdown_done documents=%s", len(markdown_docs))
        except Exception as exc:  # noqa: BLE001
            parse_errors.append(f"Markdown parse failed: {exc}")
            LOGGER.exception("Markdown parse failed for file_path=%s", file_path)
    else:
        LOGGER.info("ingest_parse markdown_skipped reason=file_path_targets_non_markdown")

    if want_json:
        try:
            LOGGER.info("ingest_parse mode=json file_path=%s json_root=%s", file_path or "", json_root)
            json_docs = parse_stackoverflow_json_files(json_root, only_file_path=file_path)
            LOGGER.info("ingest_parse json_done documents=%s", len(json_docs))
        except Exception as exc:  # noqa: BLE001
            parse_errors.append(f"JSON parse failed: {exc}")
            LOGGER.exception("JSON parse failed for file_path=%s", file_path)
    else:
        LOGGER.info("ingest_parse json_skipped reason=file_path_targets_non_json")

    if want_excel:
        try:
            LOGGER.info(
                "ingest_parse mode=tabular excel_root=%s file_path=%s sheet=%s",
                excel_root,
                file_path or "",
                excel_sheet or "",
            )
            excel_docs, excel_summary = parse_excel_rows(
                excel_root,
                file_path,
                excel_sheet,
                ingest_all_sheets=ingest_all_excel_sheets,
            )
            LOGGER.info("ingest_parse excel_done documents=%s", len(excel_docs))
            if excel_summary:
                LOGGER.info(
                    "ingest_parse_summary parser=tabular file=%s type=%s rows=%s skipped=%s warnings=%s sheet_count=%s",
                    excel_summary.get("file"),
                    excel_summary.get("detected_file_type"),
                    excel_summary.get("data_row_count"),
                    excel_summary.get("skipped_empty_rows"),
                    len(excel_summary.get("warnings") or []),
                    excel_summary.get("sheet_count"),
                )
        except Exception as exc:  # noqa: BLE001
            parse_errors.append(f"Excel parse failed: {exc}")
            LOGGER.exception("Excel parse failed for file_path=%s sheet=%s", file_path, excel_sheet)
    else:
        LOGGER.info("ingest_parse excel_skipped reason=no_xlsx_csv_path_or_not_requested")

    return markdown_docs, json_docs, excel_docs, excel_summary, parse_errors


def _batched(items: list[Chunk], n: int) -> Iterable[list[Chunk]]:
    iterator = iter(items)
    while True:
        batch = list(islice(iterator, n))
        if not batch:
            break
        yield batch


def _collect_files(root: Path, extensions: set[str]) -> list[Path]:
    files: list[Path] = []
    if not root.exists():
        return files
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in extensions:
            files.append(path)
    return files


def _normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def _is_sane_text(text: str) -> bool:
    if not text:
        return False
    printable = sum(1 for ch in text if ch.isprintable() or ch.isspace())
    ratio = printable / max(1, len(text))
    return ratio >= 0.9


def _required_metadata_ok(doc: SourceDocument) -> bool:
    if doc.source_type == "md":
        return bool((doc.metadata or {}).get("file_path"))
    if doc.source_type == "json":
        return bool((doc.metadata or {}).get("post_id") or doc.source_id)
    if doc.source_type == "excel":
        meta = doc.metadata or {}
        return bool(meta.get("excel_file") and meta.get("sheet"))
    if doc.source_type == "freshdesk":
        return bool(doc.source_id)
    return True


def _standardize_document_metadata(
    *,
    doc: SourceDocument,
    ingest_run_id: str,
    parser_version: str,
    tenant: str | None,
    access_scope: str | None,
) -> SourceDocument:
    metadata = dict(doc.metadata or {})
    metadata["doc_type"] = doc.source_type
    metadata["source_type"] = doc.source_type
    metadata["source_path_or_id"] = metadata.get("file_path") or doc.source_id
    metadata["updated_at"] = metadata.get("updated_at") or metadata.get("creation_date") or doc.creation_date
    metadata["ingest_run_id"] = ingest_run_id
    metadata["parser_version"] = parser_version
    metadata["content_language"] = metadata.get("content_language") or "unknown"
    metadata["tenant"] = metadata.get("tenant") or tenant
    metadata["access_scope"] = metadata.get("access_scope") or access_scope
    # Keep document-level context so retrieval has accurate semantic anchors
    # across markdown, json, excel, and freshdesk records.
    metadata["doc_title"] = doc.title
    metadata["doc_heading"] = doc.heading
    metadata["doc_tags"] = list(doc.tags or [])
    metadata["source_id"] = doc.source_id
    metadata["source_content_chars"] = len(doc.content or "")
    return SourceDocument(
        source_type=doc.source_type,
        source_id=doc.source_id,
        content=doc.content,
        title=doc.title,
        heading=doc.heading,
        tags=doc.tags,
        creation_date=doc.creation_date,
        metadata=metadata,
    )


def _validate_documents(
    *,
    documents: list[SourceDocument],
    ingest_run_id: str,
    parser_version: str,
    tenant: str | None,
    access_scope: str | None,
    min_document_chars: int,
) -> tuple[list[SourceDocument], list[dict[str, Any]], int]:
    accepted: list[SourceDocument] = []
    quarantined: list[dict[str, Any]] = []
    duplicates = 0
    seen_keys: set[str] = set()
    seen_hashes: set[str] = set()

    if documents:
        input_by_type = Counter(doc.source_type for doc in documents)
        LOGGER.info("ingest_validate input total=%s by_source_type=%s", len(documents), dict(input_by_type))
    else:
        LOGGER.info("ingest_validate input total=0")

    for raw_doc in documents:
        doc = _standardize_document_metadata(
            doc=raw_doc,
            ingest_run_id=ingest_run_id,
            parser_version=parser_version,
            tenant=tenant,
            access_scope=access_scope,
        )
        content = _normalize_whitespace(doc.content)
        if len(content) < min_document_chars:
            quarantined.append({"reason": "too_short", "source_type": doc.source_type, "source_id": doc.source_id})
            continue
        if not _is_sane_text(content):
            quarantined.append({"reason": "invalid_charset", "source_type": doc.source_type, "source_id": doc.source_id})
            continue
        if not _required_metadata_ok(doc):
            quarantined.append({"reason": "missing_required_metadata", "source_type": doc.source_type, "source_id": doc.source_id})
            continue
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        dedupe_key = f"{doc.source_type}:{doc.source_id}:{content_hash}"
        if dedupe_key in seen_keys:
            duplicates += 1
            continue
        seen_keys.add(dedupe_key)
        if content_hash in seen_hashes:
            duplicates += 1
            continue
        seen_hashes.add(content_hash)
        accepted.append(
            SourceDocument(
                source_type=doc.source_type,
                source_id=doc.source_id,
                content=content,
                title=doc.title,
                heading=doc.heading,
                tags=doc.tags,
                creation_date=doc.creation_date,
                metadata=doc.metadata,
            )
        )

    accepted_by_type = Counter(doc.source_type for doc in accepted)
    quarantine_by_type = Counter(str(item.get("source_type") or "?") for item in quarantined)
    LOGGER.info(
        "ingest_validate result accepted=%s accepted_by_source_type=%s quarantined=%s "
        "quarantine_by_source_type=%s duplicate_documents=%s",
        len(accepted),
        dict(accepted_by_type),
        len(quarantined),
        dict(quarantine_by_type),
        duplicates,
    )

    return accepted, quarantined, duplicates


def _write_quarantine_report(reports_path: str, ingest_run_id: str, quarantined: list[dict[str, Any]]) -> str | None:
    if not quarantined:
        return None
    report_dir = Path(reports_path).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / f"quarantine_{ingest_run_id}.json"
    report_file.write_text(json.dumps({"ingest_run_id": ingest_run_id, "quarantined": quarantined}, indent=2), encoding="utf-8")
    return str(report_file)


def run_ingest(
    settings: Settings,
    options: IngestRequestOptions | None = None,
    **legacy_kwargs: Any,
) -> IngestResult:
    request = _resolve_ingest_options(options, legacy_kwargs)
    start = time.time()
    ingest_run_id = str(uuid.uuid4())
    errors: list[str] = []
    repo = request.repo_url or settings.repo_url
    branch = request.ref or settings.repo_branch
    LOGGER.info("ingest_start run_id=%s repo=%s branch=%s file_path=%s", ingest_run_id, repo, branch, request.file_path or "")
    is_freshdesk_only = _freshdesk_requested(
        freshdesk_updated_since=request.freshdesk_updated_since,
        freshdesk_updated_until=request.freshdesk_updated_until,
        freshdesk_ticket_ids=request.freshdesk_ticket_ids,
        freshdesk_ticket_types=request.freshdesk_ticket_types,
        freshdesk_requester_ids=request.freshdesk_requester_ids,
        freshdesk_responder_ids=request.freshdesk_responder_ids,
        freshdesk_group_ids=request.freshdesk_group_ids,
        freshdesk_statuses=request.freshdesk_statuses,
        freshdesk_priorities=request.freshdesk_priorities,
    ) and not request.file_path

    if is_freshdesk_only:
        repo_path = Path(settings.repo_local_path).resolve()
        commit_sha = "freshdesk-only"
    elif _local_ingest_path_skips_git_sync(request.file_path):
        # Local file/folder (uploads, absolute paths) — do not require a successful git pull.
        LOGGER.info("ingest: skipping Fumadocs git sync; local file_path=%s", request.file_path)
        repo_path = Path(settings.repo_local_path).resolve()
        repo_path.mkdir(parents=True, exist_ok=True)
        commit_sha = "local-file"
    else:
        try:
            repo_path, commit_sha = sync_repo(
                repo,
                branch,
                settings.repo_local_path,
                command_timeout_s=settings.git_command_timeout_s,
            )
        except Exception as exc:  # noqa: BLE001
            # Never hard-fail ingestion just because git is unavailable.
            # Continue with whatever is available locally (uploads/json/excel/local repo mirror).
            LOGGER.warning(
                "ingest git sync failed; continuing with local fallback. file_path=%s error=%s",
                request.file_path,
                exc,
            )
            errors.append(
                f"Git sync skipped (using local sources only): {exc}"
            )
            repo_path = Path(settings.repo_local_path).resolve()
            repo_path.mkdir(parents=True, exist_ok=True)
            commit_sha = "local-fallback"
    json_root = Path(settings.json_data_path).resolve()
    excel_root = Path(settings.excel_data_path).resolve()
    LOGGER.info(
        "ingest_roots run_id=%s repo_path=%s json_root=%s excel_root=%s docs_glob=%s",
        ingest_run_id,
        repo_path,
        json_root,
        excel_root,
        settings.docs_glob,
    )

    # Request routing:
    # - `file_path` controls whether we ingest markdown, JSON, or Excel (.xlsx).
    # - Freshdesk runs only when explicitly requested in body (or when full reindex is requested).
    markdown_docs, json_docs, excel_docs, excel_summary, parse_errors = _prepare_source_documents(
        repo_path=repo_path,
        json_root=json_root,
        excel_root=excel_root,
        file_path=request.file_path,
        excel_sheet=request.excel_sheet,
        ingest_all_excel_sheets=settings.excel_ingest_all_sheets,
        docs_glob=settings.docs_glob,
        freshdesk_only=is_freshdesk_only,
    )
    errors.extend(parse_errors)
    freshdesk_docs = _parse_freshdesk_documents(settings, request, errors)

    documents = [*markdown_docs, *json_docs, *freshdesk_docs, *excel_docs]
    combined_by_type = Counter(getattr(d, "source_type", "?") for d in documents)
    LOGGER.info(
        "ingest_documents_combined run_id=%s total=%s by_source_type=%s",
        ingest_run_id,
        len(documents),
        dict(combined_by_type),
    )
    normalized_docs, quarantined_docs, duplicate_docs = _validate_documents(
        documents=documents,
        ingest_run_id=ingest_run_id,
        parser_version=settings.parser_version,
        tenant=settings.metadata_tenant,
        access_scope=settings.metadata_access_scope,
        min_document_chars=settings.min_document_chars,
    )
    quarantine_report_path = _write_quarantine_report(settings.ingest_reports_path, ingest_run_id, quarantined_docs)

    LOGGER.info(
        "Source docs prepared: markdown=%s json=%s freshdesk=%s excel=%s total=%s accepted=%s quarantined=%s duplicates=%s",
        len(markdown_docs),
        len(json_docs),
        len(freshdesk_docs),
        len(excel_docs),
        len(documents),
        len(normalized_docs),
        len(quarantined_docs),
        duplicate_docs,
    )
    all_chunks: list[Chunk] = []
    for doc in normalized_docs:
        all_chunks.extend(
            chunk_document(
                doc,
                min_words=settings.min_chunk_words,
                target_words=settings.target_chunk_words,
                max_words=settings.max_chunk_words,
                overlap_words=settings.chunk_overlap_words,
                strategy=settings.chunk_strategy,  # type: ignore[arg-type]
                max_chars=settings.chunk_max_chars,
                min_orphan_words=settings.chunk_min_orphan_words,
            )
        )

    embeddings = EmbeddingClient(
        provider=settings.embedding_provider,
        api_key=settings.embedding_api_key,
        model=settings.embedding_model,
        base_url=settings.embedding_base_url,
        timeout_s=settings.embedding_timeout_s,
        max_retries=settings.embedding_max_retries,
        retry_base_delay_s=settings.embedding_retry_base_delay_s,
    )
    store = VectorStore(
        supabase_url=settings.supabase_url,
        supabase_key=settings.supabase_key,
        table_name=settings.supabase_table,
        local_fallback_max_rows=settings.local_match_fallback_max_rows,
    )
    # Safety: keep full reindex upsert-only and avoid destructive deletes.
    if request.full_reindex:
        LOGGER.info("full_reindex requested: running upsert-only refresh (no deletions).")
    totals = StoreResult(created=0, updated=0, skipped=0)

    failed_batches = 0
    for batch in _batched(all_chunks, settings.embedding_batch_size):
        try:
            vectors = embeddings.embed_texts([item.content for item in batch])
            chunk_vectors = list(zip(batch, vectors))
            attempts = 5
            result: StoreResult | None = None
            last_error: Exception | None = None
            for attempt in range(1, attempts + 1):
                try:
                    result = store.upsert_chunks(
                        repo=repo,
                        commit_sha=commit_sha,
                        index_version=settings.write_index_version,
                        chunk_vectors=chunk_vectors,
                    )
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    if attempt >= attempts or not _is_transient_upsert_error(exc):
                        raise
                    sleep_s = min(90.0, 12.0 * (2 ** (attempt - 1)))
                    LOGGER.warning(
                        "Batch upsert transient failure (attempt %s/%s, batch_size=%s): %s. Retrying in %.1fs",
                        attempt,
                        attempts,
                        len(batch),
                        exc,
                        sleep_s,
                    )
                    time.sleep(sleep_s)
            if result is None:
                if last_error is not None:
                    raise last_error
                raise RuntimeError("Batch upsert failed without error details.")
            totals = StoreResult(
                created=totals.created + result.created,
                updated=totals.updated + result.updated,
                skipped=totals.skipped + result.skipped,
            )
        except Exception as exc:  # noqa: BLE001
            failed_batches += 1
            errors.append(f"Batch failed: {exc}")
            LOGGER.exception("Batch upsert failed batch_size=%s", len(batch))
    LOGGER.info(
        "upsert_totals run_id=%s created=%s updated=%s skipped=%s",
        ingest_run_id,
        totals.created,
        totals.updated,
        totals.skipped,
    )

    duration_ms = int((time.time() - start) * 1000)
    max_batch_errors = settings.ingest_max_batch_errors
    status = _determine_status(failed_batches, max_batch_errors, errors)
    LOGGER.info(
        "ingest_end run_id=%s status=%s duration_ms=%s failed_batches=%s errors=%s",
        ingest_run_id,
        status,
        duration_ms,
        failed_batches,
        len(errors),
    )
    return IngestResult(
        status=status,
        files_scanned=len(markdown_docs) + len(json_docs) + len(freshdesk_docs) + len(excel_docs),
        chunks_total=len(all_chunks),
        chunks_created=totals.created,
        chunks_updated=totals.updated,
        chunks_skipped=totals.skipped,
        failed_batches=failed_batches,
        duration_ms=duration_ms,
        repo_commit_sha=commit_sha,
        errors=errors,
        quarantined_documents=len(quarantined_docs),
        duplicate_documents=duplicate_docs,
        accepted_documents=len(normalized_docs),
        quarantine_report_path=quarantine_report_path,
        excel=excel_summary,
    )


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest Markdown and StackOverflow JSON into Supabase pgvector.")
    parser.add_argument("--repo-url", default=None, help="Override repository URL")
    parser.add_argument("--ref", default=None, help="Override branch or ref")
    parser.add_argument("--full-reindex", action="store_true", help="Upsert-only full refresh (no deletions)")
    parser.add_argument("--file-path", default=None, help="Ingest a single repo-relative .md/.mdx/.json file")
    return parser


def main() -> None:
    args = _build_cli_parser().parse_args()
    settings = get_settings()
    result = run_ingest(
        settings,
        repo_url=args.repo_url,
        ref=args.ref,
        full_reindex=args.full_reindex,
        file_path=args.file_path,
    )
    print(result)


if __name__ == "__main__":
    main()

