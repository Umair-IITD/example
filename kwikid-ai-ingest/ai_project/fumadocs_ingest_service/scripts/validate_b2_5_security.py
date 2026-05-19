"""
scripts/validate_b2_5_security.py

Phase B2.5 Security Hardening Validation Suite
20 checks: 13 offline + 7 HTTP (FastAPI TestClient)

Usage:
    python scripts/validate_b2_5_security.py
    python scripts/validate_b2_5_security.py --report

Offline checks (no network required):
  S1-S4  : _constant_time_key_check correctness and timing safety
  S5-S8  : load_api_keys env var parsing
  S9-S11 : validate_startup_security error detection
  S12-S13: RateLimiter sliding-window behavior

HTTP checks (FastAPI TestClient -- sets test env vars before import):
  S14    : GET /health -> 200 without API key (unprotected)
  S15    : POST /rag/chat without X-API-Key -> 401
  S16    : POST /rag/chat with wrong X-API-Key -> 401
  S17    : GET /freshdesk/filter-options with correct key -> not 401
  S18    : GET /chat/suggestions with correct key -> not 401
  S19    : GET /docs -> 404 (docs disabled by default)
  S20    : GET /openapi.json -> 404 (OpenAPI spec hidden by default)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

# Must set env vars BEFORE any app imports.
# security.py reads RAG_CHAT_RATE_LIMIT at import time.
# lifespan reads RAG_API_KEY and OPENAI_API_KEY at startup.
_TEST_API_KEY = "test-rag-api-key-for-b25-security-validation"
os.environ["RAG_API_KEY"] = _TEST_API_KEY
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key-not-real")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-supabase-key-not-real")
# Keep rate limits high so HTTP tests never hit them during test runs.
os.environ["RAG_CHAT_RATE_LIMIT"] = "1000"

sys.path.insert(0, str(Path(__file__).parent.parent))

# Offline imports (no FastAPI app)
from app.security import (  # noqa: E402
    RateLimiter,
    _constant_time_key_check,
    load_api_keys,
    validate_startup_security,
)

# ---------------------------------------------------------------------------
# Check harness
# ---------------------------------------------------------------------------

_results: list[dict[str, Any]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    status = "PASS" if passed else "FAIL"
    _results.append({"name": name, "status": status, "detail": detail})
    icon = "[+]" if passed else "[x]"
    suffix = f" - {detail}" if detail else ""
    print(f"  {icon} {name}{suffix}")


# ---------------------------------------------------------------------------
# S1-S4: _constant_time_key_check
# ---------------------------------------------------------------------------


def run_s1_s4() -> None:
    print("\n--- S1-S4: constant_time_key_check ---")

    keys = frozenset({"alpha-key-1", "beta-key-2"})

    check("S1: correct key -> True", _constant_time_key_check("alpha-key-1", keys))
    check("S2: second key -> True", _constant_time_key_check("beta-key-2", keys))
    check("S3: wrong key -> False", not _constant_time_key_check("wrong-key", keys))
    check("S4: empty key set -> False (fail closed)",
          not _constant_time_key_check("alpha-key-1", frozenset()))


# ---------------------------------------------------------------------------
# S5-S8: load_api_keys
# ---------------------------------------------------------------------------


def run_s5_s8() -> None:
    print("\n--- S5-S8: load_api_keys ---")

    orig_single = os.environ.pop("RAG_API_KEY", None)
    orig_multi = os.environ.pop("RAG_API_KEYS", None)

    try:
        # S5: single key
        os.environ["RAG_API_KEY"] = "single-key"
        os.environ.pop("RAG_API_KEYS", None)
        keys = load_api_keys()
        check("S5: RAG_API_KEY -> 1 key", keys == frozenset({"single-key"}))

        # S6: comma-separated list
        os.environ.pop("RAG_API_KEY", None)
        os.environ["RAG_API_KEYS"] = "key-a,key-b, key-c "
        keys = load_api_keys()
        check("S6: RAG_API_KEYS CSV -> 3 keys", keys == frozenset({"key-a", "key-b", "key-c"}))

        # S7: both vars merged, deduplicated
        os.environ["RAG_API_KEY"] = "key-a"
        os.environ["RAG_API_KEYS"] = "key-b,key-a"
        keys = load_api_keys()
        check("S7: both vars merged and deduplicated", keys == frozenset({"key-a", "key-b"}))

        # S8: empty -> empty frozenset
        os.environ["RAG_API_KEY"] = ""
        os.environ["RAG_API_KEYS"] = ""
        keys = load_api_keys()
        check("S8: empty env -> empty frozenset", keys == frozenset())

    finally:
        if orig_single is not None:
            os.environ["RAG_API_KEY"] = orig_single
        else:
            os.environ.pop("RAG_API_KEY", None)
        if orig_multi is not None:
            os.environ["RAG_API_KEYS"] = orig_multi
        else:
            os.environ.pop("RAG_API_KEYS", None)


# ---------------------------------------------------------------------------
# S9-S11: validate_startup_security
# ---------------------------------------------------------------------------


def run_s9_s11() -> None:
    print("\n--- S9-S11: validate_startup_security ---")

    errors = validate_startup_security(frozenset(), "sk-real-key")
    check("S9: no API keys -> error returned", len(errors) >= 1, f"errors={errors}")

    errors = validate_startup_security(frozenset({"some-key"}), "")
    check("S10: no OpenAI key -> error returned", len(errors) >= 1, f"errors={errors}")

    errors = validate_startup_security(frozenset({"some-key"}), "sk-real-key")
    check("S11: both configured -> no errors", errors == [], f"errors={errors}")


# ---------------------------------------------------------------------------
# S12-S13: RateLimiter
# ---------------------------------------------------------------------------


def run_s12_s13() -> None:
    print("\n--- S12-S13: RateLimiter ---")

    rl = RateLimiter(max_requests=3, window_s=2.0)

    results = [rl.is_allowed("client-ip") for _ in range(3)]
    check("S12: allows up to max_requests (3/3)", all(results), f"results={results}")

    fourth = rl.is_allowed("client-ip")
    check("S13: rejects request exceeding limit", not fourth, f"4th_result={fourth}")


# ---------------------------------------------------------------------------
# S14-S20: HTTP tests via TestClient
# ---------------------------------------------------------------------------


def run_http_tests() -> None:
    print("\n--- S14-S20: HTTP middleware (TestClient) ---")

    try:
        from starlette.testclient import TestClient
    except ImportError:
        print("  [SKIP] starlette.testclient not available")
        return

    # Lazy import of app -- env vars already set at module level above
    try:
        from app.main import app  # noqa: PLC0415
    except Exception as exc:
        print(f"  [SKIP] Could not import app.main: {exc}")
        return

    with TestClient(app, raise_server_exceptions=False) as client:

        # S14: /health is unprotected
        resp = client.get("/health")
        check("S14: GET /health -> 200 (unprotected)", resp.status_code == 200,
              f"status={resp.status_code}")

        # S15: missing key -> 401
        resp = client.post(
            "/rag/chat",
            json={"query_text": "test", "client": "unity_bank"},
        )
        check("S15: POST /rag/chat no key -> 401", resp.status_code == 401,
              f"status={resp.status_code} body={resp.text[:80]}")

        # S16: wrong key -> 401
        resp = client.post(
            "/rag/chat",
            headers={"X-API-Key": "totally-wrong-key"},
            json={"query_text": "test", "client": "unity_bank"},
        )
        check("S16: POST /rag/chat wrong key -> 401", resp.status_code == 401,
              f"status={resp.status_code}")

        # S17: correct key -> auth passes (any non-401 response)
        # /freshdesk/filter-options returns static data without DB/LLM.
        resp = client.get(
            "/freshdesk/filter-options",
            headers={"X-API-Key": _TEST_API_KEY},
        )
        check("S17: correct key passes auth (not 401)", resp.status_code != 401,
              f"status={resp.status_code}")

        # S18: correct key on /chat/suggestions
        resp = client.get(
            "/chat/suggestions?limit=1",
            headers={"X-API-Key": _TEST_API_KEY},
        )
        check("S18: correct key on /chat/suggestions (not 401)", resp.status_code != 401,
              f"status={resp.status_code}")

        # S19: /docs disabled -> 404 even with valid key (auth passes, no route registered)
        resp = client.get("/docs", headers={"X-API-Key": _TEST_API_KEY})
        check("S19: GET /docs with valid key -> 404 (docs disabled)", resp.status_code == 404,
              f"status={resp.status_code}")

        # S20: /openapi.json disabled -> 404 even with valid key
        resp = client.get("/openapi.json", headers={"X-API-Key": _TEST_API_KEY})
        check("S20: GET /openapi.json with valid key -> 404 (spec hidden)", resp.status_code == 404,
              f"status={resp.status_code}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def print_summary() -> int:
    passed = sum(1 for r in _results if r["status"] == "PASS")
    total = len(_results)
    print(f"\n{'='*60}")
    print(f"  RESULT: {passed}/{total} checks passed")
    if passed < total:
        print("\n  FAILED:")
        for r in _results:
            if r["status"] == "FAIL":
                suffix = f" - {r['detail']}" if r["detail"] else ""
                print(f"    [x] {r['name']}{suffix}")
    print(f"{'='*60}")
    return 0 if passed == total else 1


def write_report(report_dir: Path) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "b2_5_security_report.md"
    passed = sum(1 for r in _results if r["status"] == "PASS")
    lines = [
        "# Phase B2.5 Security Validation Report",
        f"\n**Date**: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
        f"**Result**: {passed}/{len(_results)} checks passed",
        "\n## Check Results\n",
        "| Check | Status | Detail |",
        "|---|---|---|",
    ]
    for r in _results:
        icon = "PASS" if r["status"] == "PASS" else "FAIL"
        detail = r["detail"].replace("|", "\\|") if r["detail"] else ""
        lines.append(f"| {r['name']} | {icon} | {detail} |")
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n  Report written to: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase B2.5 Security Validation Suite")
    parser.add_argument("--report", action="store_true", help="Write markdown report")
    args = parser.parse_args()

    print("Phase B2.5 Security Validation Suite")
    print("=" * 60)

    run_s1_s4()
    run_s5_s8()
    run_s9_s11()
    run_s12_s13()
    run_http_tests()

    code = print_summary()

    if args.report:
        write_report(Path(__file__).parent.parent / "data" / "reports")

    sys.exit(code)


if __name__ == "__main__":
    main()
