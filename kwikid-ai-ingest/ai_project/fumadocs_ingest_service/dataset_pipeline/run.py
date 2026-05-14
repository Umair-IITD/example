"""
CLI entrypoint for the KwikID dataset engineering pipeline.

Usage:
    python -m dataset_pipeline.run [--stages all] [--log-level INFO]

Stages:
    all         Run the full pipeline end-to-end (default)
    load        Stage 1: load source files only
    normalize   Stages 1-2: load + normalize
    filter      Stages 1-3: load + normalize + filter
    clean       Stages 1-4: ...+ clean text
    score       Stages 1-5: ...+ RCA scoring
    label       Stages 1-6: ...+ automation labeling
    extract     Stages 1-7: ...+ knowledge extraction
    eval        Stages 1-8: ...+ eval dataset
    export      Stages 1-9: full pipeline (same as 'all')
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from .config import default_config
from .stages import (  # noqa: F401  (imported for side effect of module init)
    s1_load,
    s2_normalize,
    s3_filter,
    s4_clean,
    s5_rca_score,
    s6_automate_label,
    s7_extract,
    s8_eval,
    s9_export,
)


def _setup_logging(level: str) -> None:
    fmt = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format=fmt)


def _stage_index(name: str) -> int:
    order = ["load", "normalize", "filter", "clean", "score", "label", "extract", "eval", "export"]
    try:
        return order.index(name)
    except ValueError:
        return len(order) - 1  # "all" or unknown → run everything


def run_pipeline(up_to_stage: str = "all", cfg=None) -> None:
    if cfg is None:
        cfg = default_config()

    stop = _stage_index(up_to_stage)
    logger = logging.getLogger(__name__)
    t0 = time.perf_counter()

    logger.info("=" * 60)
    logger.info("KwikID Dataset Pipeline — starting (up_to=%s)", up_to_stage)
    logger.info("=" * 60)

    # Stage 1: Load
    frames = s1_load.run(cfg)
    if stop == 0:
        return

    # Stage 2: Normalize
    tickets = s2_normalize.run(frames, cfg)
    if stop == 1:
        return

    # Stage 3: Filter
    included, excluded = s3_filter.run(tickets, cfg)
    all_tickets = included + excluded  # keep excluded for full reporting
    if stop == 2:
        return

    # Stage 4: Clean
    s4_clean.run(included, cfg)
    if stop == 3:
        return

    # Stage 5: RCA scoring
    s5_rca_score.run(included, cfg)
    if stop == 4:
        return

    # Stage 6: Automation labels (run on all — excluded get EXCLUDE label)
    s6_automate_label.run(all_tickets, cfg)
    if stop == 5:
        return

    # Stage 7: Knowledge extraction
    taxonomy, kc_candidates = s7_extract.run(included, cfg)
    if stop == 6:
        return

    # Stage 8: Evaluation dataset
    eval_dataset = s8_eval.run(included, cfg)
    if stop == 7:
        return

    # Stage 9: Export all artifacts
    s9_export.run(
        all_tickets=all_tickets,
        included=included,
        excluded=excluded,
        eval_dataset=eval_dataset,
        taxonomy=taxonomy,
        kc_candidates=kc_candidates,
        cfg=cfg,
    )

    elapsed = time.perf_counter() - t0
    logger.info("=" * 60)
    logger.info("Pipeline complete in %.1f seconds.", elapsed)
    logger.info("Outputs written to: %s", cfg.processed_dir)
    logger.info("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="KwikID Freshdesk dataset engineering pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--stages",
        default="all",
        choices=["all", "load", "normalize", "filter", "clean", "score", "label",
                 "extract", "eval", "export"],
        help="Run pipeline up to and including this stage (default: all)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    args = parser.parse_args()

    _setup_logging(args.log_level)
    run_pipeline(up_to_stage=args.stages)


if __name__ == "__main__":
    main()
