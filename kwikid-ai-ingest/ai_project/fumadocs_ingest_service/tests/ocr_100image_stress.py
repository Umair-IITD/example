"""
Sprint 2.36 — 100-image OCR stress test.
Selects 100 random local PNG files, runs _run_ocr on each,
measures throughput, memory, failure rate, and confidence distribution.

Run: python -X utf8 tests/ocr_100image_stress.py
"""
from __future__ import annotations
import os, sys, time, random, tracemalloc
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from rag_engine.ingestion.parsers.stackoverflow_parser import (
    StackOverflowParser, IMAGE_CLASS_SCREENSHOT,
)

DATA_DIR = Path("C:/Users/Umair.Alam/Desktop/kwikid_support_system/stackoverflow")
SAMPLE_SIZE = 100
RANDOM_SEED = 42
SEP = "=" * 70


def classify_failure(ocr_text, local_path: str) -> str:
    if ocr_text is not None:
        return "success"
    # Check file size to distinguish corrupt vs blank
    try:
        size = os.path.getsize(local_path)
        if size < 1000:
            return "corrupt_or_tiny"
        return "no_text_detected"
    except Exception:
        return "file_unreadable"


def main() -> None:
    print(SEP)
    print("100-IMAGE OCR STRESS TEST")
    print(SEP)

    images_dir = DATA_DIR / "images"
    all_pngs = list(images_dir.glob("*.png"))
    print(f"Total local PNGs available: {len(all_pngs)}")

    random.seed(RANDOM_SEED)
    sample = random.sample(all_pngs, min(SAMPLE_SIZE, len(all_pngs)))
    print(f"Sample selected: {len(sample)} images (seed={RANDOM_SEED})")

    parser = StackOverflowParser()

    # Warm up the engine with first image to isolate init time
    print(f"\nWarming up OCR engine (first image)...")
    t_init = time.time()
    _ = parser._run_ocr(str(sample[0]), "warmup", IMAGE_CLASS_SCREENSHOT)
    init_elapsed = time.time() - t_init
    print(f"Engine init + first inference: {init_elapsed:.1f}s")
    print(f"Engine instance ID: {id(StackOverflowParser._ocr_engine)}")

    # Track memory
    tracemalloc.start()

    print(f"\nRunning OCR on remaining {len(sample)-1} images...")
    print(f"{'#':<5} {'GUID[:12]':<14} {'result':<18} {'conf':<7} {'len':<6} {'elapsed':<8}")
    print("-" * 65)

    results = []
    t_batch_start = time.time()

    for i, png_path in enumerate(sample, 1):
        guid = png_path.stem[:16]
        t0 = time.time()
        try:
            meta = parser._run_ocr(str(png_path), png_path.stem, IMAGE_CLASS_SCREENSHOT)
            ocr_text = meta.ocr_text
            conf = meta.ocr_confidence
            failure_class = classify_failure(ocr_text, str(png_path))
        except Exception as e:
            ocr_text = None
            conf = None
            failure_class = f"exception: {type(e).__name__}"
        elapsed = time.time() - t0

        result = {
            "i": i,
            "guid": png_path.stem,
            "path": str(png_path),
            "ocr_text": ocr_text,
            "confidence": conf,
            "elapsed_s": elapsed,
            "failure_class": failure_class,
            "text_len": len(ocr_text) if ocr_text else 0,
        }
        results.append(result)

        status = failure_class if failure_class != "success" else f"ok ({len(ocr_text or '')} chars)"
        conf_str = f"{conf:.4f}" if conf else "n/a"
        if i % 10 == 0 or i <= 5 or failure_class != "success":
            print(f"{i:<5} {guid:<14} {status:<18} {conf_str:<7} {result['text_len']:<6} {elapsed:.1f}s")

    total_elapsed = time.time() - t_batch_start
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # Analysis
    successes = [r for r in results if r["failure_class"] == "success"]
    failures = [r for r in results if r["failure_class"] != "success"]
    failure_classes = Counter(r["failure_class"] for r in failures)
    confs = [r["confidence"] for r in successes if r["confidence"]]
    text_lens = [r["text_len"] for r in successes]
    elapsed_all = [r["elapsed_s"] for r in results[1:]]  # skip warmup image

    print(f"\n{SEP}")
    print("STRESS TEST SUMMARY")
    print(SEP)
    print(f"  Images tested           : {len(results)}")
    print(f"  OCR succeeded           : {len(successes)}")
    print(f"  OCR failed / no-text    : {len(failures)}")
    print(f"  Success rate            : {len(successes)/len(results)*100:.1f}%")
    print()
    print(f"  Total time (excl. warmup): {total_elapsed:.1f}s")
    print(f"  Engine init time        : {init_elapsed:.1f}s")
    print(f"  Avg per image (excl. warmup): {sum(elapsed_all)/len(elapsed_all):.2f}s" if elapsed_all else "")
    print(f"  Min / Max per image     : {min(elapsed_all):.1f}s / {max(elapsed_all):.1f}s" if elapsed_all else "")
    print(f"  Peak memory (MB)        : {peak/1_048_576:.1f}")
    print()
    if confs:
        print(f"  Confidence min          : {min(confs):.4f}")
        print(f"  Confidence avg          : {sum(confs)/len(confs):.4f}")
        print(f"  Confidence max          : {max(confs):.4f}")
        above90 = sum(1 for c in confs if c >= 0.90)
        above80 = sum(1 for c in confs if c >= 0.80)
        print(f"  Confidence >= 0.90      : {above90}/{len(confs)} ({above90/len(confs)*100:.0f}%)")
        print(f"  Confidence >= 0.80      : {above80}/{len(confs)} ({above80/len(confs)*100:.0f}%)")
    print()
    if text_lens:
        print(f"  OCR text length min     : {min(text_lens)} chars")
        print(f"  OCR text length avg     : {sum(text_lens)/len(text_lens):.0f} chars")
        print(f"  OCR text length max     : {max(text_lens)} chars")
    print()
    print(f"  Failure breakdown:")
    if failure_classes:
        for fc, cnt in failure_classes.most_common():
            print(f"    {fc:<30}: {cnt}")
    else:
        print(f"    (none)")

    print()
    extrapolate_count = 1460  # estimated full corpus OCR calls
    avg_t = sum(elapsed_all)/len(elapsed_all) if elapsed_all else 12.0
    est_total = (init_elapsed + extrapolate_count * avg_t) / 3600
    print(f"  Projected full corpus ({extrapolate_count} imgs): {est_total:.1f}h")

    print()
    if len(successes) >= 80:
        print("100-IMAGE STRESS TEST: PASS (>= 80% success rate)")
    else:
        print(f"100-IMAGE STRESS TEST: FAIL ({len(successes)/len(results)*100:.0f}% < 80%)")
    print(SEP)


if __name__ == "__main__":
    main()
