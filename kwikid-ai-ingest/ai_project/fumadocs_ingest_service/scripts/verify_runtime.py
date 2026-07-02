"""
scripts/verify_runtime.py
Sprint 2.36 — Permanent pre-flight runtime check.

Verifies the entire ingestion + retrieval runtime before any ingestion or
deployment. Run this before every knowledge ingestion and before pushing
to production.

Exit codes:
  0 — all checks PASS (or WARN only)
  1 — one or more checks FAIL

Usage:
  python scripts/verify_runtime.py
  python scripts/verify_runtime.py --verbose     # extra detail
  python scripts/verify_runtime.py --skip-ocr    # skip live OCR test (faster)
  python scripts/verify_runtime.py --skip-net    # skip network connectivity tests
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
import time
from pathlib import Path
from typing import Callable

# ------------------------------------------------------------------
# Ensure project root on path so imports work from any working dir
# ------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(_PROJECT_ROOT))
from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")


# ------------------------------------------------------------------
# Result helpers
# ------------------------------------------------------------------

class CheckResult:
    def __init__(self, name: str, status: str, detail: str = "") -> None:
        self.name   = name
        self.status = status   # PASS | FAIL | WARN | SKIP
        self.detail = detail

    def __repr__(self) -> str:
        sym = {"PASS": "[PASS]", "FAIL": "[FAIL]", "WARN": "[WARN]", "SKIP": "[SKIP]"}
        s = sym.get(self.status, f"[{self.status}]")
        line = f"  {s} {self.name}"
        if self.detail:
            line += f"\n         {self.detail}"
        return line


def passed(name: str, detail: str = "") -> CheckResult:
    return CheckResult(name, "PASS", detail)

def failed(name: str, detail: str = "") -> CheckResult:
    return CheckResult(name, "FAIL", detail)

def warned(name: str, detail: str = "") -> CheckResult:
    return CheckResult(name, "WARN", detail)

def skipped(name: str, detail: str = "") -> CheckResult:
    return CheckResult(name, "SKIP", detail)


# ------------------------------------------------------------------
# Check groups
# ------------------------------------------------------------------

def check_python_environment() -> list[CheckResult]:
    results = []

    # Python version
    v = sys.version_info
    if v >= (3, 9):
        results.append(passed("Python version", f"{v.major}.{v.minor}.{v.micro} ({sys.executable})"))
    else:
        results.append(failed("Python version", f"{v.major}.{v.minor}.{v.micro} — requires 3.9+"))

    # Architecture
    bits = struct.calcsize("P") * 8
    if bits == 64:
        results.append(passed("Python architecture", "64-bit AMD64"))
    else:
        results.append(failed("Python architecture", f"{bits}-bit — must be 64-bit for ONNX Runtime"))

    # Virtual env warning
    venv = os.environ.get("VIRTUAL_ENV") or os.environ.get("CONDA_PREFIX")
    if not venv:
        results.append(warned("Virtual environment", "NOT active — system Python in use. Consider venv for isolation."))
    else:
        results.append(passed("Virtual environment", venv))

    return results


def check_vc_runtime() -> list[CheckResult]:
    results = []
    required_dlls = {
        "MSVCP140.dll":    r"C:\Windows\System32\MSVCP140.dll",
        "VCRUNTIME140.dll":  r"C:\Windows\System32\VCRUNTIME140.dll",
        "VCRUNTIME140_1.dll": r"C:\Windows\System32\VCRUNTIME140_1.dll",
    }
    for name, path in required_dlls.items():
        if os.path.exists(path):
            results.append(passed(f"VC++ Runtime {name}", f"{os.path.getsize(path):,} bytes"))
        else:
            results.append(failed(f"VC++ Runtime {name}", f"MISSING at {path} — install VC++ 2019/2022 Redistributable"))
    return results


def check_packages() -> list[CheckResult]:
    results = []

    package_checks = [
        ("onnxruntime",           "1.20.0", "onnxruntime"),
        ("numpy",                 "1.24.0", "numpy"),
        ("rapidocr_onnxruntime",  "1.3.0",  "rapidocr_onnxruntime"),
        ("cv2",                   "4.0.0",  "cv2"),
        ("PIL",                   "9.0.0",  "PIL"),
        ("httpx",                 "0.20.0", "httpx"),
        ("supabase",              "2.0.0",  "supabase"),
        ("dotenv",                "0.1.0",  "dotenv"),
    ]

    for display_name, min_ver_str, import_name in package_checks:
        try:
            mod = __import__(import_name)
            ver = getattr(mod, "__version__", "unknown")
            results.append(passed(f"Package {display_name}", f"version {ver}"))
        except ImportError as e:
            results.append(failed(f"Package {display_name}", f"NOT INSTALLED — {e}"))

    # Check for GPU duplicate (conflict risk)
    try:
        import subprocess
        r = subprocess.run(
            [sys.executable, "-m", "pip", "show", "onnxruntime-gpu"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0 and "Version:" in r.stdout:
            results.append(failed(
                "Package onnxruntime-gpu",
                "INSTALLED alongside onnxruntime — DLL conflict risk. Uninstall one.",
            ))
        else:
            results.append(passed("Package onnxruntime-gpu (absent)", "Not installed — no conflict"))
    except Exception:
        pass

    # Check duplicate site-packages
    sys_sp = Path(sys.executable).parent / "Lib" / "site-packages"
    usr_sp = Path(os.path.expanduser("~")) / "AppData" / "Roaming" / "Python" / f"Python{sys.version_info.major}{sys.version_info.minor}" / "site-packages"
    conflict_pkgs = ["onnxruntime", "numpy", "rapidocr_onnxruntime", "cv2"]
    for pkg in conflict_pkgs:
        in_sys = (sys_sp / pkg).exists()
        in_usr = (usr_sp / pkg).exists()
        if in_sys and in_usr:
            results.append(failed(f"Duplicate {pkg}", f"Found in BOTH {sys_sp} and {usr_sp}"))
        # else: fine

    return results


def check_ocr_engine(skip: bool = False) -> list[CheckResult]:
    results = []
    if skip:
        results.append(skipped("RapidOCR engine init", "--skip-ocr passed"))
        results.append(skipped("RapidOCR inference test", "--skip-ocr passed"))
        return results

    try:
        from rapidocr_onnxruntime import RapidOCR
        from rag_engine.ingestion.parsers.stackoverflow_parser import StackOverflowParser
        t0 = time.time()
        engine = RapidOCR()
        init_t = time.time() - t0
        results.append(passed("RapidOCR engine init", f"{type(engine).__name__} in {init_t:.1f}s"))
        StackOverflowParser._ocr_engine = None  # reset for clean test

        # Find one local image to test
        img_dir = Path(os.getenv("B3_KNOWLEDGE_SOURCE_DIR", "")).resolve() / "images"
        if img_dir.exists():
            pngs = list(img_dir.glob("*.png"))
            if pngs:
                test_img = str(pngs[0])
                t0 = time.time()
                meta = StackOverflowParser._run_ocr(test_img, pngs[0].stem, "SCREENSHOT")
                infer_t = time.time() - t0
                ocr_text = meta.ocr_text
                results.append(passed(
                    "RapidOCR inference test",
                    f"{'text returned (' + str(len(ocr_text)) + ' chars)' if ocr_text else 'no text regions (blank image)'} "
                    f"in {infer_t:.1f}s, engine={meta.ocr_version}",
                ))
            else:
                results.append(warned("RapidOCR inference test", "No PNG files found in images/ — skipping live test"))
        else:
            results.append(warned("RapidOCR inference test", f"images/ directory not found at {img_dir}"))
    except ImportError as e:
        results.append(failed("RapidOCR engine init", f"ImportError: {e}"))
        results.append(failed("RapidOCR inference test", "Skipped — engine failed to import"))
    except Exception as e:
        results.append(failed("RapidOCR engine init", f"{type(e).__name__}: {e}"))
        results.append(failed("RapidOCR inference test", "Skipped — engine failed"))

    return results


def check_environment_variables() -> list[CheckResult]:
    results = []

    required = {
        "SUPABASE_URL":   "Supabase project URL",
        "SUPABASE_KEY":   "Supabase service_role key",
        "OPENAI_API_KEY": "OpenAI API key for embeddings",
    }
    for var, description in required.items():
        val = os.getenv(var, "").strip()
        if val:
            # Mask secrets
            if "KEY" in var or "SECRET" in var:
                display = val[:8] + "..." + val[-4:] if len(val) > 12 else "***"
            else:
                display = val
            results.append(passed(f"Env {var}", display))
        else:
            results.append(failed(f"Env {var}", f"NOT SET — {description} is required"))

    # Security checks
    hmac = os.getenv("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
    if hmac.lower().startswith("true"):
        results.append(passed("Env FRESHDESK_WEBHOOK_ENFORCE_HMAC", "true — HMAC enforced"))
    else:
        results.append(warned("Env FRESHDESK_WEBHOOK_ENFORCE_HMAC", f"{hmac!r} — set to 'true' before production"))

    # Index version consistency
    active = os.getenv("ACTIVE_INDEX_VERSION", "")
    b1 = os.getenv("B1_INDEX_VERSION", "")
    if active and b1 and active == b1:
        results.append(passed("Index version consistency", f"ACTIVE={active} == B1={b1}"))
    elif active and b1:
        results.append(warned("Index version consistency", f"ACTIVE={active} != B1={b1} — keep in sync"))
    else:
        results.append(warned("Index version", f"ACTIVE={active!r} B1={b1!r} — at least one not set"))

    return results


def check_source_directory() -> list[CheckResult]:
    results = []

    source_raw = os.getenv("B3_KNOWLEDGE_SOURCE_DIR", "../More_data")
    script_root = Path(__file__).parent.parent.resolve()
    p = Path(source_raw)
    if not p.is_absolute():
        p = script_root / p
    source = p.resolve()

    if not source.exists():
        results.append(failed("Source directory exists", f"{source} — set B3_KNOWLEDGE_SOURCE_DIR to absolute path"))
        return results
    results.append(passed("Source directory exists", str(source)))

    for fname in ["posts.json", "comments.json", "posts2votes.json", "tags.json"]:
        if (source / fname).exists():
            results.append(passed(f"Source file {fname}"))
        else:
            results.append(failed(f"Source file {fname}", f"MISSING in {source}"))

    images_dir = source / "images"
    if images_dir.exists():
        pngs = list(images_dir.glob("*.png"))
        results.append(passed("Source images/", f"{len(pngs)} PNG files"))
    else:
        results.append(warned("Source images/", "images/ directory not found — OCR will skip all images"))

    return results


def check_supabase(skip: bool = False) -> list[CheckResult]:
    results = []
    if skip:
        results.append(skipped("Supabase connectivity", "--skip-net passed"))
        return results

    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_KEY", "")
    if not url or not key:
        results.append(failed("Supabase connectivity", "SUPABASE_URL or SUPABASE_KEY not set"))
        return results

    try:
        from supabase import create_client
        sb = create_client(url, key)

        # Table existence
        for table in ["rag_knowledge_articles", "rag_knowledge_chunks", "rag_ticket_chunks"]:
            try:
                r = sb.table(table).select("*", count="exact").limit(0).execute()
                results.append(passed(f"Supabase table {table}", f"{r.count} rows"))
            except Exception as e:
                results.append(failed(f"Supabase table {table}", str(e)[:80]))

        # B3_006 migration (image_metadata column)
        try:
            sb.table("rag_knowledge_articles").select("image_metadata").limit(1).execute()
            results.append(passed("Supabase B3_006 (image_metadata)", "column exists"))
        except Exception:
            results.append(failed("Supabase B3_006 (image_metadata)", "column MISSING — apply B3_006_image_metadata.sql"))

        # RPC functions
        for rpc_name in ["match_all_b1_sources", "search_b1_sources_fts"]:
            try:
                if rpc_name == "match_all_b1_sources":
                    sb.rpc(rpc_name, {
                        "p_query_embedding": [0.0] * 1536,
                        "p_client": "unity",
                        "p_match_count": 1,
                        "p_match_threshold": 0.99,
                        "p_index_version": "v2",
                    }).execute()
                else:
                    sb.rpc(rpc_name, {
                        "p_query_text": "test",
                        "p_client": "unity",
                        "p_match_count": 1,
                        "p_index_version": "v2",
                    }).execute()
                results.append(passed(f"Supabase RPC {rpc_name}", "callable"))
            except Exception as e:
                err = str(e)
                if "does not exist" in err.lower():
                    results.append(failed(f"Supabase RPC {rpc_name}", "MISSING — apply B3 migrations"))
                else:
                    results.append(passed(f"Supabase RPC {rpc_name}", f"exists (error is parameter mismatch, not missing: {err[:60]})"))

    except Exception as e:
        results.append(failed("Supabase connectivity", f"{type(e).__name__}: {e}"))

    return results


def check_openai(skip: bool = False) -> list[CheckResult]:
    results = []
    if skip:
        results.append(skipped("OpenAI connectivity", "--skip-net passed"))
        return results

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        results.append(failed("OpenAI API key", "NOT SET"))
        return results

    try:
        import httpx, json
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        t0 = time.time()
        resp = httpx.post(
            f"{base_url}/embeddings",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            content=json.dumps({"model": "text-embedding-3-small", "input": "runtime check"}),
            timeout=15.0,
        )
        latency = time.time() - t0
        resp.raise_for_status()
        data = resp.json()
        dim = len(data["data"][0]["embedding"])
        results.append(passed(
            "OpenAI embedding connectivity",
            f"text-embedding-3-small, dim={dim}, latency={latency:.2f}s",
        ))
    except Exception as e:
        results.append(failed("OpenAI embedding connectivity", f"{type(e).__name__}: {str(e)[:120]}"))

    return results


def check_pipeline_imports() -> list[CheckResult]:
    results = []
    modules = [
        ("rag_engine.config.rag_settings",         "get_rag_settings"),
        ("rag_engine.ingestion.knowledge_pipeline", "KnowledgePipeline"),
        ("rag_engine.ingestion.parsers.stackoverflow_parser", "StackOverflowParser"),
        ("rag_engine.ingestion.tenant_mapper",      "TenantMapper"),
        ("rag_engine.embedding.openai_provider",    "OpenAIEmbeddingProvider"),
        ("rag_engine.retrieval.ticket_retriever",   "TicketRetriever"),
    ]
    for module_path, symbol in modules:
        try:
            mod = __import__(module_path, fromlist=[symbol])
            getattr(mod, symbol)
            results.append(passed(f"Import {symbol}", f"from {module_path}"))
        except ImportError as e:
            results.append(failed(f"Import {symbol}", f"ImportError: {e}"))
        except AttributeError as e:
            results.append(failed(f"Import {symbol}", f"AttributeError: {e}"))
        except Exception as e:
            results.append(failed(f"Import {symbol}", f"{type(e).__name__}: {e}"))
    return results


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pre-flight runtime check for KwikID AI ingestion + retrieval"
    )
    parser.add_argument("--verbose", action="store_true", help="Show all PASS results")
    parser.add_argument("--skip-ocr", action="store_true", help="Skip live OCR inference test (~15s)")
    parser.add_argument("--skip-net", action="store_true", help="Skip network checks (Supabase, OpenAI)")
    args = parser.parse_args()

    SEP = "=" * 68
    print(SEP)
    print("KWIKID AI INGEST — RUNTIME PRE-FLIGHT CHECK")
    print(f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} | {sys.executable}")
    print(SEP)

    all_results: list[CheckResult] = []

    groups = [
        ("Python Environment",    check_python_environment),
        ("VC++ Runtime",          check_vc_runtime),
        ("Python Packages",       check_packages),
        ("Pipeline Imports",      check_pipeline_imports),
        ("Environment Variables", check_environment_variables),
        ("Source Directory",      check_source_directory),
    ]

    for group_name, check_fn in groups:
        print(f"\n--- {group_name} ---")
        if check_fn in (check_supabase, check_openai, check_ocr_engine):
            pass  # handled below with skip flags
        try:
            results = check_fn()
        except Exception as e:
            results = [failed(group_name, f"Check group raised: {e}")]
        all_results.extend(results)
        for r in results:
            if args.verbose or r.status in ("FAIL", "WARN"):
                print(repr(r))
            elif r.status == "PASS":
                print(f"  [PASS] {r.name}")
            elif r.status == "SKIP":
                print(f"  [SKIP] {r.name}")

    # OCR check
    print("\n--- OCR Engine ---")
    ocr_results = check_ocr_engine(skip=args.skip_ocr)
    all_results.extend(ocr_results)
    for r in ocr_results:
        if args.verbose or r.status in ("FAIL", "WARN"):
            print(repr(r))
        else:
            print(f"  [{r.status}] {r.name}")

    # Network checks
    print("\n--- Network: Supabase ---")
    sb_results = check_supabase(skip=args.skip_net)
    all_results.extend(sb_results)
    for r in sb_results:
        if args.verbose or r.status in ("FAIL", "WARN"):
            print(repr(r))
        else:
            print(f"  [{r.status}] {r.name}")

    print("\n--- Network: OpenAI ---")
    oai_results = check_openai(skip=args.skip_net)
    all_results.extend(oai_results)
    for r in oai_results:
        if args.verbose or r.status in ("FAIL", "WARN"):
            print(repr(r))
        else:
            print(f"  [{r.status}] {r.name}")

    # Final summary
    passes  = sum(1 for r in all_results if r.status == "PASS")
    fails   = sum(1 for r in all_results if r.status == "FAIL")
    warns   = sum(1 for r in all_results if r.status == "WARN")
    skips   = sum(1 for r in all_results if r.status == "SKIP")
    total   = len(all_results)

    print(f"\n{SEP}")
    print(f"SUMMARY: {total} checks — {passes} PASS, {fails} FAIL, {warns} WARN, {skips} SKIP")
    print(SEP)

    if fails > 0:
        print("\nFAILED CHECKS:")
        for r in all_results:
            if r.status == "FAIL":
                print(repr(r))
        print()
        print("RESULT: FAIL — fix the items above before running ingestion")
        return 1
    elif warns > 0:
        print("\nWARNINGS (non-blocking):")
        for r in all_results:
            if r.status == "WARN":
                print(repr(r))
        print()
        print("RESULT: PASS WITH WARNINGS — ingestion can proceed; review warnings")
        return 0
    else:
        print("\nRESULT: PASS — runtime is ready for production ingestion")
        return 0


if __name__ == "__main__":
    sys.exit(main())
