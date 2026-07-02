"""
scripts/fidelity_projection.py

Phase 5 verification: simulate ALL three fixes across all 325 ingested articles.

Fixes simulated:
  1. KnowledgeChunker carry logic (short segments carried forward, never dropped)
  2. ChunkQualityFilter code bypass (CODE_SAMPLE/COMMAND always kept)
  3. Multi-answer parser fix (ALL valid answers included, not just best)
  4. Question + all-answer comments included

Runs WITHOUT OCR (image text excluded from this projection).
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

def out(s):
    sys.stdout.buffer.write((s + "\n").encode("utf-8", errors="replace"))

_HTML_TAG_RE = re.compile(r"<[^>]+>")

def clean_text(html):
    return _HTML_TAG_RE.sub(" ", html)

def parse_tags(post):
    raw = post.get("tags")
    if isinstance(raw, list):
        return [str(t).strip().lower() for t in raw if str(t).strip()]
    if isinstance(raw, str):
        return [t.strip().lower() for t in re.split(r"[|,;]", raw) if t.strip()]
    return []

def format_comments(comments, min_chars=20):
    """Format all substantive comments (new behavior: no cap)."""
    if not comments:
        return ""
    scored = sorted(comments, key=lambda c: int(c.get("score", 0)), reverse=True)
    parts = []
    for c in scored:
        text = _normalize_markdown(str(c.get("bodyMarkdown", "") or c.get("body", ""))).strip()
        if text and len(text) >= min_chars:
            parts.append(f"  - {text}")
    if not parts:
        return ""
    return "\n\nComments:\n" + "\n".join(parts)

def simulate_old(embed_text, min_chars=100):
    """Old chunker: hard drop of short segments (no carry)."""
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
        segments.append(cc)
        last_end = end
    if last_end < len(embed_text):
        for para in _PARA_BREAK_RE.split(embed_text[last_end:]):
            para = para.strip()
            if para:
                segments.append(para)
    kept = [s for s in segments if len(s.strip()) >= min_chars]
    return sum(len(s) for s in kept)

# Load
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
DATA_DIR = os.environ.get("B3_KNOWLEDGE_SOURCE_DIR",
    "C:/Users/Umair.Alam/Desktop/kwikid_support_system/stackoverflow")

arts_db = {}
offset = 0
while True:
    r = sb.table("rag_knowledge_articles").select("article_id,knowledge_class").range(offset, offset+999).execute()
    for a in r.data:
        arts_db[a["article_id"]] = a
    if len(r.data) < 1000:
        break
    offset += 1000
out(f"DB articles: {len(arts_db)}")

with open(f"{DATA_DIR}/posts.json", "r", encoding="utf-8") as f:
    raw = json.load(f)
posts = raw.get("items", list(raw.values())[0]) if isinstance(raw, dict) else raw

with open(f"{DATA_DIR}/comments.json", "r", encoding="utf-8") as f:
    comments_raw = json.load(f)
comments_list = comments_raw.get("items", list(comments_raw.values())[0]) if isinstance(comments_raw, dict) else comments_raw

# Build lookup maps
ans_map = {}
for p in posts:
    if p.get("postType") == "answer":
        pid = p.get("parentId")
        if isinstance(pid, int):
            ans_map.setdefault(pid, []).append(p)

comment_map = {}
for c in comments_list:
    pid = c.get("postId")
    if isinstance(pid, int):
        comment_map.setdefault(pid, []).append(c)

questions = {p["id"]: p for p in posts if p.get("postType") == "question"}

qf = ChunkQualityFilter(ChunkQualityConfig(min_content_chars=30, min_alpha_chars=20, min_token_count=8))

total_src_old = 0
total_src_new = 0
total_old_sim = 0
total_new_sim = 0
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
    accepted_id = q.get("acceptedAnswerId")
    tags_list   = parse_tags(q)
    q_md        = _normalize_markdown(str(q.get("bodyMarkdown", "") or "")).strip()

    klass_str = arts_db[art_id].get("knowledge_class", "FAQ")
    try:
        klass = KnowledgeClass(klass_str)
    except ValueError:
        klass = KnowledgeClass.FAQ

    # ── OLD: best-answer-only, no question comments, top-3 answer comments ──
    valid = [a for a in ans_map.get(qid, []) if a.get("postState", "Published") != "Deleted"]
    best = next((a for a in valid if a.get("id") == accepted_id), None) if accepted_id else None
    if best is None and valid:
        best = max(valid, key=lambda a: (int(a.get("score", 0)), int(a.get("id", 0))))

    a_md_old = ""
    if best:
        a_md_old = _normalize_markdown(str(best.get("bodyMarkdown", "") or "")).strip()
        # OLD: top-3 comments on best answer only
        best_comments = sorted(comment_map.get(best.get("id", -1), []),
                                key=lambda c: int(c.get("score", 0)), reverse=True)[:3]
        comment_parts = []
        for c in best_comments:
            txt = _normalize_markdown(str(c.get("bodyMarkdown", "") or c.get("body", ""))).strip()
            if txt and len(txt) > 20:
                comment_parts.append(f"  - {txt}")
        if comment_parts:
            a_md_old += "\n\nRelated comments:\n" + "\n".join(comment_parts)

    total_src_old += len(q_md) + len(a_md_old)
    old_embed = _build_embed_text_raw(
        title=title,
        question_body=q_md,
        answer_body=a_md_old or None,
        knowledge_class=klass,
        tags_raw=tags_list,
        completeness_score=0.6,
        image_refs=[],
    )
    total_old_sim += simulate_old(old_embed, min_chars=100)

    # ── NEW: all answers sorted, question + all-answer comments ──
    q_md_new = q_md
    q_comment_text = format_comments(comment_map.get(qid, []))
    if q_comment_text:
        q_md_new = q_md_new + q_comment_text

    sorted_answers = sorted(
        valid,
        key=lambda a: (
            1 if (accepted_id is not None and a.get("id") == accepted_id) else 0,
            int(a.get("score", 0)),
            int(a.get("id", 0)),
        ),
        reverse=True,
    )

    answer_parts = []
    for i, ans in enumerate(sorted_answers):
        a_text = _normalize_markdown(str(ans.get("bodyMarkdown", "") or "")).strip()
        if not a_text:
            a_text = clean_text(str(ans.get("body", "") or "")).strip()
        if not a_text:
            continue
        cmt = format_comments(comment_map.get(ans.get("id", -1), []))
        if cmt:
            a_text += cmt
        if i == 0:
            answer_parts.append(a_text)
        else:
            score = int(ans.get("score", 0))
            hdr = f"Additional Answer (score={score}):" if score > 0 else "Additional Answer:"
            answer_parts.append(f"{hdr}\n{a_text}")

    a_md_new = "\n\n---\n\n".join(answer_parts) if answer_parts else None
    total_src_new += len(q_md_new) + len(a_md_new or "")

    new_embed = _build_embed_text_raw(
        title=title,
        question_body=q_md_new,
        answer_body=a_md_new,
        knowledge_class=klass,
        tags_raw=tags_list,
        completeness_score=0.6,
        image_refs=[],
    )

    chunker = KnowledgeChunker(
        knowledge_class=klass,
        chunk_target_tokens=1200,
        chunk_overlap_tokens=150,
        min_chunk_chars=100,
    )
    qf.reset_batch()
    chunks = chunker.chunk(new_embed, article_id=art_id, index_version="v2")
    code_chunks  = [c for c in chunks if c.chunk_type in ("CODE_SAMPLE", "COMMAND")]
    prose_chunks = [c for c in chunks if c.chunk_type not in ("CODE_SAMPLE", "COMMAND")]
    valid_prose  = [c for c in prose_chunks if qf.check(c.content).is_valid]
    all_valid    = valid_prose + code_chunks
    new_sim_chars = sum(len(c.content) for c in all_valid)
    total_new_sim += new_sim_chars

    old_sim_chars = simulate_old(old_embed, min_chars=100)
    if new_sim_chars > old_sim_chars + 50:
        articles_improved += 1
        improvements.append({
            "art_id":    art_id,
            "title":     title[:60],
            "old_chars": old_sim_chars,
            "new_chars": new_sim_chars,
            "gain":      new_sim_chars - old_sim_chars,
            "n_chunks":  len(all_valid),
            "n_extra":   max(0, len(sorted_answers) - 1),
        })
    else:
        articles_unchanged += 1

old_pct = total_old_sim / total_src_old * 100 if total_src_old else 0
new_pct = total_new_sim / total_src_new * 100 if total_src_new else 0

out("")
out("=" * 70)
out("PHASE 5 FINAL FIDELITY PROJECTION (all 3 fixes)")
out("=" * 70)
out(f"Articles analyzed:                    {articles_total}")
out(f"")
out(f"OLD source chars (best answer only):  {total_src_old:,}")
out(f"NEW source chars (all answers):       {total_src_new:,}")
out(f"Additional content from multi-answer: +{total_src_new - total_src_old:,} chars")
out(f"")
out(f"OLD simulated preserved chars: {total_old_sim:,}  ({old_pct:.1f}% of old source)")
out(f"NEW simulated preserved chars: {total_new_sim:,}  ({new_pct:.1f}% of new source)")
out(f"Net gain in stored chars:      +{total_new_sim - total_old_sim:,}")
out(f"")
out(f"Articles improved:   {articles_improved}/{articles_total}")
out(f"Articles unchanged:  {articles_unchanged}/{articles_total}")
out("")
out("TOP 25 most-improved articles (by absolute gain):")
improvements.sort(key=lambda x: -x["gain"])
hdr = f"{'ART_ID':<12} {'OLD':>7} {'NEW':>7} {'GAIN':>7} {'CHK':>4} {'XANS':>4}  TITLE"
out(hdr)
out("-" * 90)
for iss in improvements[:25]:
    line = (
        f"{iss['art_id']:<12} {iss['old_chars']:>7} {iss['new_chars']:>7} "
        f"+{iss['gain']:>6} {iss['n_chunks']:>4} {iss['n_extra']:>4}  {iss['title']}"
    )
    sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))

out("")
out("CONCLUSION:")
if new_pct >= 85:
    out(f"  Projected fidelity {new_pct:.1f}% >= 85% target. REBUILD AUTHORIZED.")
elif new_pct >= 75:
    out(f"  Projected fidelity {new_pct:.1f}%. Good improvement. Rebuild recommended.")
else:
    out(f"  Projected fidelity {new_pct:.1f}%. Further fixes needed before rebuild.")
