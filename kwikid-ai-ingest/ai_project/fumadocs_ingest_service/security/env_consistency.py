"""
security/env_consistency.py

Sprint 2.10: ConfigConsistencyValidator — detect .env vs .env.example drift.

Responsibilities:
  1. Parse .env.example to extract all documented variable names and default values.
  2. Parse .env to extract all configured variable names and values.
  3. Report: missing_from_env, orphan_vars, empty_required.
  4. validate_against_environ() checks os.environ for required vars at startup.

Design:
  - conservative: only raises for provably absent REQUIRED vars.
  - Missing optional vars → warnings only.
  - Both files are optional: if .env is absent, os.environ is checked instead.
  - SECRET VALUES are never logged — only KEY names appear in reports.

Usage (standalone):
    from security.env_consistency import ConfigConsistencyValidator
    validator = ConfigConsistencyValidator()
    report = validator.check()          # never raises
    report = validator.validate()       # raises ConfigConsistencyError on hard errors

Usage (startup):
    validator.validate_against_environ()  # raises ConfigConsistencyError if required vars absent
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

LOGGER = logging.getLogger(__name__)

# Variables that MUST have a non-empty value in os.environ for production readiness.
# Absence or empty value → ConfigConsistencyError from validate_against_environ().
REQUIRED_IN_PRODUCTION: frozenset[str] = frozenset({
    "SUPABASE_URL",
    "SUPABASE_KEY",
    "RAG_API_KEY",
})

# Variables that appear in .env.example with empty values but are intentionally optional.
# Only a WARNING is emitted when these are absent.
_KNOWN_OPTIONAL: frozenset[str] = frozenset({
    "EMBEDDING_API_KEY",           # fallback to OPENAI_API_KEY
    "RAG_API_KEYS",                # alternative multi-key format
    "FRESHDESK_WEBHOOK_SECRET",    # only required when FRESHDESK_WEBHOOK_ENFORCE_HMAC=true
    "FRESHDESK_DOMAIN",
    "FRESHDESK_API_KEY",
    "FRESHDESK_UPDATED_SINCE",
    "FRESHDESK_WEBHOOK_DEFAULT_CLIENT",
    "METADATA_TENANT",
    "METADATA_ACCESS_SCOPE",
    "OPENAI_API_KEY",              # may be set as EMBEDDING_API_KEY instead
    "OPENAI_CHAT_API_KEY",         # may be shared with OPENAI_API_KEY
    "LOG_DIR",
    "TRACE_DIR",
})

_ENV_VAR_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)")


@dataclass
class ConsistencyReport:
    """
    Result of a .env vs .env.example consistency check.

    missing_from_env: keys in .env.example that are absent from .env
    orphan_vars:      keys in .env that are absent from .env.example
    empty_required:   REQUIRED keys that are empty/absent in .env or os.environ
    warnings:         advisory messages (non-blocking)
    errors:           blocking messages (used by validate())
    is_consistent:    True when no hard errors exist
    """

    missing_from_env: list[str] = field(default_factory=list)
    orphan_vars: list[str] = field(default_factory=list)
    empty_required: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def is_consistent(self) -> bool:
        return len(self.errors) == 0


class ConfigConsistencyError(RuntimeError):
    """Raised when required environment variables are missing or empty."""


class ConfigConsistencyValidator:
    """
    Compares .env and .env.example to detect configuration drift.

    Instantiation auto-discovers both files by walking up from CWD.

    Methods:
        check()                    — compare files, return report, never raises.
        validate()                 — like check(), but raises on hard errors.
        validate_against_environ() — check os.environ for REQUIRED vars, raises if absent.
    """

    def __init__(
        self,
        env_path: str | Path | None = None,
        example_path: str | Path | None = None,
        required_var_names: frozenset[str] | None = None,
    ) -> None:
        self._env_path = Path(env_path) if env_path else _find_env_file(".env")
        self._example_path = (
            Path(example_path) if example_path else _find_env_file(".env.example")
        )
        self._required = (
            required_var_names if required_var_names is not None else REQUIRED_IN_PRODUCTION
        )

    # ── Public API ─────────────────────────────────────────────────────────────

    def check(self) -> ConsistencyReport:
        """Compare .env and .env.example. Returns report. Never raises."""
        report = ConsistencyReport()
        try:
            self._run(report)
        except Exception as exc:
            LOGGER.error("env_consistency.check: unexpected error: %s", exc)
            report.errors.append(f"Consistency check failed: {exc}")
        return report

    def validate(self) -> ConsistencyReport:
        """
        Compare files and raise ConfigConsistencyError if hard errors exist.

        Use this in CI pipelines or pre-flight scripts where you want strict failure.
        For production startup, use validate_against_environ() instead.
        """
        report = self.check()
        if not report.is_consistent:
            msg = "Environment file consistency errors:\n" + "\n".join(
                f"  - {e}" for e in report.errors
            )
            raise ConfigConsistencyError(msg)
        return report

    def validate_against_environ(self) -> ConsistencyReport:
        """
        Check os.environ for REQUIRED vars. Raise ConfigConsistencyError if any are absent.

        Also runs file drift check (check()) and logs warnings, but does NOT raise
        on drift — drift is advisory in production where .env may not exist.
        """
        report = self.check()

        # Log drift warnings
        for w in report.warnings:
            LOGGER.warning("env_consistency: %s", w)

        # Check required vars against live os.environ (not just .env file)
        environ_errors: list[str] = []
        missing_required: list[str] = []
        for var in sorted(self._required):
            val = os.environ.get(var, "").strip()
            if not val:
                missing_required.append(var)
                environ_errors.append(
                    f"Required env var {var!r} is not set or is empty"
                )

        if environ_errors:
            msg = "Missing required environment variables:\n" + "\n".join(
                f"  - {e}" for e in environ_errors
            )
            LOGGER.critical("env_consistency.validate_against_environ: %s", msg)
            raise ConfigConsistencyError(msg)

        if missing_required:
            report.empty_required.extend(missing_required)

        LOGGER.info(
            "env_consistency: startup validation OK "
            "(missing_from_env=%d orphan=%d)",
            len(report.missing_from_env),
            len(report.orphan_vars),
        )
        return report

    # ── Internal ───────────────────────────────────────────────────────────────

    def _run(self, report: ConsistencyReport) -> None:
        example_vars = _parse_env_file(self._example_path)
        env_vars = _parse_env_file(self._env_path)

        if not example_vars:
            report.warnings.append(
                f".env.example not found or empty at {self._example_path!s}. "
                "Cannot perform drift check."
            )
            return

        example_keys = set(example_vars.keys())
        env_keys = set(env_vars.keys())

        # Vars in .env.example not in .env
        missing = example_keys - env_keys
        if missing:
            report.missing_from_env = sorted(missing)
            for k in sorted(missing):
                if k in self._required:
                    report.errors.append(
                        f"Required variable {k!r} is in .env.example but missing from .env"
                    )
                else:
                    report.warnings.append(
                        f"Variable {k!r} is documented in .env.example but missing from .env"
                    )

        # Vars in .env not in .env.example (undocumented)
        orphan = env_keys - example_keys
        if orphan:
            report.orphan_vars = sorted(orphan)
            for k in sorted(orphan):
                report.warnings.append(
                    f"Variable {k!r} is in .env but not documented in .env.example"
                )

        # Check required vars for empty values in .env
        for var in sorted(self._required):
            if var in env_vars and not env_vars[var].strip():
                if var not in report.empty_required:
                    report.empty_required.append(var)
                report.errors.append(
                    f"Required variable {var!r} is present in .env but has an empty value"
                )

        _log_report(report)


def _find_env_file(name: str) -> Path:
    """Walk up from CWD to find a .env or .env.example file."""
    cwd = Path.cwd()
    for directory in [cwd, cwd.parent, cwd.parent.parent]:
        candidate = directory / name
        if candidate.exists():
            return candidate
    return cwd / name  # Return canonical path even if not found


def _parse_env_file(path: Path) -> dict[str, str]:
    """
    Parse a .env-format file. Returns {KEY: value} dict.
    Ignores blank lines and comment lines. Returns {} if file absent.
    Secret values are stored but should never be logged.
    """
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                m = _ENV_VAR_RE.match(line)
                if m:
                    key = m.group(1)
                    val = m.group(2).strip()
                    if (
                        (val.startswith('"') and val.endswith('"'))
                        or (val.startswith("'") and val.endswith("'"))
                    ) and len(val) >= 2:
                        val = val[1:-1]
                    result[key] = val
    except OSError as exc:
        LOGGER.warning("env_consistency: cannot read %s: %s", path, exc)
    return result


def _log_report(report: ConsistencyReport) -> None:
    if report.errors:
        for msg in report.errors:
            LOGGER.error("env_consistency: %s", msg)
    if report.warnings:
        for msg in report.warnings[:10]:  # cap to avoid log flood
            LOGGER.warning("env_consistency: %s", msg)
        if len(report.warnings) > 10:
            LOGGER.warning(
                "env_consistency: ... and %d more warnings suppressed",
                len(report.warnings) - 10,
            )
