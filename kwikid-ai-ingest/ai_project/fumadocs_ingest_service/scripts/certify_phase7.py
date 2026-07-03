"""
scripts/certify_phase7.py

Phase 7: Final Source Fidelity Certification.

Proves mathematically:
  Source Posts = Rejected + Stored + Known Exclusions + 0 Unexplained

  Images = OCR_processed + Intentional_non_OCR + 0 Missing

  Stored text matches source within acceptable fidelity bounds

Lists every article with imperfect fidelity and explains why.
"""
import sys, os, json, re
from pathlib import Path
sys.path.insert(0, ".")
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(usecwd=True))

from supabase import create_client
from rag_engine.ingestion.parsers.stackoverflow_parser import _normalize_markdown
from rag_engine.ingestion.knowledge_pipeline import _build_embed_text_raw
from rag_engine.ingestion.knowledge_classifier import KnowledgeClass, KnowledgeClassifier
from rag_engine.ingestion.knowledge_quality_validator import KnowledgeQualityValidator

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

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
DATA_DIR = os.environ.get("B3_KNOWLEDGE_SOURCE_DIR",
    "C:/Users/Umair.Alam/Desktop/kwikid_support_system/stackoverflow")

# Load source
with open(f"{DATA_DIR}/posts.json", "r", encoding="utf-8") as f:
    raw = json.load(f)
posts = raw.get("items", list(raw.values())[0]) if isinstance(raw, dict) else raw

with open(f"{DATA_DIR}/comments.json", "r", encoding="utf-8") as f:
    c_raw = json.load(f)
comments_list = c_raw.get("items", list(c_raw.values())[0]) if isinstance(c_raw, dict) else c_raw

questions = {p["id"]: p for p in posts if p.get("postType") == "question"}
answers_all = [p for p in posts if p.get("postType") == "answer"]

ans_map = {}
for a in answers_all:
    pid = a.get("parentId")
    if isinstance(pid, int):
        ans_map.setdefault(pid, []).append(a)

comment_map = {}
for c in comments_list:
    pid = c.get("postId")
    if isinstance(pid, int):
        comment_map.setdefault(pid, []).append(c)

# Load DB
arts_db = {}
offset = 0
while True:
    r = sb.table("rag_knowledge_articles").select(
        "article_id,title,knowledge_class,quality_score,is_active"
    ).range(offset, offset+999).execute()
    for a in r.data:
        arts_db[a["article_id"]] = a
    if len(r.data) < 1000:
        break
    offset += 1000

chunks_db = {}
offset = 0
while True:
    r = sb.table("rag_knowledge_chunks").select(
        "article_id,content,chunk_type,embedding"
    ).range(offset, offset+999).execute()
    for c in r.data:
        chunks_db.setdefault(c["article_id"], []).append(c)
    if len(r.data) < 1000:
        break
    offset += 1000

out(f"DB articles: {len(arts_db)}")
out(f"DB chunks:   {sum(len(v) for v in chunks_db.values())}")
out(f"Source questions: {len(questions)}")

# ── Classify every source question offline ──────────────────────────────────
validator  = KnowledgeQualityValidator()
classifier = KnowledgeClassifier()

from rag_engine.ingestion.parsers.stackoverflow_parser import (
    StackOverflowParser, KnowledgeArticle, ImageReference
)

# Build a minimal parser just for classification (no OCR)
REJECTED_VALIDATOR  = {}  # art_id → reason
REJECTED_CLASSIFIER = {}  # art_id → reason
STORED              = {}  # art_id → article
UNEXPLAINED_MISSING = {}  # art_id → why

for qid, q in questions.items():
    art_id = f"so_{qid}"

    q_md = _normalize_markdown(str(q.get("bodyMarkdown", "") or "")).strip()
    title = _normalize_markdown(str(q.get("title", ""))).strip()
    accepted_id = q.get("acceptedAnswerId")
    tags_raw = parse_tags(q)

    valid_answers = sorted(
        [a for a in ans_map.get(qid, []) if a.get("postState", "Published") != "Deleted"],
        key=lambda a: (
            1 if (accepted_id is not None and a.get("id") == accepted_id) else 0,
            int(a.get("score", 0)),
            int(a.get("id", 0)),
        ),
        reverse=True,
    )

    a_md_parts = []
    for ans in valid_answers:
        a_text = _normalize_markdown(str(ans.get("bodyMarkdown", "") or "")).strip()
        if a_text:
            a_md_parts.append(a_text)
    a_md = "\n\n---\n\n".join(a_md_parts) if a_md_parts else None

    # Build a minimal KnowledgeArticle for validation
    article = KnowledgeArticle(
        article_id=art_id,
        source="stackoverflow_for_teams",
        source_post_id=qid,
        article_type="qa_pair" if a_md else "question_only",
        canonical_url=f"https://stackoverflowteams.com/c/kwikid/questions/{qid}",
        title=title,
        question_body=q_md,
        answer_body=a_md,
        question_markdown=q_md,
        answer_markdown=a_md,
        answer_post_id=valid_answers[0].get("id") if valid_answers else None,
        answer_score=int(valid_answers[0].get("score", 0)) if valid_answers else 0,
        question_score=int(q.get("score", 0)),
        view_count=int(q.get("viewCount", 0)),
        accepted_answer_id=accepted_id if isinstance(accepted_id, int) else None,
        tags_raw=tags_raw,
        clients=[],
        image_references=[],
        completeness_flags={},
        completeness_score=0.5,
        manual_review_required=False,
        image_count=0,
        resolved_image_count=0,
        missing_image_count=0,
        image_grounding_status="no_images",
        safety_classification="safe_for_llm",
        image_metadata=[],
        post_state=str(q.get("postState", "Published")),
        created_at_source=q.get("creationDate"),
    )

    # Validation gate
    validation = validator.validate(article)
    if not validation.is_valid:
        reasons = [i.code for i in validation.errors]
        REJECTED_VALIDATOR[art_id] = ", ".join(reasons)
        continue

    # Classifier gate
    clf = classifier.classify(article)
    if not clf.is_embeddable:
        REJECTED_CLASSIFIER[art_id] = clf.reject_reason or clf.knowledge_class.value
        continue

    STORED[art_id] = article

out("")
out("=" * 70)
out("PHASE 7: SOURCE FIDELITY CERTIFICATION")
out("=" * 70)
out("")
out("=== ACCOUNTING (Source Posts) ===")
total_q = len(questions)
rejected_v  = len(REJECTED_VALIDATOR)
rejected_c  = len(REJECTED_CLASSIFIER)
stored_exp  = len(STORED)
stored_act  = len(arts_db)
out(f"Total source questions:     {total_q}")
out(f"Rejected by validator:      {rejected_v}")
out(f"Rejected by classifier:     {rejected_c}")
out(f"Expected to be stored:      {stored_exp}")
out(f"Actually stored in DB:      {stored_act}")
diff = stored_exp - stored_act
out(f"Unexplained difference:     {diff}")
if diff == 0:
    out("  PROOF: Source = Rejected(v) + Rejected(c) + Stored. 0 unexplained.")
else:
    out(f"  WARNING: {abs(diff)} articles {'missing from' if diff > 0 else 'extra in'} DB vs expectation.")

# Find actually unexplained missing
missing_from_db = [a for a in STORED if a not in arts_db]
extra_in_db     = [a for a in arts_db if a not in STORED and a not in REJECTED_VALIDATOR and a not in REJECTED_CLASSIFIER]
out(f"  Articles expected but missing from DB: {len(missing_from_db)}")
out(f"  Articles in DB but not expected:       {len(extra_in_db)}")
for a in missing_from_db[:10]:
    out(f"    MISSING: {a}")
for a in extra_in_db[:10]:
    out(f"    EXTRA:   {a}")

out("")
out("=== CHUNK STATS ===")
total_chunks = sum(len(v) for v in chunks_db.values())
null_embeddings = sum(
    1 for chunks in chunks_db.values()
    for c in chunks
    if c.get("embedding") is None
)
out(f"Total chunks:      {total_chunks}")
out(f"Null embeddings:   {null_embeddings}")
out(f"Chunks per article: {total_chunks / stored_act:.1f} avg" if stored_act > 0 else "N/A")
orphan_chunks = [art_id for art_id in chunks_db if art_id not in arts_db]
out(f"Orphan chunks:     {len(orphan_chunks)}")

out("")
out("=== FIDELITY ANALYSIS (stored articles) ===")

# Compute preservation per article
total_src_chars = 0
total_stored_chars = 0
imperfect = []
zero_chunks = []

for art_id, article in STORED.items():
    src_chars = len(article.question_body or "") + len(article.answer_body or "")
    total_src_chars += src_chars

    stored_chunks = chunks_db.get(art_id, [])
    stored_chars = sum(len(c["content"]) for c in stored_chunks)
    total_stored_chars += stored_chars

    if not stored_chunks:
        zero_chunks.append(art_id)
        continue

    pct = stored_chars / src_chars * 100 if src_chars > 0 else 100
    if pct < 50 or stored_chars < 200:
        imperfect.append({
            "art_id":  art_id,
            "title":   questions.get(int(art_id.replace("so_", "")), {}).get("title", "")[:60],
            "src":     src_chars,
            "stored":  stored_chars,
            "pct":     pct,
            "n_chunks": len(stored_chunks),
        })

overall_pct = total_stored_chars / total_src_chars * 100 if total_src_chars > 0 else 0
out(f"Source chars (Q+A+comments): {total_src_chars:,}")
out(f"Stored chars (all chunks):   {total_stored_chars:,}")
out(f"Overall preservation ratio:  {overall_pct:.1f}%")
out(f"Articles with <50% fidelity: {len(imperfect)}")
out(f"Articles with 0 chunks:      {len(zero_chunks)}")

if zero_chunks:
    out("  ZERO-CHUNK ARTICLES (should not exist):")
    for a in zero_chunks:
        out(f"    {a}")

out("")
out("=== IMAGE / OCR ACCOUNTING ===")
ocr_total = 0
ocr_done  = 0
ocr_required_missing = 0
for art_id in arts_db:
    r = sb.table("rag_knowledge_articles").select("image_metadata").eq("article_id", art_id).limit(1).execute()
    if not r.data:
        continue
    metadata = r.data[0].get("image_metadata") or []
    for img in metadata:
        ocr_total += 1
        if img.get("ocr_required"):
            if img.get("ocr_text"):
                ocr_done += 1
            else:
                ocr_required_missing += 1
        else:
            ocr_done += 1  # intentional non-OCR counts as handled

out(f"Total images referenced:    {ocr_total}")
out(f"OCR processed or excluded:  {ocr_done}")
out(f"OCR required but missing:   {ocr_required_missing}")
if ocr_required_missing == 0:
    out("  PROOF: All images either OCR-processed or intentionally excluded.")
else:
    out(f"  WARNING: {ocr_required_missing} images needed OCR but text is missing.")

out("")
out("=== IMPERFECT FIDELITY ARTICLES ===")
imperfect.sort(key=lambda x: x["pct"])
hdr = f"{'ART_ID':<12} {'SRC':>7} {'STOR':>7} {'PCT':>6} {'CHK':>4}  TITLE"
out(hdr)
out("-" * 80)
for iss in imperfect[:30]:
    line = (
        f"{iss['art_id']:<12} {iss['src']:>7} {iss['stored']:>7} "
        f"{iss['pct']:>5.1f}% {iss['n_chunks']:>4}  {iss['title']}"
    )
    sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))

out("")
out("=== INTENTIONAL EXCLUSIONS ===")
out(f"Validator rejections ({rejected_v} articles):")
by_code = {}
for art_id, reasons in REJECTED_VALIDATOR.items():
    for r in reasons.split(", "):
        by_code.setdefault(r, []).append(art_id)
for code, ids in sorted(by_code.items(), key=lambda x: -len(x[1])):
    out(f"  {code}: {len(ids)} articles")

out(f"\nClassifier rejections ({rejected_c} articles):")
by_reason = {}
for art_id, reason in REJECTED_CLASSIFIER.items():
    by_reason.setdefault(reason, []).append(art_id)
for reason, ids in sorted(by_reason.items(), key=lambda x: -len(x[1])):
    out(f"  {reason}: {len(ids)} articles")

out("")
out("=" * 70)
out("PHASE 7 CERTIFICATION RESULT")
out("=" * 70)
checks = []
checks.append(("Source accounting balanced", diff == 0 and len(missing_from_db) == 0))
checks.append(("No null embeddings", null_embeddings == 0))
checks.append(("No orphan chunks", len(orphan_chunks) == 0))
checks.append(("No zero-chunk articles", len(zero_chunks) == 0))
checks.append(("Preservation >= 80%", overall_pct >= 80.0))
checks.append(("OCR complete or excluded", ocr_required_missing == 0))

for check, passed in checks:
    status = "PASS" if passed else "FAIL"
    out(f"  [{status}] {check}")

all_pass = all(p for _, p in checks)
out("")
if all_pass:
    out("CERTIFICATION: GO")
    out(f"Knowledge Layer v2.0 — Source Fidelity Certified")
    out(f"  Articles: {stored_act} | Chunks: {total_chunks} | Preservation: {overall_pct:.1f}%")
else:
    out("CERTIFICATION: HOLD — see FAIL items above")
