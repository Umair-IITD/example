"""
scripts/diagnose_so905.py

Post-fix diagnostic for so_905 "How to retrigger CBS customer creation".
Confirms both fixes work:
  1. KnowledgeChunker carry logic (short segments no longer dropped)
  2. ChunkQualityFilter bypass for CODE_SAMPLE/COMMAND chunks
"""
import sys, os, json
from pathlib import Path
sys.path.insert(0, ".")
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(usecwd=True))

from rag_engine.ingestion.parsers.stackoverflow_parser import StackOverflowParser
from rag_engine.ingestion.knowledge_classifier import KnowledgeClass
from rag_engine.ingestion.knowledge_pipeline import _build_embed_text_raw
from rag_engine.chunking.knowledge_chunker import KnowledgeChunker
from rag_engine.chunking.chunk_quality_filter import ChunkQualityFilter, ChunkQualityConfig

DATA_DIR = os.environ.get(
    "B3_KNOWLEDGE_SOURCE_DIR",
    "C:/Users/Umair.Alam/Desktop/kwikid_support_system/stackoverflow",
)

# Load source
with open(f"{DATA_DIR}/posts.json", "r", encoding="utf-8") as f:
    raw = json.load(f)
posts = raw.get("items", list(raw.values())[0]) if isinstance(raw, dict) else raw

# Locate so_905
TARGET_ID = 905
question = next((p for p in posts if p.get("postType") == "question" and p.get("id") == TARGET_ID), None)
if not question:
    print("ERROR: so_905 not found in posts.json")
    sys.exit(1)

answers = [p for p in posts if p.get("postType") == "answer" and p.get("parentId") == TARGET_ID]
accepted_id = question.get("acceptedAnswerId")
best = next((a for a in answers if a.get("id") == accepted_id), None)
if best is None and answers:
    valid = [a for a in answers if a.get("postState", "Published") != "Deleted"]
    if valid:
        best = max(valid, key=lambda a: (int(a.get("score", 0)), int(a.get("id", 0))))

print(f"=== so_905: {question.get('title', '')[:80]} ===")
print(f"Answers: {len(answers)} | Best answer ID: {best.get('id') if best else 'none'}")

# Parse via StackOverflowParser to get normalized article
parser = StackOverflowParser()
articles = parser.parse(Path(DATA_DIR))
article = next((a for a in articles if a.article_id == "so_905"), None)
if not article:
    print("ERROR: so_905 not produced by StackOverflowParser (was filtered out)")
    sys.exit(1)

print(f"\nParsed article:")
print(f"  question_body chars: {len(article.question_body)}")
print(f"  answer_body chars:   {len(article.answer_body or '')}")

# Build embed text
embed_text = _build_embed_text_raw(
    title=article.title,
    question_body=article.question_body,
    answer_body=article.answer_body,
    knowledge_class=KnowledgeClass.VERIFIED_REPLY,
    tags_raw=article.tags_raw,
    completeness_score=getattr(article, "completeness_score", None),
    image_refs=[],
)
print(f"  embed_text chars:    {len(embed_text)}")

# Run chunker (min_chunk_chars=100 = production setting)
chunker = KnowledgeChunker(
    knowledge_class=KnowledgeClass.VERIFIED_REPLY,
    chunk_target_tokens=1200,
    chunk_overlap_tokens=150,
    min_chunk_chars=100,
)
chunks = chunker.chunk(embed_text, article_id="so_905", index_version="v2")

print(f"\nChunker output ({len(chunks)} chunks):")
for c in chunks:
    print(f"  [{c.chunk_index}] {c.chunk_type:16s} | chars={len(c.content):4d} | is_code={c.is_code}")
    print(f"       preview: {c.content[:80].replace(chr(10), ' ')!r}")

# Simulate quality filter (production settings from rag_settings defaults)
qf = ChunkQualityFilter(ChunkQualityConfig(
    min_content_chars=30,
    min_alpha_chars=20,
    min_token_count=8,
))

print(f"\nQuality filter check (full gate, no code bypass):")
for c in chunks:
    result = qf.check(c.content)
    status = "PASS" if result.is_valid else f"FAIL({result.rejection_reason})"
    print(f"  [{c.chunk_index}] {c.chunk_type:16s} is_code={c.is_code} → {status}")

# Now apply the FIXED bypass logic (code bypasses filter)
print(f"\nQuality filter with code bypass (production fix):")
code_chunks  = [c for c in chunks if c.chunk_type in ("CODE_SAMPLE", "COMMAND")]
prose_chunks = [c for c in chunks if c.chunk_type not in ("CODE_SAMPLE", "COMMAND")]
print(f"  Code chunks (bypass): {len(code_chunks)}")
print(f"  Prose chunks (gated): {len(prose_chunks)}")
qf.reset_batch()
passed = 0
for c in prose_chunks:
    r = qf.check(c.content)
    if r.is_valid:
        passed += 1
    print(f"  [prose] {c.chunk_type:16s} chars={len(c.content):4d} → {'PASS' if r.is_valid else f'FAIL({r.rejection_reason})'}")
for c in code_chunks:
    print(f"  [code]  {c.chunk_type:16s} chars={len(c.content):4d} → PASS (bypassed)")

total_valid = passed + len(code_chunks)
print(f"\nResult: {total_valid}/{len(chunks)} chunks survive → article would be INGESTED" if total_valid > 0 else "\nResult: 0 chunks survive → article still REJECTED")
