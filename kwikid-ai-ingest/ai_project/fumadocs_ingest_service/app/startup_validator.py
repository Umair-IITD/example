"""
app/startup_validator.py

Sprint 2.52: Deterministic startup validation for the Support Automation
Platform runtime.

Responsibilities
----------------
- Validate the presence + coherence of every production configuration group
  the runtime requires (Freshdesk, Unity, Metrics Platform, Prometheus,
  Database, Runtime Registry, Tool Registration, Investigation Runtime,
  Audit backend).
- Wire the production Unity + Metrics tool adapters into a supplied
  `ToolRegistry` (Sprint 2.17 or Sprint 2.45).
- Emit a single concise `STARTUP_READY` readiness summary at INFO level.
- Never mutate the environment. Never overwrite user values. Never expose
  secrets in the printed report.

Design rules
------------
- **Fail fast on hard-required config.** `RAG_API_KEY`, `SUPABASE_URL`,
  `SUPABASE_KEY` — missing any of these raises `StartupValidationError`.
- **Warn on optional-but-recommended config.** Unity password, Metrics
  API key, dedicated AI Freshdesk agent key — missing produces a WARN
  entry in the report but startup continues (offline mode).
- **Idempotent.** Safe to call multiple times.
- **Never raises for optional integrations.** Wiring failures are captured
  in the report; the runtime remains functional in degraded mode.
- No I/O beyond reading environment + calling `.register()` on the supplied
  registry. No network calls — reachability is not verified here (that is
  the health-check endpoint's job).

Dependency direction
--------------------
    startup_validator.py → stdlib + os + logging
    startup_validator.py → freshdesk.config (optional)
    startup_validator.py → metrics_platform.config
    startup_validator.py → unity.config
    startup_validator.py → case_engine.tools.adapters
        (register_unity_tools + register_metrics_tools)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

LOGGER = logging.getLogger("app.startup")


# ── Component enum + report models ────────────────────────────────────────────

class ComponentStatus(str, Enum):
    OK          = "OK"
    WARN        = "WARN"
    FAIL        = "FAIL"
    SKIPPED     = "SKIPPED"
    NOT_WIRED   = "NOT_WIRED"


@dataclass(frozen=True)
class ComponentCheck:
    """One line of the STARTUP_READY table."""
    name:   str          # e.g. "Freshdesk"
    status: ComponentStatus
    detail: str = ""     # PII-free reason string

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status.value, "detail": self.detail}


@dataclass(frozen=True)
class StartupReport:
    """Complete result of `validate_startup()`."""
    checks:            tuple[ComponentCheck, ...]
    registered_tools:  tuple[str, ...] = ()
    ready:             bool = True

    @property
    def has_failures(self) -> bool:
        return any(c.status == ComponentStatus.FAIL for c in self.checks)

    @property
    def warnings(self) -> tuple[ComponentCheck, ...]:
        return tuple(c for c in self.checks if c.status == ComponentStatus.WARN)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready":            self.ready,
            "checks":           [c.to_dict() for c in self.checks],
            "registered_tools": list(self.registered_tools),
            "warning_count":    len(self.warnings),
            "failure_count":    sum(1 for c in self.checks if c.status == ComponentStatus.FAIL),
        }


class StartupValidationError(RuntimeError):
    """Raised when a hard-required configuration group is missing / invalid."""

    def __init__(self, missing_or_invalid: Iterable[str]) -> None:
        items = tuple(missing_or_invalid)
        super().__init__(
            "startup validation failed — missing / invalid: "
            + ", ".join(items)
        )
        self.items = items


# ── Individual checks ────────────────────────────────────────────────────────

def _check_freshdesk() -> ComponentCheck:
    """Presence + coherence of Freshdesk config."""
    enabled  = _env_bool("FRESHDESK_WEBHOOK_ENABLED", default=False)
    secret   = os.environ.get("FRESHDESK_WEBHOOK_SECRET", "").strip()
    enforce  = _env_bool("FRESHDESK_WEBHOOK_ENFORCE_HMAC", default=False)
    domain   = os.environ.get("FRESHDESK_DOMAIN", "").strip()
    api_key  = os.environ.get("FRESHDESK_API_KEY", "").strip()

    if not enabled:
        return ComponentCheck("Freshdesk", ComponentStatus.SKIPPED,
                              detail="webhook disabled (FRESHDESK_WEBHOOK_ENABLED=false)")
    if enforce and not secret:
        return ComponentCheck("Freshdesk", ComponentStatus.FAIL,
                              detail="ENFORCE_HMAC=true but FRESHDESK_WEBHOOK_SECRET is empty")
    warnings = []
    if not secret:
        warnings.append("no webhook secret configured (HMAC validation disabled)")
    if not domain:
        warnings.append("no FRESHDESK_DOMAIN configured (writes disabled)")
    if not api_key:
        warnings.append("no FRESHDESK_API_KEY configured (writes disabled)")
    if warnings:
        return ComponentCheck("Freshdesk", ComponentStatus.WARN,
                              detail=" | ".join(warnings))
    return ComponentCheck("Freshdesk", ComponentStatus.OK)


def _check_unity() -> ComponentCheck:
    try:
        from unity import UnityConfig
    except ImportError as exc:
        return ComponentCheck("Unity", ComponentStatus.FAIL,
                              detail=f"unity package import failed: {exc}")
    try:
        cfg = UnityConfig.from_env()
    except ValueError as exc:
        return ComponentCheck("Unity", ComponentStatus.FAIL,
                              detail=f"config invalid: {exc}")
    if not cfg.enabled:
        return ComponentCheck("Unity", ComponentStatus.SKIPPED,
                              detail="UNITY_ENABLED=false")
    if not cfg.has_credentials:
        return ComponentCheck("Unity", ComponentStatus.WARN,
                              detail="UNITY_PASSWORD unset — token generation will fail at first request")
    return ComponentCheck("Unity", ComponentStatus.OK,
                          detail=f"base={cfg.base_url} user={cfg.masked_username}")


def _check_metrics_platform() -> ComponentCheck:
    try:
        from metrics_platform import MetricsPlatformConfig
    except ImportError as exc:
        return ComponentCheck("Metrics Platform", ComponentStatus.FAIL,
                              detail=f"metrics_platform package import failed: {exc}")
    try:
        cfg = MetricsPlatformConfig.from_env()
    except ValueError as exc:
        return ComponentCheck("Metrics Platform", ComponentStatus.FAIL,
                              detail=f"config invalid: {exc}")
    if not cfg.enabled:
        return ComponentCheck("Metrics Platform", ComponentStatus.SKIPPED,
                              detail="METRICS_PLATFORM_ENABLED=false")
    # /metrics endpoint requires a credential unless auth_mode=none
    if cfg.effective_auth_mode == "none":
        return ComponentCheck("Metrics Platform", ComponentStatus.OK,
                              detail=f"base={cfg.base_url} auth=none (public endpoints only)")
    if not cfg.has_api_key and not cfg.has_prometheus_credentials:
        return ComponentCheck("Metrics Platform", ComponentStatus.WARN,
                              detail="no METRICS_PLATFORM_API_KEY and no Prometheus creds — /metrics will 401")
    return ComponentCheck("Metrics Platform", ComponentStatus.OK,
                          detail=f"base={cfg.base_url} auth={cfg.effective_auth_mode}")


def _check_prometheus() -> ComponentCheck:
    """
    Distinct from Metrics Platform. When explicit Prometheus creds are set
    they're used for basic-auth on the /metrics endpoint (Sprint 2.52).
    """
    try:
        from metrics_platform import MetricsPlatformConfig
    except ImportError as exc:
        return ComponentCheck("Prometheus", ComponentStatus.FAIL,
                              detail=f"metrics_platform import failed: {exc}")
    cfg = MetricsPlatformConfig.from_env()
    if not cfg.enabled:
        return ComponentCheck("Prometheus", ComponentStatus.SKIPPED,
                              detail="metrics platform disabled")
    if cfg.has_prometheus_credentials:
        return ComponentCheck("Prometheus", ComponentStatus.OK,
                              detail=f"basic-auth user={cfg.masked_prometheus_username}")
    return ComponentCheck("Prometheus", ComponentStatus.SKIPPED,
                          detail="no METRICS_PROMETHEUS_USERNAME/PASSWORD set — using METRICS_PLATFORM_API_KEY only")


def _check_database() -> ComponentCheck:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_KEY", "").strip()
    if not url or not key:
        return ComponentCheck("Database", ComponentStatus.FAIL,
                              detail="SUPABASE_URL and SUPABASE_KEY are required")
    if not url.startswith(("http://", "https://")):
        return ComponentCheck("Database", ComponentStatus.FAIL,
                              detail="SUPABASE_URL must include http(s):// scheme")
    if not (url.startswith("https://") or "localhost" in url or "127.0.0.1" in url):
        return ComponentCheck("Database", ComponentStatus.WARN,
                              detail="SUPABASE_URL is not HTTPS (dev / staging only)")
    return ComponentCheck("Database", ComponentStatus.OK,
                          detail=f"url={_mask_url(url)}")


def _check_runtime_registry(registry: Any) -> ComponentCheck:
    if registry is None:
        return ComponentCheck("Tool Registry", ComponentStatus.NOT_WIRED,
                              detail="registry not supplied to validator")
    try:
        count = len(registry)
    except TypeError:
        try:
            count = len(getattr(registry, "list_tool_names", lambda: [])())
        except Exception:
            count = -1
    return ComponentCheck("Tool Registry", ComponentStatus.OK,
                          detail=f"{count} tool(s) registered")


def _check_investigation_runtime(investigation_service: Any) -> ComponentCheck:
    if investigation_service is None:
        return ComponentCheck("Investigation Runtime", ComponentStatus.NOT_WIRED,
                              detail="investigation_service not supplied")
    return ComponentCheck("Investigation Runtime", ComponentStatus.OK,
                          detail=f"service={type(investigation_service).__name__}")


def _check_audit_backend() -> ComponentCheck:
    backend = os.environ.get("AUDIT_BACKEND", "inmemory").strip().lower()
    if backend not in {"inmemory", "supabase"}:
        return ComponentCheck("Audit", ComponentStatus.FAIL,
                              detail=f"invalid AUDIT_BACKEND={backend!r} — expected inmemory | supabase")
    if backend == "inmemory":
        return ComponentCheck("Audit", ComponentStatus.WARN,
                              detail="AUDIT_BACKEND=inmemory — events lost on restart (dev only)")
    return ComponentCheck("Audit", ComponentStatus.OK,
                          detail="backend=supabase")


def _check_hard_required() -> list[str]:
    """
    Return a list of names of hard-required env vars that are missing / empty.
    Empty list means all required vars are present.
    """
    missing: list[str] = []
    for name in ("RAG_API_KEY", "SUPABASE_URL", "SUPABASE_KEY"):
        val = os.environ.get(name, "").strip()
        if not val:
            # RAG_API_KEYS (plural, rotation) is accepted as an alternative
            # for RAG_API_KEY only.
            if name == "RAG_API_KEY" and os.environ.get("RAG_API_KEYS", "").strip():
                continue
            missing.append(name)
    return missing


# ── Tool registration ────────────────────────────────────────────────────────

def _register_production_tools(registry: Any) -> tuple[list[str], list[str]]:
    """
    Run the two Sprint 2.51 / 2.50 registrars against the supplied registry.

    Returns:
        (registered_names, error_messages)

    Never raises. Failures per adapter are captured; the runtime remains
    functional in degraded mode when an integration cannot register.
    """
    registered: list[str] = []
    errors:     list[str] = []
    if registry is None:
        return registered, ["registry not supplied — no production tools registered"]

    # ── Unity (5 production adapters) ────────────────────────────────────────
    try:
        from case_engine.tools.adapters import register_unity_tools
        from unity import UnityConfig
        outcomes = register_unity_tools(registry, config=UnityConfig.from_env())
        for name, status in outcomes.items():
            if status == "registered" or status == "replaced":
                registered.append(name)
                LOGGER.info("REGISTERED_TOOL: %s (unity)", name)
            else:
                errors.append(f"unity:{name}:{status}")
    except Exception as exc:
        errors.append(f"unity:import_or_register_failed:{exc}")

    # ── Metrics platform (2 production adapters) ─────────────────────────────
    try:
        from case_engine.tools.adapters import register_metrics_tools
        from metrics_platform import MetricsPlatformConfig
        outcomes = register_metrics_tools(registry, config=MetricsPlatformConfig.from_env())
        for name, status in outcomes.items():
            if status == "registered" or status == "replaced":
                registered.append(name)
                LOGGER.info("REGISTERED_TOOL: %s (metrics_platform)", name)
            else:
                errors.append(f"metrics:{name}:{status}")
    except Exception as exc:
        errors.append(f"metrics:import_or_register_failed:{exc}")

    return registered, errors


# ── Public entry point ───────────────────────────────────────────────────────

def validate_startup(
    *,
    tool_registry:          Any = None,
    investigation_service:  Any = None,
    strict:                 bool = False,
    register_production_tools: bool = True,
) -> StartupReport:
    """
    Run the full startup-validation sequence and produce a `StartupReport`.

    Args:
        tool_registry:         Sprint 2.17 `ToolRegistry` or Sprint 2.45
                               `ProductionToolRegistry`. When supplied, the
                               function will additionally REGISTER the
                               production Unity + Metrics adapters into it
                               (unless `register_production_tools=False`).
        investigation_service: The pre-warmed InvestigationService instance
                               (produced by `build_investigation_service`).
        strict:                When True, raises `StartupValidationError`
                               if any hard-required env var is missing.
                               When False (default), the failures are
                               recorded in the report and `ready=False`.

    Returns:
        StartupReport — never None. Prints the STARTUP_READY summary at INFO.
    """
    LOGGER.info("ENTER_STARTUP_VALIDATION")

    hard_missing = _check_hard_required()
    if strict and hard_missing:
        LOGGER.warning("EXCEPTION_STARTUP: missing required env vars: %s", hard_missing)
        raise StartupValidationError(hard_missing)

    checks: list[ComponentCheck] = []
    checks.append(_check_freshdesk())
    checks.append(_check_unity())
    checks.append(_check_metrics_platform())
    checks.append(_check_prometheus())
    checks.append(_check_database())

    registered: list[str] = []
    if tool_registry is not None:
        if register_production_tools:
            reg_names, reg_errors = _register_production_tools(tool_registry)
            registered.extend(reg_names)
            if reg_errors:
                checks.append(ComponentCheck(
                    "Tool Registration",
                    ComponentStatus.WARN if reg_names else ComponentStatus.FAIL,
                    detail=f"registered={len(reg_names)}; errors={len(reg_errors)}",
                ))
            else:
                checks.append(ComponentCheck(
                    "Tool Registration",
                    ComponentStatus.OK,
                    detail=f"{len(reg_names)} production tools registered",
                ))
        else:
            checks.append(ComponentCheck(
                "Tool Registration",
                ComponentStatus.SKIPPED,
                detail="register_production_tools=False",
            ))
    else:
        checks.append(ComponentCheck(
            "Tool Registration",
            ComponentStatus.NOT_WIRED,
            detail="tool_registry not supplied",
        ))

    checks.append(_check_runtime_registry(tool_registry))
    checks.append(_check_investigation_runtime(investigation_service))
    checks.append(_check_audit_backend())

    if hard_missing:
        checks.append(ComponentCheck(
            "Required Env Vars",
            ComponentStatus.FAIL,
            detail=f"missing: {hard_missing}",
        ))
    else:
        checks.append(ComponentCheck(
            "Required Env Vars",
            ComponentStatus.OK,
            detail="RAG_API_KEY + SUPABASE_URL + SUPABASE_KEY all present",
        ))

    report = StartupReport(
        checks=tuple(checks),
        registered_tools=tuple(sorted(set(registered))),
        ready=not any(c.status == ComponentStatus.FAIL for c in checks),
    )
    _emit_readiness_summary(report)
    LOGGER.info(
        "EXIT_STARTUP_VALIDATION ready=%s failures=%d warnings=%d",
        report.ready, report.to_dict()["failure_count"], len(report.warnings),
    )
    return report


# ── Formatting ───────────────────────────────────────────────────────────────

_MAX_NAME_LEN = 24


def _emit_readiness_summary(report: StartupReport) -> None:
    banner = "STARTUP_READY" if report.ready else "STARTUP_NOT_READY"
    lines = [banner, ""]
    for check in report.checks:
        dots = "." * max(3, _MAX_NAME_LEN - len(check.name))
        line = f"{check.name} {dots} {check.status.value}"
        if check.detail:
            line += f"    ({check.detail})"
        lines.append(line)
    if report.registered_tools:
        lines.append("")
        lines.append(f"REGISTERED_TOOLS ({len(report.registered_tools)}):")
        for name in report.registered_tools:
            lines.append(f"  - {name}")
    if report.warnings:
        lines.append("")
        lines.append(f"WARNINGS ({len(report.warnings)}):")
        for c in report.warnings:
            lines.append(f"  ! {c.name}: {c.detail}")
    for line in lines:
        LOGGER.warning(line)


# ── Utilities ─────────────────────────────────────────────────────────────────

def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _mask_url(url: str) -> str:
    """
    Mask any embedded credentials in a URL.
    `https://user:pw@host/path` → `https://***@host/path`.
    """
    if "@" not in url:
        return url
    scheme, rest = url.split("://", 1) if "://" in url else ("", url)
    _creds, host = rest.split("@", 1)
    return f"{scheme}://***@{host}" if scheme else f"***@{host}"
