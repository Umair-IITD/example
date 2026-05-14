from __future__ import annotations

import json
import logging
import re
import shutil
import time
import zipfile
from html import unescape
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from app.chunker import SourceDocument


HTML_TAG_RE = re.compile(r"<[^>]+>")
PRE_CODE_RE = re.compile(r"<pre><code>.*?</code></pre>", flags=re.IGNORECASE | re.DOTALL)
MARKDOWN_CODE_RE = re.compile(r"```.*?```", flags=re.DOTALL)
DEPENDENCY_FILES = {"posts.json", "comments.json", "users.json", "posts2votes.json"}
STACK_TEAMS_BASE_URL = "https://stackoverflowteams.com/c/kwikid"
JSON_GLOB = "*.json"
GENERIC_JSON_MAX_VALUE_CHARS = 6000
GENERIC_JSON_MAX_TITLE_CHARS = 240
GENERIC_JSON_PRIORITY_KEYS = [
    "subject",
    "title",
    "summary",
    "status",
    "priority",
    "type",
    "issue_area",
    "resolution",
    "impact",
    "query_type",
    "environment",
    "created_at",
    "updated_at",
    "resolved_at",
    "closed_at",
    "ticket_url",
    "post_link",
    "stack_link",
    "stackoverflow_link",
    "tags",
]
LOW_VALUE_BLOB_RE = re.compile(r"(eyJ[a-zA-Z0-9_-]{20,}|indattachment\.freshdesk\.com/inline/attachment)", re.IGNORECASE)
LOGGER = logging.getLogger(__name__)


def _get_any(record: dict[str, Any], *names: str) -> Any:
    lowered = {str(k).lower(): v for k, v in record.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def _parse_tags(tags: Any) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, list):
        return [str(tag).strip() for tag in tags if str(tag).strip()]
    raw = str(tags).strip()
    if not raw:
        return []
    if "<" in raw and ">" in raw:
        return [match.strip() for match in re.findall(r"<([^>]+)>", raw) if match.strip()]
    return [tag.strip() for tag in raw.split(",") if tag.strip()]


def _clean_html(value: Any, *, remove_code_blocks: bool = True) -> str:
    text = str(value or "")
    if remove_code_blocks:
        text = PRE_CODE_RE.sub(" ", text)
        text = MARKDOWN_CODE_RE.sub(" ", text)
    text = HTML_TAG_RE.sub(" ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _load_json_records(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("posts", "questions", "data", "items", "tickets", "records", "rows"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                return [item for item in candidate if isinstance(item, dict)]
        return [payload]
    return []


def _normalize_post_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "question"}:
        return "question"
    if normalized in {"2", "answer"}:
        return "answer"
    return normalized


def _sort_answers(answers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def sort_key(answer: dict[str, Any]) -> tuple[int, str]:
        score = _to_int(_get_any(answer, "score")) or 0
        created = str(_get_any(answer, "creationDate", "creation_date") or "")
        return (-score, created)

    return sorted(answers, key=sort_key)


def _select_primary_answer(question: dict[str, Any], candidate_answers: list[dict[str, Any]]) -> dict[str, Any] | None:
    accepted_answer_id = _get_any(question, "acceptedAnswerId", "accepted_answer_id")
    accepted_answer = _select_accepted_answer(accepted_answer_id, candidate_answers)
    if accepted_answer is not None:
        return accepted_answer
    ranked = _sort_answers(candidate_answers)
    return ranked[0] if ranked else None


def _build_user_lookup(records: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    user_by_id: dict[int, dict[str, Any]] = {}
    for row in records:
        uid = _to_int(_get_any(row, "id", "userId", "user_id"))
        if uid is not None:
            user_by_id[uid] = row
    return user_by_id


def _group_by_post_id(records: list[dict[str, Any]], key_names: tuple[str, ...]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in records:
        post_id = _to_int(_get_any(row, *key_names))
        if post_id is not None:
            grouped.setdefault(post_id, []).append(row)
    return grouped


def _format_comment_text(comments: list[dict[str, Any]]) -> str:
    cleaned = [
        _clean_html(_get_any(comment, "text", "comment", "comment_text"), remove_code_blocks=False)
        for comment in comments
    ]
    return " ".join(text for text in cleaned if text).strip()


def _format_vote_summary(votes: list[dict[str, Any]]) -> str:
    if not votes:
        return ""
    by_vote_type: dict[str, int] = {}
    for vote in votes:
        vote_type = str(_get_any(vote, "voteTypeId", "vote_type_id") or "unknown")
        by_vote_type[vote_type] = by_vote_type.get(vote_type, 0) + 1
    parts = [f"type {vote_type}: {count}" for vote_type, count in sorted(by_vote_type.items())]
    return ", ".join(parts)


def _extract_post_link(question: dict[str, Any], post_id: int) -> str:
    raw_link = _get_any(
        question,
        "postLink",
        "post_link",
        "link",
        "url",
        "questionUrl",
        "question_url",
        "permalink",
    )
    link = str(raw_link or "").strip()
    if link:
        return link
    return f"{STACK_TEAMS_BASE_URL}/questions/{post_id}"


def _build_content(
    question_title: str,
    post_link: str,
    question_body: str,
    primary_answer_body: str,
    related_answer_bodies: list[str],
    question_comments: str,
    answer_comments: str,
    vote_summary: str,
) -> str:
    parts = [f"Question Title: {question_title}", f"Post Link: {post_link}", f"Question Body: {question_body}"]
    if primary_answer_body:
        parts.append(f"Primary Answer: {primary_answer_body}")
    for idx, body in enumerate(related_answer_bodies, start=1):
        parts.append(f"Related Answer {idx}: {body}")
    if question_comments:
        parts.append(f"Question Comments: {question_comments}")
    if answer_comments:
        parts.append(f"Answer Comments: {answer_comments}")
    if vote_summary:
        parts.append(f"Votes Summary: {vote_summary}")
    return "\n\n".join(part for part in parts if part.strip()).strip()


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_iso_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _latest_post_activity_iso(question: dict[str, Any], answers: list[dict[str, Any]], comments: list[dict[str, Any]]) -> str | None:
    candidates: list[datetime] = []
    for row in [question, *answers, *comments]:
        dt = _parse_iso_dt(_get_any(row, "lastActivityDate", "last_activity_date", "lastEditDate", "last_edit_date", "creationDate", "creation_date"))
        if dt is not None:
            candidates.append(dt)
    if not candidates:
        return None
    return max(candidates).astimezone(timezone.utc).isoformat()


def _is_question_row(row: dict[str, Any], post_type_id: Any) -> bool:
    title = _get_any(row, "title", "question_title")
    return title is not None or str(post_type_id) == "1"


def _is_answer_row(parent_id: Any, post_type_id: Any) -> bool:
    return parent_id is not None or str(post_type_id) == "2"


def _classify_row(
    row: dict[str, Any],
    questions: dict[int, dict[str, Any]],
    answers_by_parent: dict[int, list[dict[str, Any]]],
    comments_by_post: dict[int, list[dict[str, Any]]],
) -> None:
    post_id = _to_int(_get_any(row, "id", "post_id", "postId"))
    if post_id is None:
        return

    post_type_id = _get_any(row, "postTypeId", "post_type_id")
    parent_id = _get_any(row, "parentId", "parent_id")
    body = _get_any(row, "body", "question_body", "answer_body")
    text = _get_any(row, "text", "comment", "comment_text")

    if _is_question_row(row, post_type_id):
        questions[post_id] = row
        return

    if _is_answer_row(parent_id, post_type_id):
        parent_post_id = _to_int(parent_id)
        answers_by_parent.setdefault(parent_post_id if parent_post_id is not None else -1, []).append(row)
        return

    comment_parent = _to_int(_get_any(row, "postId", "post_id", "parentId", "parent_id"))
    if text is not None and comment_parent is not None and body is None:
        comments_by_post.setdefault(comment_parent, []).append(row)


def _extract_entities(records: list[dict[str, Any]]) -> tuple[dict[int, dict[str, Any]], dict[int, list[dict[str, Any]]], dict[int, list[dict[str, Any]]]]:
    questions: dict[int, dict[str, Any]] = {}
    answers_by_parent: dict[int, list[dict[str, Any]]] = {}
    comments_by_post: dict[int, list[dict[str, Any]]] = {}

    for row in records:
        _classify_row(row, questions, answers_by_parent, comments_by_post)

    return questions, answers_by_parent, comments_by_post


def _select_accepted_answer(
    accepted_answer_id: Any,
    candidate_answers: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if accepted_answer_id is None:
        return candidate_answers[0] if candidate_answers else None
    wanted = str(accepted_answer_id)
    for answer in candidate_answers:
        answer_id = _get_any(answer, "id", "post_id", "postId")
        if str(answer_id) == wanted:
            return answer
    return candidate_answers[0] if candidate_answers else None


def _combine_content(question_title: str, post_link: str, question_body: str, answer_body: str, comments_text: str) -> str:
    parts = [f"Question Title: {question_title}", f"Post Link: {post_link}", f"Question Body: {question_body}"]
    if answer_body:
        parts.append(f"Accepted Answer: {answer_body}")
    if comments_text:
        parts.append(f"Comments: {comments_text}")
    return "\n\n".join(part for part in parts if part.strip()).strip()


def _to_source_document(
    qid: int,
    question: dict[str, Any],
    candidate_answers: list[dict[str, Any]],
    comment_rows: list[dict[str, Any]],
    json_root_path: Path,
    remove_code_blocks: bool,
) -> SourceDocument | None:
    accepted_answer_id = _get_any(question, "acceptedAnswerId", "accepted_answer_id")
    accepted_answer = _select_accepted_answer(accepted_answer_id, candidate_answers)
    question_title = str(_get_any(question, "title", "question_title") or f"Question {qid}")
    post_link = _extract_post_link(question, qid)
    question_body = _clean_html(_get_any(question, "body", "question_body"), remove_code_blocks=remove_code_blocks)
    answer_body = _clean_html(
        _get_any(accepted_answer or {}, "body", "answer_body"),
        remove_code_blocks=remove_code_blocks,
    )
    comments_text = " ".join(
        _clean_html(_get_any(comment, "text", "comment", "comment_text"), remove_code_blocks=False)
        for comment in comment_rows
    ).strip()
    content = _combine_content(question_title, post_link, question_body, answer_body, comments_text)
    if not content:
        return None

    tags = _parse_tags(_get_any(question, "tags"))
    created = _get_any(question, "creationDate", "creation_date")
    metadata = {
        "post_id": qid,
        "post_link": post_link,
        "file_path": f"{json_root_path.name}/{str(_get_any(question, '__source_file') or '')}",
        "tags": tags,
        "creation_date": str(created) if created is not None else None,
        "accepted_answer_id": _get_any(accepted_answer or {}, "id", "post_id", "postId"),
        "answer_count": len(candidate_answers),
        "comment_count": len(comment_rows),
    }
    return SourceDocument(
        source_type="json",
        source_id=str(qid),
        content=content,
        title=question_title,
        heading=question_title,
        tags=tags,
        creation_date=str(created) if created is not None else None,
        metadata=metadata,
    )


def _normalize_only_file_path(only_file_path: str | None) -> str | None:
    if not only_file_path:
        return None
    only = only_file_path.strip().replace("\\", "/")
    if only.startswith("./"):
        only = only[2:]
    if only.startswith("/"):
        only = only[1:]
    return only


def _collect_absolute_json_files(only_file_path: str | None) -> list[Path]:
    if not only_file_path:
        return []
    candidate = Path(only_file_path).expanduser()
    if not candidate.is_absolute():
        return []
    resolved_candidate = candidate.resolve()
    if not resolved_candidate.exists():
        return []
    if resolved_candidate.is_file() and resolved_candidate.suffix.lower() == ".zip":
        return _extract_zip_json_files(resolved_candidate)
    if resolved_candidate.is_dir():
        return sorted(path.resolve() for path in resolved_candidate.rglob(JSON_GLOB) if path.is_file())
    if not resolved_candidate.is_file() or resolved_candidate.suffix.lower() != ".json":
        return []

    selected_files: list[Path] = [resolved_candidate]
    if resolved_candidate.name.lower() in DEPENDENCY_FILES:
        for dependency_name in DEPENDENCY_FILES:
            dependency_path = resolved_candidate.parent / dependency_name
            if dependency_path.exists() and dependency_path.is_file():
                selected_files.append(dependency_path.resolve())
    return selected_files


def _matches_relative_selection(
    *,
    rel_path: str,
    file_name: str,
    normalized_only: str,
    only_is_json_file: bool,
) -> bool:
    if not normalized_only:
        return True
    if only_is_json_file:
        return rel_path == normalized_only or file_name in DEPENDENCY_FILES
    return rel_path == normalized_only or rel_path.startswith(f"{normalized_only}/")


def _safe_extract_zip(zip_path: Path, extract_dir: Path) -> None:
    extract_root = extract_dir.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        members = archive.infolist()
        file_members = [member for member in members if not member.is_dir()]
        total_files = len(file_members)
        LOGGER.info(
            "zip_extract_start zip=%s extract_dir=%s total_entries=%s total_files=%s",
            zip_path,
            extract_root,
            len(members),
            total_files,
        )
        if total_files == 0:
            LOGGER.warning("zip_extract_empty zip=%s has no files to extract", zip_path)

        next_progress_mark = 10
        extracted_files = 0
        for member in members:
            candidate = (extract_root / member.filename).resolve()
            try:
                candidate.relative_to(extract_root)
            except ValueError as exc:
                raise ValueError(f"Unsafe zip entry path: {member.filename}") from exc
            archive.extract(member, extract_root)
            if member.is_dir():
                continue
            extracted_files += 1
            if total_files > 0:
                progress_percent = int((extracted_files * 100) / total_files)
                while progress_percent >= next_progress_mark and next_progress_mark <= 100:
                    LOGGER.info(
                        "zip_extract_progress zip=%s extracted=%s/%s progress=%s%%",
                        zip_path,
                        extracted_files,
                        total_files,
                        next_progress_mark,
                    )
                    next_progress_mark += 10


def _extract_zip_json_files(zip_path: Path) -> list[Path]:
    if not zip_path.exists() or not zip_path.is_file() or zip_path.suffix.lower() != ".zip":
        return []
    extract_dir = zip_path.parent / ".ingest_extracted" / zip_path.stem
    if extract_dir.exists():
        shutil.rmtree(extract_dir, ignore_errors=True)
    extract_dir.mkdir(parents=True, exist_ok=True)
    started_at = time.perf_counter()
    _safe_extract_zip(zip_path, extract_dir)
    extracted_json_files = sorted(path.resolve() for path in extract_dir.rglob(JSON_GLOB) if path.is_file())
    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    LOGGER.info(
        "zip_extract_complete zip=%s json_files=%s extract_dir=%s duration_ms=%s",
        zip_path,
        len(extracted_json_files),
        extract_dir.resolve(),
        elapsed_ms,
    )
    return extracted_json_files


def _collect_relative_json_files(json_root_path: Path, only: str | None) -> list[Path]:
    normalized_only = (only or "").strip("/")
    only_is_json_file = normalized_only.lower().endswith(".json")
    selected_files: list[Path] = []
    for path in sorted(json_root_path.rglob(JSON_GLOB)):
        rel_path = str(path.relative_to(json_root_path)).replace("\\", "/")
        file_name = path.name.lower()
        if not _matches_relative_selection(
            rel_path=rel_path,
            file_name=file_name,
            normalized_only=normalized_only,
            only_is_json_file=only_is_json_file,
        ):
            continue
        selected_files.append(path)
    return selected_files


def _resolve_record_path(path: Path, json_root_path: Path) -> str:
    try:
        return str(path.relative_to(json_root_path)).replace("\\", "/")
    except ValueError:
        return path.name


def _load_json_files_with_dependencies(json_root_path: Path, only_file_path: str | None) -> dict[str, list[dict[str, Any]]]:
    only = _normalize_only_file_path(only_file_path)
    selected_files = _collect_absolute_json_files(only_file_path)
    if not selected_files:
        if only and only.lower().endswith(".zip"):
            selected_files = _extract_zip_json_files((json_root_path / only).resolve())
        else:
            selected_files = _collect_relative_json_files(json_root_path, only)

    records_by_file: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(set(selected_files)):
        rel_path = _resolve_record_path(path, json_root_path)
        records = _load_json_records(path)
        if records:
            records_by_file[rel_path] = records
    return records_by_file


def _parse_from_posts_dataset(
    json_root_path: Path,
    records_by_file: dict[str, list[dict[str, Any]]],
    *,
    remove_code_blocks: bool,
) -> list[SourceDocument]:
    posts_records = records_by_file.get("posts.json", [])
    if not posts_records:
        return []

    questions, answers_by_parent = _extract_posts_graph(posts_records)
    comments_by_post = _group_by_post_id(records_by_file.get("comments.json", []), ("postId", "post_id", "parentId", "parent_id"))
    votes_by_post = _group_by_post_id(records_by_file.get("posts2votes.json", []), ("postId", "post_id"))
    users_by_id = _build_user_lookup(records_by_file.get("users.json", []))
    related_json_files = sorted(records_by_file.keys())

    docs: list[SourceDocument] = []
    for qid, question in questions.items():
        source_doc = _build_posts_source_document(
            qid=qid,
            question=question,
            answers_by_parent=answers_by_parent,
            comments_by_post=comments_by_post,
            votes_by_post=votes_by_post,
            users_by_id=users_by_id,
            related_json_files=related_json_files,
            json_root_path=json_root_path,
            remove_code_blocks=remove_code_blocks,
        )
        if source_doc is not None:
            docs.append(source_doc)
    return docs


def _extract_posts_graph(posts_records: list[dict[str, Any]]) -> tuple[dict[int, dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    questions: dict[int, dict[str, Any]] = {}
    answers_by_parent: dict[int, list[dict[str, Any]]] = {}
    for post in posts_records:
        post_id = _to_int(_get_any(post, "id", "post_id", "postId"))
        if post_id is None:
            continue
        post_type = _normalize_post_type(_get_any(post, "postType", "post_type", "postTypeId", "post_type_id"))
        if post_type == "question":
            questions[post_id] = post
            continue
        if post_type == "answer":
            parent_id = _to_int(_get_any(post, "parentId", "parent_id"))
            if parent_id is not None:
                answers_by_parent.setdefault(parent_id, []).append(post)
    return questions, answers_by_parent


def _build_posts_source_document(
    *,
    qid: int,
    question: dict[str, Any],
    answers_by_parent: dict[int, list[dict[str, Any]]],
    comments_by_post: dict[int, list[dict[str, Any]]],
    votes_by_post: dict[int, list[dict[str, Any]]],
    users_by_id: dict[int, dict[str, Any]],
    related_json_files: list[str],
    json_root_path: Path,
    remove_code_blocks: bool,
) -> SourceDocument | None:
    candidate_answers = _sort_answers(answers_by_parent.get(qid, []))
    primary_answer = _select_primary_answer(question, candidate_answers)
    primary_answer_id = _to_int(_get_any(primary_answer or {}, "id", "post_id", "postId"))
    related_answers = [ans for ans in candidate_answers if _to_int(_get_any(ans, "id", "post_id", "postId")) != primary_answer_id][:2]

    question_title = str(_get_any(question, "title", "question_title") or f"Question {qid}")
    post_link = _extract_post_link(question, qid)
    question_body = _clean_html(
        _get_any(question, "body", "bodyMarkdown", "question_body"),
        remove_code_blocks=remove_code_blocks,
    )
    primary_answer_body = _clean_html(
        _get_any(primary_answer or {}, "body", "bodyMarkdown", "answer_body"),
        remove_code_blocks=remove_code_blocks,
    )
    related_answer_bodies = [
        _clean_html(_get_any(answer, "body", "bodyMarkdown", "answer_body"), remove_code_blocks=remove_code_blocks)
        for answer in related_answers
    ]
    related_answer_bodies = [body for body in related_answer_bodies if body]

    question_comments_text = _format_comment_text(comments_by_post.get(qid, [])[:5])
    answer_comments_text = _format_comment_text(comments_by_post.get(primary_answer_id or -1, [])[:5])
    vote_summary = _format_vote_summary(votes_by_post.get(qid, []))
    content = _build_content(
        question_title=question_title,
        post_link=post_link,
        question_body=question_body,
        primary_answer_body=primary_answer_body,
        related_answer_bodies=related_answer_bodies,
        question_comments=question_comments_text,
        answer_comments=answer_comments_text,
        vote_summary=vote_summary,
    )
    if not content:
        return None

    owner_id = _to_int(_get_any(question, "ownerUserId", "owner_user_id"))
    owner = users_by_id.get(owner_id) if owner_id is not None else None
    tags = _parse_tags(_get_any(question, "tags"))
    created = _get_any(question, "creationDate", "creation_date")
    all_comments = comments_by_post.get(qid, []) + comments_by_post.get(primary_answer_id or -1, [])
    updated_at = _latest_post_activity_iso(question, candidate_answers, all_comments)
    metadata = {
        "post_id": qid,
        "post_link": post_link,
        "file_path": f"{json_root_path.name}/posts.json",
        "tags": tags,
        "creation_date": str(created) if created is not None else None,
        "updated_at": updated_at,
        "accepted_answer_id": _get_any(question, "acceptedAnswerId", "accepted_answer_id"),
        "primary_answer_id": primary_answer_id,
        "answer_count": len(candidate_answers),
        "comment_count": len(comments_by_post.get(qid, [])),
        "vote_count": len(votes_by_post.get(qid, [])),
        "owner_user_id": owner_id,
        "owner_reputation": _get_any(owner or {}, "reputation"),
        "related_json_files": related_json_files,
    }
    return SourceDocument(
        source_type="json",
        source_id=str(qid),
        content=content,
        title=question_title,
        heading=question_title,
        tags=tags,
        creation_date=str(created) if created is not None else None,
        metadata=metadata,
    )


def _truncate_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip() + " ... [truncated]"


def _first_non_empty_str(record: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        raw = _get_any(record, key)
        text = str(raw or "").strip()
        if text:
            return text
    return None


def _stringify_json_value(value: Any, *, max_chars: int = GENERIC_JSON_MAX_VALUE_CHARS) -> str:
    if isinstance(value, str):
        cleaned = value.strip()
        if LOW_VALUE_BLOB_RE.search(cleaned):
            return ""
        if "<" in cleaned and ">" in cleaned:
            cleaned = _clean_html(cleaned, remove_code_blocks=False)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return _truncate_text(cleaned, max_chars)
    if isinstance(value, (int, float, bool)) or value is None:
        return str(value)
    try:
        dumped = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        dumped = str(value)
    if LOW_VALUE_BLOB_RE.search(dumped):
        return ""
    return _truncate_text(dumped, max_chars)


def _generic_record_identity(record: dict[str, Any], rel_path: str, index: int) -> tuple[str, str]:
    candidate_id = _first_non_empty_str(
        record,
        "ticket_id",
        "id",
        "post_id",
        "postId",
        "source_id",
        "uuid",
    )
    source_id = candidate_id or f"{rel_path}#{index}"
    nested_ticket = record.get("ticket") if isinstance(record.get("ticket"), dict) else {}
    nested_excel = record.get("excel_context") if isinstance(record.get("excel_context"), dict) else {}
    title = (
        _first_non_empty_str(record, "subject", "title", "name", "summary")
        or _first_non_empty_str(nested_ticket, "subject", "title")
        or _first_non_empty_str(nested_excel, "Subject", "Summary")
        or f"JSON record {index}"
    )
    title = _truncate_text(title, GENERIC_JSON_MAX_TITLE_CHARS)
    return source_id, title


def _record_to_source_document(record: dict[str, Any], *, rel_path: str, index: int) -> SourceDocument | None:
    source_id, title = _generic_record_identity(record, rel_path, index)
    lines: list[str] = [f"Source File: {rel_path}", f"Record Index: {index}", f"Record Title: {title}"]

    key_lookup = {str(key).lower(): key for key in record.keys()}
    ordered_keys: list[Any] = []
    for priority_key in GENERIC_JSON_PRIORITY_KEYS:
        original_key = key_lookup.get(priority_key)
        if original_key is not None:
            ordered_keys.append(original_key)
    for key in sorted(record.keys(), key=lambda item: str(item).lower()):
        if key in ordered_keys:
            continue
        ordered_keys.append(key)

    for key in ordered_keys:
        value = record.get(key)
        rendered = _stringify_json_value(value)
        if not rendered.strip():
            continue
        lines.append(f"{key}: {rendered}")

    content = "\n\n".join(lines).strip()
    if not content:
        return None

    metadata: dict[str, Any] = {
        "file_path": rel_path,
        "json_source_file": rel_path,
        "record_index": index,
        "top_level_keys": sorted(str(key) for key in record.keys()),
        "record_title": title,
    }
    return SourceDocument(
        source_type="json",
        source_id=source_id,
        content=content,
        title=title,
        heading=title,
        tags=[],
        creation_date=_first_non_empty_str(record, "created_at", "creation_date", "creationDate"),
        metadata=metadata,
    )


def _parse_generic_json_records(records_by_file: dict[str, list[dict[str, Any]]]) -> list[SourceDocument]:
    docs: list[SourceDocument] = []
    for rel_path, records in records_by_file.items():
        for index, record in enumerate(records, start=1):
            source_doc = _record_to_source_document(record, rel_path=rel_path, index=index)
            if source_doc is not None:
                docs.append(source_doc)
    return docs


def parse_stackoverflow_json_files(
    json_root_path: Path,
    *,
    only_file_path: str | None = None,
    remove_code_blocks: bool = True,
) -> list[SourceDocument]:
    if not json_root_path.exists():
        return []

    records_by_file = _load_json_files_with_dependencies(json_root_path, only_file_path)
    docs_from_posts = _parse_from_posts_dataset(
        json_root_path,
        records_by_file,
        remove_code_blocks=remove_code_blocks,
    )
    if docs_from_posts:
        LOGGER.info("json_parse_mode mode=stackoverflow_posts docs=%s files=%s", len(docs_from_posts), len(records_by_file))
        return docs_from_posts

    docs: list[SourceDocument] = []
    all_records: list[dict[str, Any]] = []
    for rel_path, records in records_by_file.items():
        for record in records:
            enriched = dict(record)
            enriched["__source_file"] = rel_path
            all_records.append(enriched)
    questions, answers_by_parent, comments_by_post = _extract_entities(all_records)
    for qid, question in questions.items():
        source_doc = _to_source_document(
            qid=qid,
            question=question,
            candidate_answers=answers_by_parent.get(qid, []),
            comment_rows=comments_by_post.get(qid, []),
            json_root_path=json_root_path,
            remove_code_blocks=remove_code_blocks,
        )
        if source_doc is not None:
            docs.append(source_doc)

    if docs:
        LOGGER.info("json_parse_mode mode=stackoverflow_entities docs=%s files=%s", len(docs), len(records_by_file))
        return docs

    # Generic fallback for non-StackOverflow JSON exports (e.g. Freshdesk conversation dumps).
    generic_docs = _parse_generic_json_records(records_by_file)
    LOGGER.info("json_parse_mode mode=generic_records docs=%s files=%s", len(generic_docs), len(records_by_file))
    return generic_docs

