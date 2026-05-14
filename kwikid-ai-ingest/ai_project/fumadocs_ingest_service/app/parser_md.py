from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from app.chunker import SourceDocument


FRONTMATTER_PATTERN = re.compile(r"^---\r?\n.*?\r?\n---\r?\n?", flags=re.DOTALL)
MARKDOWN_EXTENSIONS = {".md", ".mdx", ".markdown"}

def _normalize_only_file_path(only_file_path: str) -> str:
    """Normalize user-provided file_path for comparison against repo-relative paths."""
    normalized = only_file_path.strip().replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.startswith("/"):
        normalized = normalized[1:]
    return normalized


def _extract_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _extract_heading(text: str) -> str | None:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("## "):
            return line[3:].strip()
    return None


def _normalize_markdown(text: str) -> str:
    normalized_newlines = text.replace("\r\n", "\n").replace("\r", "\n")
    without_frontmatter = FRONTMATTER_PATTERN.sub("", normalized_newlines, count=1)
    normalized = re.sub(r"\n{3,}", "\n\n", without_frontmatter)
    return normalized.strip()


def _absolute_markdown_path(only_file_path: str | None) -> Path | None:
    if not only_file_path:
        return None
    candidate_path = Path(only_file_path).expanduser()
    if not candidate_path.is_absolute():
        return None
    resolved_candidate = candidate_path.resolve()
    if not resolved_candidate.exists() or not resolved_candidate.is_file():
        return None
    if resolved_candidate.suffix.lower() not in MARKDOWN_EXTENSIONS:
        return None
    return resolved_candidate


def _document_from_path(path: Path, *, source_id: str, metadata_file_path: str) -> SourceDocument | None:
    text = path.read_text(encoding="utf-8", errors="ignore")
    normalized = _normalize_markdown(text)
    if not normalized:
        return None
    fallback_title = path.stem
    heading = _extract_heading(normalized) or fallback_title
    return SourceDocument(
        source_type="md",
        source_id=source_id,
        content=normalized,
        title=_extract_title(normalized, fallback_title),
        heading=heading,
        tags=[],
        creation_date=None,
        metadata={"file_path": metadata_file_path},
    )


def _iter_repo_markdown_paths(repo_path: Path, glob_patterns: list[str], only: str | None) -> list[tuple[Path, str]]:
    matches: list[tuple[Path, str]] = []
    for path in sorted(repo_path.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in MARKDOWN_EXTENSIONS:
            continue
        rel_path = str(path.relative_to(repo_path)).replace("\\", "/")
        if not _matches_glob(rel_path, glob_patterns):
            continue
        if only and rel_path != only:
            continue
        matches.append((path, rel_path))
    return matches


def _expand_docs_glob(docs_glob: str | None) -> list[str]:
    if not docs_glob:
        return []
    text = docs_glob.strip()
    if not text:
        return []
    if "{" not in text or "}" not in text:
        return [text]
    prefix, rest = text.split("{", 1)
    inner, suffix = rest.split("}", 1)
    patterns = [f"{prefix}{part.strip()}{suffix}" for part in inner.split(",") if part.strip()]
    return patterns or [text]


def _matches_glob(rel_path: str, patterns: list[str]) -> bool:
    rel_posix = PurePosixPath(rel_path)
    for pattern in patterns:
        candidate_patterns = [pattern]
        if "/**/" in pattern:
            candidate_patterns.append(pattern.replace("/**/", "/", 1))
        if any(rel_posix.match(pat) for pat in candidate_patterns):
            return True
        if fnmatch(rel_path, pattern):
            return True
        if pattern.startswith("**/") and fnmatch(rel_path, pattern[3:]):
            return True
    return False


def parse_markdown_files(
    repo_path: Path,
    *,
    only_file_path: str | None = None,
    docs_glob: str | None = None,
) -> list[SourceDocument]:
    only: str | None = None
    if only_file_path:
        # Normalize user input and repo-relative paths to forward slashes.
        only = _normalize_only_file_path(only_file_path)

    # Allow ingesting an explicitly provided absolute markdown file path
    # (used by frontend uploads stored outside the git-synced repo tree).
    only_path = _absolute_markdown_path(only_file_path)
    if only_path is not None:
        source_id = str(only_path).replace("\\", "/")
        doc = _document_from_path(only_path, source_id=source_id, metadata_file_path=source_id)
        return [doc] if doc else []

    glob_patterns = _expand_docs_glob(docs_glob) or ["**/*.md", "**/*.mdx"]
    docs: list[SourceDocument] = []
    for path, rel_path in _iter_repo_markdown_paths(repo_path, glob_patterns, only):
        doc = _document_from_path(path, source_id=rel_path, metadata_file_path=rel_path)
        if doc is not None:
            docs.append(doc)
    return docs
