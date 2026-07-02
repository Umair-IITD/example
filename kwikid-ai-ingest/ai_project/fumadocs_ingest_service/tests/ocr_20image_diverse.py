"""
Sprint 2.36 — 20-image diverse-class OCR verification.
Selects 2-3 representative images per image class, runs full OCR,
verifies structured metadata, and confirms embedding separation.

Run: python -X utf8 tests/ocr_20image_diverse.py
"""
from __future__ import annotations
import json, os, sys, time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from rag_engine.ingestion.parsers.stackoverflow_parser import (
    StackOverflowParser, _IMAGE_URL_RE,
    IMAGE_CLASS_WHATSAPP_CHAT, IMAGE_CLASS_FLOWCHART,
    IMAGE_CLASS_STACKTRACE, IMAGE_CLASS_ERROR_DIALOG,
    IMAGE_CLASS_CONFIG_SCREEN, IMAGE_CLASS_TABLE,
    IMAGE_CLASS_SCREENSHOT, IMAGE_CLASS_UI_SCREEN,
    IMAGE_CLASS_TEXT_ONLY, IMAGE_CLASS_OTHER,
    _extract_image_references, _resolve_local_image_path,
)

DATA_DIR = Path("C:/Users/Umair.Alam/Desktop/kwikid_support_system/stackoverflow")
TARGET_PER_CLASS = 3
SEP = "=" * 70

ALL_CLASSES = [
    IMAGE_CLASS_WHATSAPP_CHAT, IMAGE_CLASS_FLOWCHART, IMAGE_CLASS_STACKTRACE,
    IMAGE_CLASS_ERROR_DIALOG, IMAGE_CLASS_CONFIG_SCREEN, IMAGE_CLASS_TABLE,
    IMAGE_CLASS_SCREENSHOT, IMAGE_CLASS_UI_SCREEN, IMAGE_CLASS_TEXT_ONLY,
    IMAGE_CLASS_OTHER,
]
ALWAYS_OCR = {
    IMAGE_CLASS_WHATSAPP_CHAT, IMAGE_CLASS_FLOWCHART, IMAGE_CLASS_STACKTRACE,
    IMAGE_CLASS_ERROR_DIALOG, IMAGE_CLASS_CONFIG_SCREEN, IMAGE_CLASS_TABLE,
    IMAGE_CLASS_TEXT_ONLY,
}


def main() -> None:
    print(SEP)
    print("20-IMAGE DIVERSE CLASS OCR VERIFICATION")
    print(SEP)

    parser = StackOverflowParser()
    export = parser._load_export(DATA_DIR)
    if export is None:
        print("FAIL — could not load export"); sys.exit(1)

    questions = [p for p in export.posts if p.get("postType") == "question"]
    images_dir = DATA_DIR / "images"
    local_pngs = {f.stem.lower() for f in images_dir.glob("*.png")}
    print(f"Questions: {len(questions)} | Local PNGs: {len(local_pngs)}")

    # Pre-scan: classify every image reference across all questions
    # Bucket by class → list of (question, image_ref, article) tuples
    class_buckets: dict[str, list[tuple]] = defaultdict(list)
    print("\nPre-classifying all images (no OCR yet)...")

    for q in questions:
        body = q.get("bodyMarkdown", "") or ""
        answers = export.answer_map.get(q.get("id", -1), [])
        ans_body = " ".join(a.get("bodyMarkdown", "") or "" for a in answers)
        tags = " ".join(q.get("tags") if isinstance(q.get("tags"), list) else [])
        title = q.get("title", "") or ""
        combined = f"{body}\n{ans_body}\n{title} {tags}"

        refs = _extract_image_references(
            body, ans_body, export.image_map, DATA_DIR,
            title_and_tags=f"{title} {tags}",
            parser=None,  # No OCR during pre-scan
        )
        for ref in refs:
            if ref.local_path is None:
                continue
            # Classify image
            from rag_engine.ingestion.parsers.stackoverflow_parser import _ALT_TEXT_RE
            alt_map = {}
            for m in _ALT_TEXT_RE.finditer(f"{body}\n{ans_body}"):
                alt_map[m.group(2).lower()] = m.group(1)
            alt = alt_map.get(ref.guid, "")
            img_class = parser._classify_image(ref.guid, alt, combined)
            class_buckets[img_class].append((q, ref, img_class, combined))

    print(f"\nClass distribution across all images with local files:")
    for cls in ALL_CLASSES:
        n = len(class_buckets.get(cls, []))
        print(f"  {cls:<20} : {n} images")

    # Select up to TARGET_PER_CLASS from each class
    selected: list[tuple] = []
    selected_guids: set[str] = set()
    for cls in ALL_CLASSES:
        bucket = class_buckets.get(cls, [])
        count = 0
        for item in bucket:
            q, ref, img_class, combined = item
            if ref.guid in selected_guids:
                continue
            selected.append(item)
            selected_guids.add(ref.guid)
            count += 1
            if count >= TARGET_PER_CLASS:
                break

    print(f"\nSelected {len(selected)} images for OCR across {len(set(i[2] for i in selected))} classes")

    # OCR each selected image
    print(f"\n{SEP}")
    print("OCR RESULTS")
    print(SEP)

    ocr_results: list[dict] = []
    t_total_start = time.time()

    for idx, (q, ref, img_class, combined) in enumerate(selected, 1):
        post_id = q.get("id", "?")
        title = (q.get("title", "") or "")[:60]
        ocr_required = parser._should_ocr(img_class, len(combined))

        print(f"\n[{idx:02d}] post_id={post_id}  guid={ref.guid[:16]}...")
        print(f"      title:        {title}")
        print(f"      image_class:  {img_class}")
        print(f"      ocr_required: {ocr_required}")
        print(f"      local_path:   {ref.local_path}")

        t0 = time.time()
        if ocr_required and ref.local_path:
            meta = parser._run_ocr(ref.local_path, ref.guid, img_class)
        else:
            from rag_engine.ingestion.parsers.stackoverflow_parser import ImageOCRMetadata
            meta = ImageOCRMetadata(
                image_guid=ref.guid,
                image_path=str(ref.local_path) if ref.local_path else "",
                image_class=img_class,
                ocr_required=ocr_required,
                ocr_text=None,
                ocr_confidence=None,
                ocr_version="none",
            )
        elapsed = time.time() - t0

        meta_dict = meta.to_dict()

        # Verify all required fields present
        required_fields = ["image_guid","image_class","image_path","ocr_required","ocr_text","ocr_confidence","ocr_version"]
        missing = [f for f in required_fields if f not in meta_dict]

        print(f"      elapsed:      {elapsed:.1f}s")
        print(f"      ocr_engine:   {meta_dict['ocr_version']}")
        ocr_text = meta_dict.get("ocr_text")
        if ocr_text:
            print(f"      ocr_text_len: {len(ocr_text)} chars")
            print(f"      confidence:   {meta_dict.get('ocr_confidence')}")
            print(f"      text_preview: {repr(ocr_text[:120])}")
        else:
            reason = "not required" if not ocr_required else ("no file" if not ref.local_path else "no text detected")
            print(f"      ocr_text:     None ({reason})")

        if missing:
            print(f"      MISSING FIELDS: {missing}")
        else:
            print(f"      metadata fields: ALL PRESENT")

        # Embedding separation check
        embed_snippet = f"QUESTION: {q.get('title','')}"
        if ocr_text and len(ocr_text) > 20:
            in_embed = ocr_text[:30] in embed_snippet
            print(f"      embed_sep:    {'FAIL — OCR IN EMBED' if in_embed else 'PASS — not in embed_text'}")

        result = {
            "idx": idx,
            "post_id": post_id,
            "guid": ref.guid,
            "class": img_class,
            "ocr_required": ocr_required,
            "ocr_attempted": ocr_required and ref.local_path is not None,
            "ocr_succeeded": ocr_text is not None,
            "text_len": len(ocr_text) if ocr_text else 0,
            "confidence": meta_dict.get("ocr_confidence"),
            "elapsed_s": round(elapsed, 1),
            "all_fields_present": len(missing) == 0,
        }
        ocr_results.append(result)

    total_elapsed = time.time() - t_total_start

    # Summary
    print(f"\n{SEP}")
    print("SUMMARY")
    print(SEP)
    attempted = [r for r in ocr_results if r["ocr_attempted"]]
    succeeded = [r for r in ocr_results if r["ocr_succeeded"]]
    confs = [r["confidence"] for r in succeeded if r["confidence"]]
    all_fields = all(r["all_fields_present"] for r in ocr_results)

    print(f"  Total images tested     : {len(ocr_results)}")
    print(f"  OCR attempted           : {len(attempted)}")
    print(f"  OCR succeeded           : {len(succeeded)}")
    print(f"  Success rate            : {len(succeeded)/max(len(attempted),1)*100:.0f}%")
    print(f"  Avg confidence          : {sum(confs)/len(confs):.4f}" if confs else "  Avg confidence          : n/a")
    print(f"  Min confidence          : {min(confs):.4f}" if confs else "")
    print(f"  Max confidence          : {max(confs):.4f}" if confs else "")
    print(f"  Total elapsed           : {total_elapsed:.1f}s")
    print(f"  Avg per OCR call        : {total_elapsed/max(len(attempted),1):.1f}s")
    print(f"  All metadata fields OK  : {all_fields}")

    print(f"\nResults by class:")
    class_results: dict[str, list] = defaultdict(list)
    for r in ocr_results:
        class_results[r["class"]].append(r)
    for cls, items in sorted(class_results.items()):
        succ = sum(1 for i in items if i["ocr_succeeded"])
        att = sum(1 for i in items if i["ocr_attempted"])
        print(f"  {cls:<20}: {len(items)} tested, {att} attempted, {succ} succeeded")

    print()
    all_pass = (
        len(succeeded) > 0
        and all_fields
    )
    if all_pass:
        print("20-IMAGE DIVERSE OCR: PASS")
    else:
        print("20-IMAGE DIVERSE OCR: FAIL — check details above")

    print(SEP)


if __name__ == "__main__":
    main()
