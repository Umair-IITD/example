"""Stage 2: Corpus Analysis — Production Rehearsal"""
import json
import re
from pathlib import Path
from collections import Counter

corpus_dir = Path(r"C:\Users\Umair.Alam\Desktop\kwikid_support_system\stackoverflow")

# ── Load posts ────────────────────────────────────────────────────────────────
with open(corpus_dir / "posts.json", encoding="utf-8") as f:
    posts = json.load(f)

questions = [p for p in posts if p.get("postType") == "question"]
answers   = [p for p in posts if p.get("postType") == "answer"]
deleted   = [p for p in posts if p.get("postType") == "question" and p.get("postState") == "Deleted"]

questions_with_accepted = [q for q in questions if q.get("acceptedAnswerId")]

# ── Load comments ─────────────────────────────────────────────────────────────
with open(corpus_dir / "comments.json", encoding="utf-8") as f:
    comments = json.load(f)

# ── Load images manifest ──────────────────────────────────────────────────────
with open(corpus_dir / "images.json", encoding="utf-8") as f:
    images_manifest = json.load(f)

# ── Check local PNGs ─────────────────────────────────────────────────────────
images_dir = corpus_dir / "images"
local_pngs = list(images_dir.glob("*.png")) if images_dir.exists() else []

print(f"Total posts:                   {len(posts)}")
print(f"Questions:                     {len(questions)}")
print(f"Answers:                       {len(answers)}")
print(f"Deleted questions:             {len(deleted)}")
print(f"Questions with accepted ans:   {len(questions_with_accepted)} ({100*len(questions_with_accepted)/max(len(questions),1):.1f}%)")
print(f"Total comments:                {len(comments)}")
print(f"Images in manifest:            {len(images_manifest)}")
print(f"Local PNG files:               {len(local_pngs)}")

# ── File sizes ────────────────────────────────────────────────────────────────
print("\nFile sizes:")
for fname in ["posts.json", "comments.json", "posts2votes.json", "tags.json", "images.json"]:
    p = corpus_dir / fname
    if p.exists():
        size_kb = p.stat().st_size / 1024
        print(f"  {fname}: {size_kb:.1f} KB")
    else:
        print(f"  {fname}: MISSING")

# ── Tag analysis ─────────────────────────────────────────────────────────────
all_tags = []
for q in questions:
    tags = q.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split("|") if t.strip()]
    all_tags.extend(tags)

tag_counts = Counter(all_tags)
print(f"\nUnique tags:  {len(tag_counts)}")
print("Top 20 tags:")
for tag, count in tag_counts.most_common(20):
    print(f"  {tag}: {count}")

# ── Score distribution ────────────────────────────────────────────────────────
scores = [q.get("score", 0) for q in questions]
print(f"\nQuestion score stats: min={min(scores)}, max={max(scores)}, avg={sum(scores)/len(scores):.2f}")
if answers:
    answer_scores = [a.get("score", 0) for a in answers]
    print(f"Answer score stats:   min={min(answer_scores)}, max={max(answer_scores)}, avg={sum(answer_scores)/len(answer_scores):.2f}")

# ── Question types ────────────────────────────────────────────────────────────
qa_pair     = [q for q in questions if q.get("acceptedAnswerId")]
no_answer   = [q for q in questions if q["id"] not in {a.get("parentId") for a in answers}]
print(f"\nQuestions with at least 1 answer: {len(questions) - len(no_answer)}")
print(f"Questions with NO answers:        {len(no_answer)}")

# ── Image URL analysis ────────────────────────────────────────────────────────
guid_pattern = re.compile(
    r"https?://stackoverflowteams\.com/c/[^/]+/images/s/"
    r"([0-9a-fA-F\-]{32,36})\.(png|jpg|jpeg|gif|webp)",
    flags=re.IGNORECASE,
)

all_guids_in_posts = set()
posts_with_images = []
for p in posts:
    body = p.get("bodyMarkdown", "") or p.get("body", "")
    guids = guid_pattern.findall(body)
    if guids:
        all_guids_in_posts.update(g[0] for g in guids)
        posts_with_images.append((p["id"], [g[0] for g in guids]))

# Build manifest index
manifest_by_guid_nodash = {}
manifest_by_guid_dashed = {}
for img in images_manifest:
    g = str(img.get("imageGuid", "")).strip().lower()
    g_nodash = g.replace("-", "")
    manifest_by_guid_nodash[g_nodash] = img
    manifest_by_guid_dashed[g] = img

# Verify linkage
linked = 0
broken = 0
for post_id, guids in posts_with_images:
    for guid in guids:
        g_nodash = guid.lower().replace("-", "")
        if g_nodash in manifest_by_guid_nodash or guid.lower() in manifest_by_guid_dashed:
            linked += 1
        else:
            broken += 1

# Check local files
local_png_stems = {f.stem.lower() for f in local_pngs}
with_local_file    = 0
without_local_file = 0
for img in images_manifest:
    g = str(img.get("imageGuid", "")).strip().lower().replace("-", "")
    if g in local_png_stems:
        with_local_file += 1
    else:
        without_local_file += 1

print(f"\nTotal unique image GUIDs in posts:   {len(all_guids_in_posts)}")
print(f"Posts referencing images:            {len(posts_with_images)}")
print(f"Linked (GUID in manifest):           {linked}")
print(f"Broken (GUID not in manifest):       {broken}")
print(f"Manifest images WITH local PNG:      {with_local_file}")
print(f"Manifest images WITHOUT local PNG:   {without_local_file}")

# ── Sample 5 posts with images ────────────────────────────────────────────────
post_lookup = {p["id"]: p for p in posts}
print("\nSAMPLE IMAGE LINKAGE (5 records):")
for post_id, guids in posts_with_images[:5]:
    post  = post_lookup.get(post_id, {})
    title = post.get("title", f"answer/parentId={post.get('parentId','?')}")
    for guid in guids[:2]:
        g_nodash = guid.lower().replace("-", "")
        manifest_entry = manifest_by_guid_nodash.get(g_nodash) or manifest_by_guid_dashed.get(guid.lower())
        local_file = corpus_dir / "images" / f"{g_nodash}.png"
        print(f"  Post {post_id} ({str(title)[:50]})")
        print(f"    GUID:       {guid}")
        print(f"    Manifest:   {'FOUND' if manifest_entry else 'MISSING'} {manifest_entry.get('fileName','') if manifest_entry else ''}")
        print(f"    Local PNG:  {'EXISTS' if local_file.exists() else 'NOT FOUND'}")
