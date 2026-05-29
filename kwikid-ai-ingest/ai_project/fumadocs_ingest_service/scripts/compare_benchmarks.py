"""
scripts/compare_benchmarks.py

Stage 0.6 Phase 6: Before/after benchmark comparison for prompt optimization regression testing.

Usage:
    python scripts/compare_benchmarks.py \\
        --before benchmark_results_before.json \\
        --after  benchmark_results_after.json \\
        --output regression_report.md

Flags a regression when any metric degrades beyond the tolerance defined in REGRESSION_THRESHOLDS.
Exits with code 1 if regressions are found (for CI integration).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# ── Regression thresholds ───────────────────────────────────────────────────────
# A metric is flagged as a regression when the *after* value is worse by more than
# this relative fraction. Directional: "worse" means higher latency / higher tokens /
# lower accuracy / higher schema retry rate.
REGRESSION_THRESHOLDS: dict[str, float] = {
    # Latency — 10% degradation is acceptable variance; beyond that, flag it
    "p50_wall_ms":  0.10,
    "p90_wall_ms":  0.15,
    "p99_wall_ms":  0.20,
    # Token counts — any increase is a regression (we only optimize downward)
    "avg_llm_prompt_tokens":      0.00,
    "avg_llm_completion_tokens":  0.00,
    "avg_overhead_tokens":        0.00,
    "avg_context_tokens":         0.05,  # small slack for retrieval variance
    "avg_total_tokens":           0.00,
    # Quality — these must never degrade
    "workflow_accuracy":          0.00,  # exact: higher is better → flag any decrease
    "schema_retry_rate":          0.00,  # lower is better → flag any increase
    "avg_answer_word_count":      0.30,  # answers may be shorter (target) but not by >30%
}

# Which metrics are "lower is better" vs "higher is better"
LOWER_IS_BETTER: set[str] = {
    "p50_wall_ms", "p90_wall_ms", "p99_wall_ms",
    "avg_llm_prompt_tokens", "avg_llm_completion_tokens", "avg_overhead_tokens",
    "avg_context_tokens", "avg_total_tokens", "schema_retry_rate",
}
HIGHER_IS_BETTER: set[str] = {
    "workflow_accuracy", "avg_answer_word_count",
}


def _load(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        print(f"ERROR: file not found: {p}", file=sys.stderr)
        sys.exit(2)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _pct(delta: float, base: float) -> str:
    if base == 0:
        return "n/a"
    return f"{delta / base * 100:+.1f}%"


def _compute_summary(data: dict[str, Any]) -> dict[str, float | None]:
    """Flatten top-level aggregate fields from benchmark_results.json."""
    summary: dict[str, float | None] = {}
    agg = data.get("aggregate", {})

    # Latency percentiles
    for k in ("p50_wall_ms", "p90_wall_ms", "p99_wall_ms"):
        summary[k] = agg.get(k)

    # Token averages
    token_fields = (
        "avg_llm_prompt_tokens",
        "avg_llm_completion_tokens",
        "avg_overhead_tokens",
        "avg_context_tokens",
        "avg_total_tokens",
    )
    for k in token_fields:
        summary[k] = agg.get(k)

    # Quality
    summary["workflow_accuracy"]    = agg.get("workflow_accuracy")
    summary["schema_retry_rate"]    = agg.get("schema_retry_rate")
    summary["avg_answer_word_count"] = agg.get("avg_answer_word_count")

    return summary


def _check_regression(
    metric: str,
    before: float,
    after: float,
) -> tuple[bool, str]:
    """Returns (is_regression, description)."""
    threshold = REGRESSION_THRESHOLDS.get(metric, 0.05)

    if metric in LOWER_IS_BETTER:
        # Regression = after is higher (worse) by more than threshold fraction
        if before == 0:
            return False, ""
        degradation = (after - before) / before
        if degradation > threshold:
            return True, f"{_pct(after - before, before)} worse (threshold: +{threshold*100:.0f}%)"
        return False, ""

    if metric in HIGHER_IS_BETTER:
        # Regression = after is lower (worse) by more than threshold fraction
        if before == 0:
            return False, ""
        degradation = (before - after) / before
        if degradation > threshold:
            return True, f"{_pct(after - before, before)} worse (threshold: -{threshold*100:.0f}%)"
        return False, ""

    return False, ""


def _category_deltas(
    before: dict[str, Any],
    after: dict[str, Any],
) -> list[dict[str, Any]]:
    """Per-category comparison for workflow_accuracy and latency."""
    b_cats: dict[str, Any] = before.get("by_category", {})
    a_cats: dict[str, Any] = after.get("by_category", {})
    rows: list[dict[str, Any]] = []
    for cat in sorted(set(b_cats) | set(a_cats)):
        b = b_cats.get(cat, {})
        a = a_cats.get(cat, {})
        rows.append({
            "category": cat,
            "before_p50": b.get("p50_wall_ms"),
            "after_p50":  a.get("p50_wall_ms"),
            "before_wf_acc": b.get("workflow_accuracy"),
            "after_wf_acc":  a.get("workflow_accuracy"),
        })
    return rows


def _fmt_opt(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{v:.1f}"


def compare(before_path: str, after_path: str, output_path: str) -> int:
    before = _load(before_path)
    after = _load(after_path)

    b_sum = _compute_summary(before)
    a_sum = _compute_summary(after)

    regressions: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []
    neutral: list[dict[str, Any]] = []

    for metric in REGRESSION_THRESHOLDS:
        bv = b_sum.get(metric)
        av = a_sum.get(metric)
        if bv is None or av is None:
            neutral.append({"metric": metric, "before": bv, "after": av, "note": "missing in one run"})
            continue

        is_reg, reason = _check_regression(metric, bv, av)
        delta_str = _pct(av - bv, bv) if bv != 0 else "n/a"

        if is_reg:
            regressions.append({"metric": metric, "before": bv, "after": av, "delta": delta_str, "reason": reason})
        else:
            # Classify as improvement vs neutral
            if metric in LOWER_IS_BETTER and av < bv:
                improvements.append({"metric": metric, "before": bv, "after": av, "delta": delta_str})
            elif metric in HIGHER_IS_BETTER and av > bv:
                improvements.append({"metric": metric, "before": bv, "after": av, "delta": delta_str})
            else:
                neutral.append({"metric": metric, "before": bv, "after": av, "delta": delta_str, "note": ""})

    cat_rows = _category_deltas(before, after)

    # ── Build markdown report ─────────────────────────────────────────────────
    lines: list[str] = [
        "# Stage 0.6 Benchmark Regression Report",
        "",
        f"**Before:** `{before_path}`  ",
        f"**After:**  `{after_path}`  ",
        "",
    ]

    if regressions:
        lines += [
            "## Regressions Detected",
            "",
            "| Metric | Before | After | Delta | Reason |",
            "|--------|--------|-------|-------|--------|",
        ]
        for r in regressions:
            lines.append(
                f"| {r['metric']} | {_fmt_opt(r['before'])} | {_fmt_opt(r['after'])} "
                f"| {r['delta']} | {r['reason']} |"
            )
        lines.append("")
    else:
        lines += ["## Regressions Detected", "", "None — all metrics within tolerance.", ""]

    lines += [
        "## Improvements",
        "",
        "| Metric | Before | After | Delta |",
        "|--------|--------|-------|-------|",
    ]
    for i in improvements:
        lines.append(
            f"| {i['metric']} | {_fmt_opt(i['before'])} | {_fmt_opt(i['after'])} | {i['delta']} |"
        )
    if not improvements:
        lines.append("| — | — | — | — |")
    lines.append("")

    lines += [
        "## Neutral / Missing",
        "",
        "| Metric | Before | After | Delta | Note |",
        "|--------|--------|-------|-------|------|",
    ]
    for n in neutral:
        lines.append(
            f"| {n['metric']} | {_fmt_opt(n['before'])} | {_fmt_opt(n['after'])} "
            f"| {n.get('delta', 'n/a')} | {n.get('note', '')} |"
        )
    if not neutral:
        lines.append("| — | — | — | — | — |")
    lines.append("")

    if cat_rows:
        lines += [
            "## Per-Category Latency & Workflow Accuracy",
            "",
            "| Category | Before p50 ms | After p50 ms | Before WF Acc | After WF Acc |",
            "|----------|---------------|--------------|----------------|---------------|",
        ]
        for r in cat_rows:
            lines.append(
                f"| {r['category']} | {_fmt_opt(r['before_p50'])} | {_fmt_opt(r['after_p50'])} "
                f"| {_fmt_opt(r['before_wf_acc'])} | {_fmt_opt(r['after_wf_acc'])} |"
            )
        lines.append("")

    lines += [
        "## Summary",
        "",
        f"- Regressions: **{len(regressions)}**",
        f"- Improvements: **{len(improvements)}**",
        f"- Neutral / missing: **{len(neutral)}**",
        "",
    ]

    report_md = "\n".join(lines)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report_md, encoding="utf-8")
    print(report_md)
    print(f"\nReport written to: {out}")

    return 1 if regressions else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two benchmark_results.json files")
    parser.add_argument("--before", required=True, help="Path to baseline benchmark_results.json")
    parser.add_argument("--after",  required=True, help="Path to optimized benchmark_results.json")
    parser.add_argument("--output", default="regression_report.md", help="Output markdown path")
    args = parser.parse_args()
    sys.exit(compare(args.before, args.after, args.output))


if __name__ == "__main__":
    main()
