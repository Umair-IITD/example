"""
scripts/diagnose_so905_fast.py

Fast post-fix diagnostic for so_905 -- processes only so_905 directly.
"""
import sys, os, json, re
from pathlib import Path
sys.path.insert(0, ".")
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(usecwd=True))

from rag_engine.ingestion.parsers.stackoverflow_parser import _normalize_markdown
from rag_engine.ingestion.knowledge_pipeline import _build_embed_text_raw
from rag_engine.ingestion.knowledge_classifier import KnowledgeClass
from rag_engine.chunking.knowledge_chunker import KnowledgeChunker
from rag_engine.chunking.chunk_quality_filter import ChunkQualityFilter, ChunkQualityConfig

DATA_DIR = os.environ.get(
    "B3_KNOWLEDGE_SOURCE_DIR",
    "C:/Users/Umair.Alam/Desktop/kwikid_support_system/stackoverflow",
)

_HTML_TAG_RE = re.compile(r"<[^>]+>")

def clean_text(html: str) -> str:
    return _HTML_TAG_RE.sub(" ", html)

def out(s):
    sys.stdout.buffer.write((s + "\n").encode("utf-8", errors="replace"))

with open(f"{DATA_DIR}/posts.json", "r", encoding="utf-8") as f:
    raw = json.load(f)
posts = raw.get("items", list(raw.values())[0]) if isinstance(raw, dict) else raw

TARGET_ID = 905
question = next((p for p in posts if p.get("postType") == "question" and p.get("id") == TARGET_ID), None)
answers   = [p for p in posts if p.get("postType") == "answer" and p.get("parentId") == TARGET_ID]

accepted_id = question.get("acceptedAnswerId")
best = next((a for a in answers if a.get("id") == accepted_id), None)
if best is None and answers:
    valid = [a for a in answers if a.get("postState", "Published") != "Deleted"]
    if valid:
        best = max(valid, key=lambda a: (int(a.get("score", 0)), int(a.get("id", 0))))

q_md = _normalize_markdown(str(question.get("bodyMarkdown", "") or "")).strip()
a_md = ""
if best:
    raw_md = str(best.get("bodyMarkdown", "") or "")
    a_md = _normalize_markdown(raw_md).strip() if raw_md else clean_text(str(best.get("body", "") or "")).strip()

# Parse SO tag string ("|unity|logs|cbs|") into list
raw_tags = question.get("tags", "")
if isinstance(raw_tags, str):
    tags_list = [t for t in raw_tags.strip("|").split("|") if t]
else:
    tags_list = list(raw_tags)

out(f"=== so_905: {question.get('title', '')[:80]} ===")
out(f"Answers: {len(answers)} | Best answer: #{best.get('id') if best else 'none'} score={best.get('score',0) if best else '-'}")
out(f"q_md chars: {len(q_md)}")
out(f"a_md chars: {len(a_md)}")
out(f"tags: {tags_list}")
out("")

embed_text = _build_embed_text_raw(
    title=question.get("title", ""),
    question_body=q_md,
    answer_body=a_md or None,
    knowledge_class=KnowledgeClass.TROUBLESHOOTING,
    tags_raw=tags_list,
    completeness_score=0.6,
    image_refs=[],
)
out(f"embed_text ({len(embed_text)} chars):")
out("-" * 60)
sys.stdout.buffer.write(embed_text[:1200].encode("utf-8", errors="replace"))
if len(embed_text) > 1200:
    sys.stdout.buffer.write(b"\n...\n")
out("")

# Run new chunker (min_chunk_chars=100 = production)
chunker = KnowledgeChunker(
    knowledge_class=KnowledgeClass.TROUBLESHOOTING,
    chunk_target_tokens=1200,
    chunk_overlap_tokens=150,
    min_chunk_chars=100,
)
chunks = chunker.chunk(embed_text, article_id="so_905", index_version="v2")
out(f"Chunker: {len(chunks)} chunks (min_chunk_chars=100, NEW carry logic):")
for c in chunks:
    out(f"  [{c.chunk_index}] {c.chunk_type:16s} chars={len(c.content):4d} is_code={c.is_code}")
    preview = c.content[:90].replace("\n", " ")
    out(f"       {preview!r}")
out("")

# Quality filter with code bypass
code_chunks  = [c for c in chunks if c.chunk_type in ("CODE_SAMPLE", "COMMAND")]
prose_chunks = [c for c in chunks if c.chunk_type not in ("CODE_SAMPLE", "COMMAND")]
qf = ChunkQualityFilter(ChunkQualityConfig(min_content_chars=30, min_alpha_chars=20, min_token_count=8))

out("Quality filter with code bypass:")
passed_prose = []
for c in prose_chunks:
    r = qf.check(c.content)
    status = "PASS" if r.is_valid else f"FAIL({r.rejection_reason})"
    out(f"  [prose] idx={c.chunk_index} {c.chunk_type:16s} chars={len(c.content):4d} -> {status}")
    if r.is_valid:
        passed_prose.append(c)
for c in code_chunks:
    out(f"  [code]  idx={c.chunk_index} {c.chunk_type:16s} chars={len(c.content):4d} -> BYPASS(always pass)")

total = len(passed_prose) + len(code_chunks)
out("")
out(f"RESULT: {total}/{len(chunks)} chunks survive -> {'INGESTED' if total > 0 else 'REJECTED'}")
if total > 0:
    total_chars = sum(len(c.content) for c in passed_prose + code_chunks)
    src_chars = len(q_md) + len(a_md)
    ratio = total_chars / src_chars * 100 if src_chars > 0 else 0
    out(f"Stored chars:  {total_chars}")
    out(f"Source chars:  {src_chars}")
    out(f"Preservation:  {ratio:.1f}%")
