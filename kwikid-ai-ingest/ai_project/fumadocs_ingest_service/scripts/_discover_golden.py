"""Discovers representative articles for golden test fixtures. Delete after use."""
import os, sys, json, re
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(usecwd=True))
from supabase import create_client

def out(s=""):
    sys.stdout.buffer.write((s + "\n").encode("utf-8", errors="replace"))

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

# Load all articles
arts = {}
offset = 0
while True:
    r = sb.table("rag_knowledge_articles").select(
        "article_id,title,accepted_answer_id,answer_body,question_body,image_metadata,answer_post_id"
    ).range(offset, offset + 999).execute()
    for a in r.data:
        arts[a["article_id"]] = a
    if len(r.data) < 1000:
        break
    offset += 1000

# Load chunks
chunks_db = defaultdict(list)
offset = 0
while True:
    r = sb.table("rag_knowledge_chunks").select(
        "article_id,content,chunk_type,chunk_index"
    ).range(offset, offset + 999).execute()
    for c in r.data:
        chunks_db[c["article_id"]].append(c)
    if len(r.data) < 1000:
        break
    offset += 1000

out(f"Loaded {len(arts)} articles, {sum(len(v) for v in chunks_db.values())} chunks")
out()

# ── Category: Image-heavy (most images) ──────────────────────────────────────
ranked = sorted(arts.items(), key=lambda kv: len(kv[1].get("image_metadata") or []), reverse=True)
out("=== IMAGE-HEAVY (top 5 by image count) ===")
for aid, art in ranked[:5]:
    n_img = len(art.get("image_metadata") or [])
    has_ocr = sum(1 for m in (art.get("image_metadata") or []) if m.get("ocr_text"))
    q_snip = (art.get("question_body") or "")[:60].replace("\n", " ")
    out(f"  {aid} ({n_img} imgs, {has_ocr} with OCR): {art['title'][:70]}")
    out(f"    q_body preview: {q_snip}")
out()

# ── Category: OCR-heavy (most OCR images) ────────────────────────────────────
ranked_ocr = sorted(arts.items(), key=lambda kv: sum(1 for m in (kv[1].get("image_metadata") or []) if m.get("ocr_text")), reverse=True)
out("=== OCR-HEAVY (top 5 by OCR image count) ===")
for aid, art in ranked_ocr[:5]:
    has_ocr = sum(1 for m in (art.get("image_metadata") or []) if m.get("ocr_text"))
    # Show what OCR text looks like in chunks
    chunks = chunks_db[aid]
    all_content = " ".join(c["content"] for c in chunks)
    img_markers = re.findall(r"\[IMAGE: (.{0,60})\]", all_content)
    out(f"  {aid} ({has_ocr} OCR imgs): {art['title'][:70]}")
    if img_markers:
        out(f"    first [IMAGE: {img_markers[0][:60]}]")
out()

# ── Category: Code-heavy (most CODE_SAMPLE chunks) ───────────────────────────
code_counts = {aid: sum(1 for c in chunks if c["chunk_type"] in ("CODE_SAMPLE", "COMMAND")) for aid, chunks in chunks_db.items()}
ranked_code = sorted(code_counts.items(), key=lambda kv: kv[1], reverse=True)
out("=== CODE-HEAVY (top 5 by CODE_SAMPLE+COMMAND chunks) ===")
for aid, n_code in ranked_code[:5]:
    art = arts.get(aid, {})
    chunks = chunks_db[aid]
    code_chunks = [c for c in chunks if c["chunk_type"] in ("CODE_SAMPLE", "COMMAND")]
    code_preview = code_chunks[0]["content"][:100].replace("\n", " ") if code_chunks else ""
    out(f"  {aid} ({n_code} code chunks): {art.get('title', '?')[:70]}")
    out(f"    code preview: {code_preview}")
out()

# ── Category: SOP-heavy (articles with SOP-like content) ─────────────────────
out("=== SOP-HEAVY (articles mentioning SOP/procedure) ===")
sop_candidates = []
for aid, art in arts.items():
    q = (art.get("question_body") or "")
    a = (art.get("answer_body") or "")
    src = q + a
    chunks = chunks_db[aid]
    all_content = " ".join(c["content"] for c in chunks)
    if "SOP" in all_content or "procedure" in all_content.lower() or "step" in all_content.lower():
        n_steps = len(re.findall(r"(?m)^\s*\d+\.", src))
        sop_candidates.append((aid, n_steps, art.get("title", "")))
sop_candidates.sort(key=lambda x: -x[1])
for aid, n_steps, title in sop_candidates[:5]:
    out(f"  {aid} ({n_steps} numbered steps): {title[:70]}")
out()

# ── Category: Accepted-answer ─────────────────────────────────────────────────
out("=== ACCEPTED-ANSWER (with specific accepted_answer_id, distinctive answer) ===")
acc_candidates = [(aid, art) for aid, art in arts.items() if art.get("accepted_answer_id") is not None]
# Pick ones where answer_body has something distinctive
for aid, art in acc_candidates[:5]:
    a_body = (art.get("answer_body") or "")[:120].replace("\n", " ")
    out(f"  {aid} (acc_id={art['accepted_answer_id']}): {art.get('title', '')[:60]}")
    out(f"    answer preview: {a_body}")
out()

# ── Category: Multi-answer (multiple answers combined) ───────────────────────
out("=== MULTI-ANSWER (most --- separators in answer_body) ===")
multi_candidates = []
for aid, art in arts.items():
    a_body = art.get("answer_body") or ""
    n_sep = a_body.count("---")
    if n_sep >= 2:
        multi_candidates.append((aid, n_sep, art.get("title", "")))
multi_candidates.sort(key=lambda x: -x[1])
for aid, n_sep, title in multi_candidates[:5]:
    a_body = (arts[aid].get("answer_body") or "")[:120].replace("\n", " ")
    out(f"  {aid} ({n_sep} separators): {title[:60]}")
    out(f"    answer preview: {a_body}")
out()

# ── Category: Table-heavy (most | chars in content) ──────────────────────────
out("=== TABLE-HEAVY (most pipe chars in chunks) ===")
table_candidates = []
for aid, chunks in chunks_db.items():
    all_content = " ".join(c["content"] for c in chunks)
    pipe_count = all_content.count("|")
    if pipe_count >= 10:
        table_candidates.append((aid, pipe_count, arts.get(aid, {}).get("title", "")))
table_candidates.sort(key=lambda x: -x[1])
for aid, n_pipes, title in table_candidates[:5]:
    # Show a line with table content
    chunks = chunks_db[aid]
    all_content = " ".join(c["content"] for c in chunks)
    table_lines = [l for l in all_content.splitlines() if "|" in l]
    out(f"  {aid} ({n_pipes} pipes): {title[:60]}")
    if table_lines:
        out(f"    table line: {table_lines[0][:100]}")
out()

# ── Show specific content for known articles ─────────────────────────────────
out("=== SPECIFIC ARTICLES FROM SESSION CONTEXT ===")
known = ["so_623", "so_1055", "so_1585", "so_949", "so_1077"]
for aid in known:
    art = arts.get(aid)
    if not art:
        out(f"  {aid}: NOT IN DB")
        continue
    chunks = chunks_db.get(aid, [])
    all_content = " ".join(c["content"] for c in chunks)
    n_img = len(art.get("image_metadata") or [])
    code_chunks = [c for c in chunks if c["chunk_type"] in ("CODE_SAMPLE", "COMMAND")]
    img_markers = re.findall(r"\[IMAGE: (.{0,50})\]", all_content)
    out(f"  {aid}: {art.get('title', '')[:70]}")
    out(f"    images={n_img} code_chunks={len(code_chunks)} total_chunks={len(chunks)}")
    if img_markers:
        out(f"    OCR: [IMAGE: {img_markers[0][:50]}]")
    if code_chunks:
        out(f"    code: {code_chunks[0]['content'][:80].replace(chr(10), ' ')}")
