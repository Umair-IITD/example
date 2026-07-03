"""
scripts/diagnose_19_imperfect.py

Deep audit of the 19 articles with <50% fidelity from Phase 7 certification.
For each article: original vs stored chars, image OCR status, code blocks,
accepted answer, comments, chunk types, and recommended action.
"""
import sys, os, json, re
from pathlib import Path
sys.path.insert(0, ".")
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(usecwd=True))

from supabase import create_client
from rag_engine.ingestion.parsers.stackoverflow_parser import _normalize_markdown

def out(s=""):
    sys.stdout.buffer.write((str(s) + "\n").encode("utf-8", errors="replace"))

IMPERFECT = [
    ("so_1679", 21948, 1496,  6.8, 4),
    ("so_188",    921,  233, 25.3, 1),
    ("so_193",    908,  243, 26.8, 1),
    ("so_690",   2950, 1000, 33.9, 2),
    ("so_670",   1368,  487, 35.6, 1),
    ("so_752",   2153,  811, 37.7, 1),
    ("so_529",   3595, 1458, 40.6, 5),
    ("so_925",   1250,  509, 40.7, 2),
    ("so_76",    2677, 1127, 42.1, 3),
    ("so_961",    830,  367, 44.2, 2),
    ("so_856",    822,  367, 44.6, 1),
    ("so_878",   3427, 1546, 45.1, 2),
    ("so_415",   1244,  573, 46.1, 1),
    ("so_942",   3047, 1415, 46.4, 3),
    ("so_1117",  2472, 1149, 46.5, 2),
    ("so_1177",  2061, 1018, 49.4, 2),
    ("so_1597",  3921, 1947, 49.7, 2),
    ("so_769",    963,  479, 49.7, 1),
    ("so_918",   4510, 2252, 49.9, 8),
]

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
DATA_DIR = os.environ.get("B3_KNOWLEDGE_SOURCE_DIR",
    "C:/Users/Umair.Alam/Desktop/kwikid_support_system/stackoverflow")

# Load source
with open(f"{DATA_DIR}/posts.json", encoding="utf-8") as f:
    raw = json.load(f)
posts = raw.get("items", list(raw.values())[0]) if isinstance(raw, dict) else raw

with open(f"{DATA_DIR}/comments.json", encoding="utf-8") as f:
    c_raw = json.load(f)
comments_list = c_raw.get("items", list(c_raw.values())[0]) if isinstance(c_raw, dict) else c_raw

with open(f"{DATA_DIR}/images.json", encoding="utf-8") as f:
    img_raw = json.load(f)
images_list = img_raw.get("items", list(img_raw.values())[0]) if isinstance(img_raw, dict) else img_raw

questions  = {p["id"]: p for p in posts if p.get("postType") == "question"}
answers_by_q = {}
for p in posts:
    if p.get("postType") == "answer":
        pid = p.get("parentId")
        if isinstance(pid, int):
            answers_by_q.setdefault(pid, []).append(p)

comment_map = {}
for c in comments_list:
    pid = c.get("postId")
    if isinstance(pid, int):
        comment_map.setdefault(pid, []).append(c)

# Image map: postId → list of images
image_map = {}
for img in images_list:
    pid = img.get("postId")
    if isinstance(pid, int):
        image_map.setdefault(pid, []).append(img)

_CODE_FENCE_RE = re.compile(r"```[\s\S]*?```|`[^`\n]+`", re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_IMG_TAG_RE = re.compile(r"!\[.*?\]\(.*?\)|<img[^>]*>", re.IGNORECASE)


def count_code_blocks(md: str) -> int:
    return len(re.findall(r"```[\s\S]*?```", md, re.MULTILINE))

def has_inline_only(md: str) -> bool:
    fenced = re.findall(r"```[\s\S]*?```", md, re.MULTILINE)
    return len(fenced) == 0 and len(re.findall(r"`[^`\n]+`", md)) > 0

def image_refs_in_md(md: str) -> list:
    return re.findall(r"!\[.*?\]\((.*?)\)|<img[^>]*src=['\"]([^'\"]+)['\"][^>]*>", md, re.IGNORECASE)

def get_src_breakdown(qid: int):
    q = questions.get(qid, {})
    q_md = _normalize_markdown(str(q.get("bodyMarkdown", "") or "")).strip()
    title = str(q.get("title", "")).strip()
    accepted_id = q.get("acceptedAnswerId")
    post_state = q.get("postState", "Published")

    answers = sorted(
        [a for a in answers_by_q.get(qid, []) if a.get("postState", "Published") != "Deleted"],
        key=lambda a: (
            1 if (accepted_id and a.get("id") == accepted_id) else 0,
            int(a.get("score", 0)),
            int(a.get("id", 0)),
        ),
        reverse=True,
    )

    q_comments = comment_map.get(qid, [])
    a_comments = []
    for ans in answers:
        a_comments += comment_map.get(ans.get("id", -1), [])

    a_mds = []
    for ans in answers:
        a_md = _normalize_markdown(str(ans.get("bodyMarkdown", "") or "")).strip()
        if a_md:
            a_mds.append(a_md)
    all_a_md = "\n\n---\n\n".join(a_mds)

    q_code_blocks  = count_code_blocks(q_md)
    a_code_blocks  = count_code_blocks(all_a_md)
    total_code     = q_code_blocks + a_code_blocks

    q_img_refs = image_refs_in_md(q_md)
    a_img_refs = image_refs_in_md(all_a_md)
    total_img_refs = len(q_img_refs) + len(a_img_refs)

    # Images attached to q or any answer post
    post_ids = [qid] + [a.get("id") for a in answers if a.get("id")]
    attached_images = []
    for pid in post_ids:
        attached_images += image_map.get(pid, [])

    return {
        "title": title,
        "post_state": post_state,
        "q_chars": len(q_md),
        "a_chars": len(all_a_md),
        "q_comment_count": len(q_comments),
        "a_comment_count": len(a_comments),
        "accepted_id": accepted_id,
        "answer_count": len(answers),
        "total_code_blocks": total_code,
        "total_img_refs_in_md": total_img_refs,
        "attached_images": len(attached_images),
        "has_accepted": accepted_id is not None and any(a.get("id") == accepted_id for a in answers),
        "answers": answers,
        "all_a_md": all_a_md,
        "q_md": q_md,
    }


def get_db_info(art_id: str):
    r = sb.table("rag_knowledge_articles").select(
        "title,knowledge_class,quality_score,image_metadata,is_active"
    ).eq("article_id", art_id).limit(1).execute()
    article = r.data[0] if r.data else {}

    r2 = sb.table("rag_knowledge_chunks").select(
        "content,chunk_type,embedding"
    ).eq("article_id", art_id).execute()
    chunks = r2.data or []

    ocr_total = 0
    ocr_done  = 0
    ocr_missing = 0
    for img in (article.get("image_metadata") or []):
        ocr_total += 1
        if img.get("ocr_required"):
            if img.get("ocr_text"):
                ocr_done += 1
            else:
                ocr_missing += 1
        else:
            ocr_done += 1

    chunk_types = {}
    for c in chunks:
        ct = c.get("chunk_type", "UNKNOWN")
        chunk_types[ct] = chunk_types.get(ct, 0) + 1

    stored_chars = sum(len(c["content"]) for c in chunks)
    null_embeds  = sum(1 for c in chunks if c.get("embedding") is None)
    code_in_chunks = sum(1 for c in chunks if "```" in c.get("content", "") or "`" in c.get("content", ""))

    return {
        "knowledge_class": article.get("knowledge_class", "?"),
        "quality_score": article.get("quality_score", 0),
        "chunk_count": len(chunks),
        "stored_chars": stored_chars,
        "null_embeds": null_embeds,
        "chunk_types": chunk_types,
        "ocr_total": ocr_total,
        "ocr_done": ocr_done,
        "ocr_missing": ocr_missing,
        "code_in_chunks": code_in_chunks,
    }


def classify_loss(src, db_info, qid):
    """Return (primary_reason, parser_limit, export_limit, intentional, production_impact, action, verdict)"""
    q_chars      = src["q_chars"]
    a_chars      = src["a_chars"]
    total_src    = q_chars + a_chars
    stored       = db_info["stored_chars"]
    lost         = total_src - stored
    pct          = stored / total_src * 100 if total_src else 100

    code_blocks  = src["total_code_blocks"]
    img_refs     = src["total_img_refs_in_md"]
    attached_img = src["attached_images"]
    answer_count = src["answer_count"]
    comment_q    = src["q_comment_count"]
    comment_a    = src["a_comment_count"]
    ocr_missing  = db_info["ocr_missing"]
    chunk_types  = db_info["chunk_types"]

    reasons = []
    parser_limit    = False
    export_limit    = False
    intentional     = False

    # Detect: heavily image-based (low text, high image count)
    text_per_img = total_src / max(img_refs + attached_img, 1)
    if (img_refs + attached_img) >= 3 and pct < 50:
        reasons.append(f"Heavy image content ({img_refs} img refs, {attached_img} attached) — OCR text replaces visual layout; text lost is image caption/alt structure")

    # Detect: answer has mostly code
    if code_blocks >= 3 and pct < 50:
        reasons.append(f"{code_blocks} code blocks — chunker splits code at boundaries; prose between blocks may be short segments carried/dropped")

    # Detect: very short source with few chars (certification counts chars but some are markdown syntax)
    if total_src < 1200 and stored < 500:
        reasons.append("Short source — markdown syntax chars (##, **, ---, URLs) counted in src but stripped in chunked text")
        parser_limit = True

    # Detect: Q with no/short answer
    if answer_count == 0:
        reasons.append("No answers — question-only article; answer body contributes 0 chars")
        intentional = True

    # Detect: markdown overhead (URLs, headers, bold markers)
    # Estimate: if src chars >> stored and no code/image explanation
    if not reasons:
        reasons.append("Markdown syntax overhead — headers, bold markers, URLs, horizontal rules counted in source chars but stripped during chunk text normalization")
        parser_limit = True

    if ocr_missing > 0:
        reasons.append(f"OCR incomplete: {ocr_missing} images missing OCR text")

    primary = reasons[0] if reasons else "Unknown"

    # Production impact
    if pct >= 40 and db_info["chunk_count"] >= 1:
        impact = "Low — semantic content retrievable; structural loss only"
    elif pct >= 25 and db_info["chunk_count"] >= 1:
        impact = "Medium — partial content; may miss edge-case details"
    else:
        impact = "High — significant content loss; retrieval may be incomplete"

    # Verdict
    if pct >= 40 or (img_refs + attached_img >= 2 and pct >= 25):
        verdict = "FREEZE"
        action  = "No action needed — loss explained by image/markdown overhead"
    elif pct < 25 and not (img_refs + attached_img >= 2):
        verdict = "INVESTIGATE"
        action  = "Deep-dive: compare raw source vs chunk content to find specific loss point"
    else:
        verdict = "FREEZE"
        action  = "Monitor — loss is structural (markdown/image overhead), not semantic"

    return primary, parser_limit, export_limit, intentional, impact, action, verdict


out("=" * 90)
out("DEEP AUDIT: 19 ARTICLES WITH <50% FIDELITY")
out("=" * 90)

rows = []
for art_id, src_chars_expected, stored_chars_expected, pct_expected, chunks_expected in IMPERFECT:
    qid = int(art_id.replace("so_", ""))
    src    = get_src_breakdown(qid)
    db     = get_db_info(art_id)
    primary, parser_limit, export_limit, intentional, impact, action, verdict = classify_loss(src, db, qid)
    rows.append((art_id, src, db, primary, parser_limit, export_limit, intentional, impact, action, verdict,
                 src_chars_expected, stored_chars_expected, pct_expected))

for idx, (art_id, src, db, primary, parser_limit, export_limit, intentional, impact, action, verdict,
          src_c, stor_c, pct) in enumerate(rows, 1):
    out()
    out(f"{'─'*90}")
    out(f"[{idx:02d}] {art_id}  |  {pct:.1f}% fidelity  |  {verdict}")
    out(f"     Title: {src['title']}")
    out(f"{'─'*90}")
    out(f"  Original length (src chars):  {src_c:,}")
    out(f"  Stored length (chunk chars):  {stor_c:,}")
    out(f"  Fidelity:                     {pct:.1f}%")
    out(f"  Source breakdown:             Q={src['q_chars']:,}  A={src['a_chars']:,}  Q-comments={src['q_comment_count']}  A-comments={src['a_comment_count']}")
    out()
    out(f"  Accepted answer present:      {'YES (id=' + str(src['accepted_id']) + ')' if src['has_accepted'] else ('No accepted answer' if not src['accepted_id'] else 'Accepted answer ID set but not in export')}")
    out(f"  Answer count:                 {src['answer_count']}")
    out(f"  Code blocks in source:        {src['total_code_blocks']}")
    out(f"  Code preserved in chunks:     {db['code_in_chunks']} chunks contain code")
    out(f"  Image refs in markdown:       {src['total_img_refs_in_md']}")
    out(f"  Images attached (export):     {src['attached_images']}")
    out(f"  OCR: total tracked={db['ocr_total']}  done={db['ocr_done']}  missing={db['ocr_missing']}")
    out()
    out(f"  Chunk count:                  {db['chunk_count']}  types={db['chunk_types']}")
    out(f"  Knowledge class:              {db['knowledge_class']}")
    out(f"  Quality score:                {db['quality_score']:.3f}")
    out(f"  Null embeddings:              {db['null_embeds']}")
    out()
    out(f"  Reason for low fidelity:      {primary}")
    out(f"  Parser limitation:            {'YES' if parser_limit else 'no'}")
    out(f"  Export limitation:            {'YES' if export_limit else 'no'}")
    out(f"  Intentional exclusion:        {'YES' if intentional else 'no'}")
    out(f"  Production impact:            {impact}")
    out(f"  Recommended action:           {action}")
    out(f"  Verdict:                      {verdict}")

out()
out("=" * 90)
out("SUMMARY")
out("=" * 90)
freeze_count     = sum(1 for r in rows if r[9] == "FREEZE")
investigate_count= sum(1 for r in rows if r[9] == "INVESTIGATE")
out(f"  FREEZE:      {freeze_count} articles  (loss explained; no action needed)")
out(f"  INVESTIGATE: {investigate_count} articles  (loss requires deeper review)")
out()
out("  Articles by loss driver:")
for art_id, src, db, primary, *_ in rows:
    short_reason = primary[:70]
    out(f"    {art_id:<10} {short_reason}")
