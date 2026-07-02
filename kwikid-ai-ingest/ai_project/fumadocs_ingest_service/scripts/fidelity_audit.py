"""
scripts/fidelity_audit.py

Phase 2-3: Source Fidelity Audit.
Compares every ingested article's stored chunks against the source export,
quantifying how much content was lost due to the min_chunk_chars=100 filter.

Usage:
    python scripts/fidelity_audit.py
"""
import sys, os, json, re
sys.path.insert(0, ".")
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(usecwd=True))

from supabase import create_client
from rag_engine.ingestion.parsers.stackoverflow_parser import (
    _normalize_markdown, StackOverflowParser
)
from rag_engine.ingestion.knowledge_pipeline import _build_embed_text_raw
from rag_engine.ingestion.knowledge_classifier import KnowledgeClass
from rag_engine.chunking.knowledge_chunker import (
    _FENCED_CODE_RE, _MIN_CODE_CHARS, _PARA_BREAK_RE
)

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
DATA_DIR = os.environ.get(
    "B3_KNOWLEDGE_SOURCE_DIR",
    "C:/Users/Umair.Alam/Desktop/kwikid_support_system/stackoverflow",
)

# ── Load source ────────────────────────────────────────────────────────────
with open(f"{DATA_DIR}/posts.json", "r", encoding="utf-8") as f:
    raw = json.load(f)
posts = raw.get("items", list(raw.values())[0]) if isinstance(raw, dict) else raw

# ── Load DB state ──────────────────────────────────────────────────────────
arts_db = {}
offset = 0
while True:
    r = sb.table("rag_knowledge_articles").select(
        "article_id,title,knowledge_class,quality_score"
    ).range(offset, offset + 999).execute()
    for a in r.data:
        arts_db[a["article_id"]] = a
    if len(r.data) < 1000:
        break
    offset += 1000

chunks_db: dict[str, list[dict]] = {}
offset = 0
while True:
    r = sb.table("rag_knowledge_chunks").select(
        "article_id,content,chunk_type"
    ).range(offset, offset + 999).execute()
    for c in r.data:
        chunks_db.setdefault(c["article_id"], []).append(c)
    if len(r.data) < 1000:
        break
    offset += 1000

# ── Build answer map ───────────────────────────────────────────────────────
ans_map: dict[int, list[dict]] = {}
for p in posts:
    if p.get("postType") == "answer":
        pid = p.get("parentId")
        if isinstance(pid, int):
            ans_map.setdefault(pid, []).append(p)

questions = {p["id"]: p for p in posts if p.get("postType") == "question"}

def count_code_blocks(md: str) -> int:
    return len(_FENCED_CODE_RE.findall(md))

def simulate_segments(embed_text: str, min_chars: int = 100):
    segments = []
    last_end = 0
    for match in _FENCED_CODE_RE.finditer(embed_text):
        start, end = match.span()
        if start > last_end:
            prose = embed_text[last_end:start]
            for para in _PARA_BREAK_RE.split(prose):
                para = para.strip()
                if para:
                    segments.append(("prose", para))
        cc = match.group(0)
        if len(cc.strip()) >= _MIN_CODE_CHARS:
            segments.append(("code", cc))
        else:
            segments.append(("prose", cc))
        last_end = end
    if last_end < len(embed_text):
        for para in _PARA_BREAK_RE.split(embed_text[last_end:]):
            para = para.strip()
            if para:
                segments.append(("prose", para))
    kept = [s for s in segments if len(s[1].strip()) >= min_chars]
    dropped = [s for s in segments if len(s[1].strip()) < min_chars]
    return kept, dropped

# ── Audit ─────────────────────────────────────────────────────────────────
issues = []
total_src = 0
total_stored = 0
total_src_code = 0
total_code_lost = 0
checked = 0

for qid, q in questions.items():
    art_id = f"so_{qid}"
    if art_id not in arts_db:
        continue
    checked += 1

    title = q.get("title", "")
    q_md = _normalize_markdown(str(q.get("bodyMarkdown", "") or "")).strip()

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
        a_md = _normalize_markdown(str(best.get("bodyMarkdown", "") or "")).strip()

    src_chars = len(q_md) + len(a_md)
    total_src += src_chars

    stored_chunks = chunks_db.get(art_id, [])
    stored_chars = sum(len(c["content"]) for c in stored_chunks)
    total_stored += stored_chars

    src_code = count_code_blocks(q_md) + count_code_blocks(a_md)
    db_code = sum(1 for c in stored_chunks if c["chunk_type"] in ("CODE_SAMPLE", "COMMAND"))
    total_src_code += src_code

    embed_text = _build_embed_text_raw(
        title=title,
        question_body=q_md,
        answer_body=a_md or None,
        knowledge_class=KnowledgeClass.VERIFIED_REPLY,
        tags_raw=[],
        completeness_score=0.5,
        image_refs=[],
    )
    kept, dropped = simulate_segments(embed_text, min_chars=100)
    dropped_chars = sum(len(s[1]) for s in dropped)
    kept_chars = sum(len(s[1]) for s in kept)
    code_dropped = sum(1 for s in dropped if "```" in s[1])
    total_code_lost += code_dropped

    total_candidate = dropped_chars + kept_chars
    pct_dropped = (dropped_chars / total_candidate * 100) if total_candidate > 0 else 0.0

    if pct_dropped > 30 or (src_code > 0 and db_code < src_code):
        issues.append({
            "art_id": art_id,
            "title": title[:70],
            "src_chars": src_chars,
            "stored_chars": stored_chars,
            "dropped_chars": dropped_chars,
            "pct_dropped": round(pct_dropped, 1),
            "src_code": src_code,
            "db_code": db_code,
            "code_dropped": code_dropped,
            "n_chunks": len(stored_chunks),
            "n_segments_kept": len(kept),
            "n_segments_dropped": len(dropped),
        })

print(f"Articles audited (ingested): {checked}")
print(f"Total source chars (Q+A markdown): {total_src:,}")
print(f"Total stored chars (all chunks):    {total_stored:,}")
pct = 100 * total_stored / total_src if total_src else 0
print(f"Overall preservation ratio:         {pct:.1f}%")
print(f"Source code blocks total:           {total_src_code}")
print(f"Code blocks dropped by min_chars:   {total_code_lost}")
print()
print(f"Articles with >30%% content dropped OR code blocks lost: {len(issues)}")
issues.sort(key=lambda x: -x["pct_dropped"])

print()
print("TOP 30 worst-fidelity articles:")
hdr = f"{'ART_ID':<12} {'%DROP':>6} {'SRC':>6} {'STOR':>6} {'CDROP':>5} {'DBNK':>4}  TITLE"
print(hdr)
print("-" * 110)
for iss in issues[:30]:
    line = (
        f"{iss['art_id']:<12} {iss['pct_dropped']:>5.1f}% "
        f"{iss['src_chars']:>6} {iss['stored_chars']:>6} "
        f"{iss['code_dropped']:>5} {iss['n_chunks']:>4}  {iss['title']}"
    )
    sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))

print()
print("Articles with ALL content dropped (stored=0 or stored<50):")
zero_stored = [iss for iss in issues if iss["stored_chars"] < 50]
for iss in zero_stored:
    sys.stdout.buffer.write(
        f"  {iss['art_id']} | src={iss['src_chars']} stored={iss['stored_chars']} | {iss['title']}\n"
        .encode("utf-8", errors="replace")
    )
