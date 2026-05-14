"""
rag_engine/document_builder/sop_builder.py

Builds RagSopDocument objects from SOP source files (Markdown/text).
Used when ingesting from Fumadocs/Bitbucket or knowledge cards.

SOP documents are chunked by heading section (H2/H3), so each chunk
covers one step group. This allows retrieval of specific SOP steps
rather than the entire SOP.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sop_deterministic_id(sop_key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"sop:{sop_key}"))


@dataclass
class RagSopDocument:
    """A fully constructed SOP document ready for chunking."""
    sop_id: str                             # Deterministic from file path
    title: str
    query_type: Optional[str] = None
    issue_area: Optional[str] = None
    clients: list[str] = field(default_factory=list)  # [] = global SOP
    content: str = ""
    content_hash: str = ""
    source: str = "fumadocs"
    source_path: Optional[str] = None
    commit_sha: Optional[str] = None
    version: int = 1

    # Pre-parsed sections for the chunker
    sections: list[dict] = field(default_factory=list)
    # [{"heading": "Step 1: Check Network", "content": "..."}, ...]


class SopDocumentBuilder:
    """
    Builds SOP documents from Markdown source files.
    Extracts metadata from YAML frontmatter (if present) or filename conventions.
    """

    # Frontmatter extraction
    _FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
    _HEADING_RE = re.compile(r"^(#{2,3})\s+(.+)$", re.MULTILINE)

    def build_from_markdown(
        self,
        file_path: Path,
        commit_sha: Optional[str] = None,
        default_clients: Optional[list[str]] = None,
    ) -> Optional[RagSopDocument]:
        """
        Build a SOP document from a Markdown file.
        Extracts: title, query_type, clients from YAML frontmatter.
        Sections split on H2/H3 headings.
        """
        try:
            raw = file_path.read_text(encoding="utf-8")
        except OSError:
            return None

        # Parse frontmatter
        metadata: dict[str, str] = {}
        content = raw
        fm_match = self._FRONTMATTER_RE.match(raw)
        if fm_match:
            metadata = self._parse_simple_yaml(fm_match.group(1))
            content = raw[fm_match.end():]

        # Derive SOP ID from file path (stable across renames within same dir)
        sop_key = str(file_path.stem).lower().replace(" ", "_")
        sop_id = _sop_deterministic_id(sop_key)

        title = metadata.get("title") or self._title_from_filename(file_path)
        query_type = metadata.get("query_type") or metadata.get("category")
        issue_area = metadata.get("issue_area") or metadata.get("sub_category")
        clients_raw = metadata.get("clients", "")
        clients = (
            [c.strip() for c in clients_raw.split(",") if c.strip()]
            if clients_raw
            else (default_clients or [])
        )

        # Clean content (remove frontmatter)
        clean_content = content.strip()
        content_hash = _sha256(clean_content)

        # Split into sections by heading
        sections = self._split_sections(clean_content)

        return RagSopDocument(
            sop_id=sop_id,
            title=title,
            query_type=query_type,
            issue_area=issue_area,
            clients=clients,
            content=clean_content,
            content_hash=content_hash,
            source="fumadocs",
            source_path=str(file_path),
            commit_sha=commit_sha,
            sections=sections,
        )

    def build_from_knowledge_card(
        self,
        title: str,
        content: str,
        query_type: Optional[str] = None,
        clients: Optional[list[str]] = None,
    ) -> RagSopDocument:
        """Build a SOP from a manually-created knowledge card (from /train endpoint)."""
        sop_key = re.sub(r"\W+", "_", title.lower())[:64]
        sop_id = _sop_deterministic_id(f"kc:{sop_key}")
        content_hash = _sha256(content)
        sections = self._split_sections(content)
        return RagSopDocument(
            sop_id=sop_id,
            title=title,
            query_type=query_type,
            clients=clients or [],
            content=content,
            content_hash=content_hash,
            source="knowledge_card",
            sections=sections,
        )

    def _split_sections(self, content: str) -> list[dict]:
        """Split markdown content on H2/H3 headings into sections."""
        heading_positions = [
            (m.start(), m.group(2).strip())
            for m in self._HEADING_RE.finditer(content)
        ]
        if not heading_positions:
            return [{"heading": None, "content": content}]

        sections: list[dict] = []
        # Content before first heading
        if heading_positions[0][0] > 0:
            preamble = content[: heading_positions[0][0]].strip()
            if preamble:
                sections.append({"heading": None, "content": preamble})

        for i, (pos, heading) in enumerate(heading_positions):
            next_pos = heading_positions[i + 1][0] if i + 1 < len(heading_positions) else len(content)
            # Skip past the heading line itself
            line_end = content.index("\n", pos) + 1 if "\n" in content[pos:] else pos + len(heading)
            section_content = content[line_end:next_pos].strip()
            if section_content:
                sections.append({"heading": heading, "content": section_content})

        return sections

    @staticmethod
    def _parse_simple_yaml(text: str) -> dict[str, str]:
        """Minimal YAML parser for single-level string key: value frontmatter."""
        result: dict[str, str] = {}
        for line in text.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                result[key.strip()] = value.strip().strip('"').strip("'")
        return result

    @staticmethod
    def _title_from_filename(path: Path) -> str:
        return path.stem.replace("-", " ").replace("_", " ").title()
