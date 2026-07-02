"""
scripts/fidelity_projection.py

Phase 5 verification: simulate the FIXED chunker + quality filter bypass
across all 325 ingested articles WITHOUT hitting the DB.

Compares:
  - OLD behavior: hard min_chunk_chars drop (simulated)
  - NEW behavior: KnowledgeChunker carry logic + code quality bypass

Reports projected improvement before corpus rebuild (Phase 6).
"""
import sys, os, json, re
from pathlib import Path
sys.path.insert(0, ".")
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(usecwd=True))

from supabase import create_client
from rag_engine.ingestion.parsers.stackoverflow_parser import _normalize_markdown
from rag_engine.ingestion.knowledge_pipeline import _build_embed_text_raw
from rag_engine.ingestion.knowledge_classifier import KnowledgeClass
from rag_engine.chunking.knowledge_chunker import (
    KnowledgeChunker, _FENCED_CODE_RE, _MIN_CODE_CHARS, _PARA_BREAK_RE
)
from rag_engine.chunking.chunk_quality_filter import ChunkQualityFilter, ChunkQualityConfig

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
DATA_DIR = os.environ.get(
    "B3_KNOWLEDGE_SOURCE_DIR",
    "C:/Users/Umair.Alam/Desktop/kwikid_support_system/stackoverflow",
)

_HTML_TAG_RE = re.compile(r"<[^>]+>")

def clean_text(html):
    return _HTML_TAG_RE.sub(" ", html)

def out(s):
    sys.stdout.buffer.write((s + "\n").encode("utf-8", errors="replace"))

# Load source posts
with open(f"{DATA_DIR}/posts.json", "r", encoding="utf-8") as f:
    raw = json.load(f)
posts = raw.get("items", list(raw.values())[0]) if isinstance(raw, dict) else raw

# Load DB article list (to know which 325 were ingested)
arts_db = {}
offset = 0
while True:
    r = sb.table("rag_knowledge_articles").select(
        "article_id,title,knowledge_class"
    ).range(offset, offset + 999).execute()
    for a in r.data:
        arts_db[a["article_id"]] = a
    if len(r.data) < 1000:
        break
    offset += 1000

out(f"DB articles: {len(arts_db)}")

# Build answer map
ans_map = {}
for p in posts:
    if p.get("postType") == "answer":
        pid = p.get("parentId")
        if isinstance(pid, int):
            ans_map.setdefault(pid, []).append(p)

questions = {p["id"]: p for p in posts if p.get("postType") == "question"}

def simulate_old(embed_text, min_chars=100):
    """Old behavior: hard drop of short segments."""
    segments = []
    last_end = 0
    for match in _FENCED_CODE_RE.finditer(embed_text):
        start, end = match.span()
        if start > last_end:
            for para in _PARA_BREAK_RE.split(embed_text[last_end:start]):
                para = para.strip()
                if para:
                    segments.append(para)
        cc = match.group(0)
        if len(cc.strip()) >= _MIN_CODE_CHARS:
            segments.append(cc)
        else:
            segments.append(cc)
        last_end = end
    if last_end < len(embed_text):
        for para in _PARA_BREAK_RE.split(embed_text[last_end:]):
            para = para.strip()
            if para:
                segments.append(para)
    kept = [s for s in segments if len(s.strip()) >= min_chars]
    return sum(len(s) for s in kept)

# Quality filter (production settings)
qf = ChunkQualityFilter(ChunkQualityConfig(min_content_chars=30, min_alpha_chars=20, min_token_count=8))

total_src = 0
total_old = 0
total_new = 0
old_code_dropped = 0
new_code_dropped = 0
articles_improved = 0
articles_unchanged = 0
articles_total = 0

improvements = []

for qid, q in questions.items():
    art_id = f"so_{qid}"
    if art_id not in arts_db:
        continue
    articles_total += 1

    title = q.get("title", "")
    q_md = _normalize_markdown(str(q.get("bodyMarkdown", "") or "")).strip()
    if not q_md:
        q_md = clean_text(str(q.get("body", "") or "")).strip()

    answers = ans_map.get(qid, [])
    accepted_id = q.get("acceptedAnswerId")
    best = None
    if accepted_id:
        best = next((a for a in answers if a.get("id") == accepted_id), None)
    if best is None and answers:
        valid = [a for a in answers if a.get("postState", "Published") != "Deleted"]
        if valid:
            best = max(valid, key=lambda a: (int(a.get("score", 0)), int(a.get("id", 0))))

    a_md = ""
    if best:
        raw_md = str(best.get("bodyMarkdown", "") or "")
        a_md = _normalize_markdown(raw_md).strip() if raw_md else clean_text(str(best.get("body", "") or "")).strip()

    src_chars = len(q_md) + len(a_md)
    total_src += src_chars

    klass_str = arts_db[art_id].get("knowledge_class", "FAQ")
    try:
        klass = KnowledgeClass(klass_str)
    except ValueError:
        klass = KnowledgeClass.FAQ

    embed_text = _build_embed_text_raw(
        title=title,
        question_body=q_md,
        answer_body=a_md or None,
        knowledge_class=klass,
        tags_raw=q.get("tags", "").strip("|").split("|") if isinstance(q.get("tags"), str) else list(q.get("tags", [])),
        completeness_score=0.6,
        image_refs=[],
    )

    # OLD simulation
    old_chars = simulate_old(embed_text, min_chars=100)
    total_old += old_chars

    # NEW simulation — use actual KnowledgeChunker with carry + code bypass
    chunker = KnowledgeChunker(
        knowledge_class=klass,
        chunk_target_tokens=1200,
        chunk_overlap_tokens=150,
        min_chunk_chars=100,
    )
    qf.reset_batch()
    chunks = chunker.chunk(embed_text, article_id=art_id, index_version="v2")

    code_chunks  = [c for c in chunks if c.chunk_type in ("CODE_SAMPLE", "COMMAND")]
    prose_chunks = [c for c in chunks if c.chunk_type not in ("CODE_SAMPLE", "COMMAND")]
    valid_prose  = [c for c in prose_chunks if qf.check(c.content).is_valid]
    all_valid = valid_prose + code_chunks

    new_chars = sum(len(c.content) for c in all_valid)
    total_new += new_chars

    if new_chars > old_chars + 50:
        articles_improved += 1
        gain_pct = (new_chars - old_chars) / max(old_chars, 1) * 100
        improvements.append({
            "art_id": art_id,
            "title": title[:60],
            "src_chars": src_chars,
            "old_chars": old_chars,
            "new_chars": new_chars,
            "gain_pct": gain_pct,
            "old_chunks": "n/a",
            "new_chunks": len(all_valid),
        })
    else:
        articles_unchanged += 1

old_pct = total_old / total_src * 100 if total_src else 0
new_pct = total_new / total_src * 100 if total_src else 0

out("")
out("=" * 70)
out(f"PHASE 5 FIDELITY PROJECTION — Fixed chunker vs. old behavior")
out("=" * 70)
out(f"Articles analyzed:            {articles_total}")
out(f"Source chars (Q+A markdown):  {total_src:,}")
out(f"OLD preserved chars:          {total_old:,}  ({old_pct:.1f}%)")
out(f"NEW projected chars:          {total_new:,}  ({new_pct:.1f}%)")
out(f"Delta:                        +{total_new - total_old:,} chars (+{new_pct - old_pct:.1f}pp)")
out(f"Articles improved:            {articles_improved}/{articles_total}")
out(f"Articles unchanged:           {articles_unchanged}/{articles_total}")
out("")
out(f"TOP 20 most-improved articles:")
improvements.sort(key=lambda x: -x["new_chars"] + x["old_chars"])
hdr = f"{'ART_ID':<12} {'OLD':>6} {'NEW':>6} {'GAIN%':>7} {'N_CHK':>5}  TITLE"
out(hdr)
out("-" * 80)
for iss in improvements[:20]:
    line = (
        f"{iss['art_id']:<12} {iss['old_chars']:>6} {iss['new_chars']:>6} "
        f"{iss['gain_pct']:>6.0f}%  {iss['new_chunks']:>4}  {iss['title']}"
    )
    sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))

out("")
out("CONCLUSION:")
if new_pct >= 80:
    out(f"  Projected preservation {new_pct:.1f}% >= 80% target. REBUILD AUTHORIZED.")
elif new_pct >= 70:
    out(f"  Projected preservation {new_pct:.1f}%. Significant improvement. Consider rebuild.")
else:
    out(f"  Projected preservation {new_pct:.1f}%. Further fixes may be needed.")
