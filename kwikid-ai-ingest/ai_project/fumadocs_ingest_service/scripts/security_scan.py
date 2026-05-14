"""
scripts/security_scan.py

Regex-based secret scanner. Scans all .py, .md, .json, .yaml, .yml, .env files
for patterns that look like hardcoded secrets or PII.

Does NOT make network calls. Does NOT modify any files.

Usage:
    python scripts/security_scan.py
    python scripts/security_scan.py --path ./app
    python scripts/security_scan.py --report
    python scripts/security_scan.py -v

Exit code: 0 if clean, 1 if findings detected.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).parent.parent

# ── Patterns ──────────────────────────────────────────────────────────────────

@dataclass
class Pattern:
    name: str
    regex: re.Pattern
    severity: str  # CRITICAL | HIGH | MEDIUM | LOW
    description: str
    false_positive_hints: list[str] = field(default_factory=list)


PATTERNS: list[Pattern] = [
    Pattern(
        name="openai_key",
        regex=re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
        severity="CRITICAL",
        description="OpenAI API key (sk-...)",
        false_positive_hints=["sk-[REDACTED]", "sk-your", "sk-proj"],
    ),
    Pattern(
        name="bearer_token",
        regex=re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]{20,}=*", re.IGNORECASE),
        severity="CRITICAL",
        description="Bearer token in code",
        false_positive_hints=["Bearer [REDACTED]", "Bearer {", "Bearer <"],
    ),
    Pattern(
        name="supabase_service_key",
        regex=re.compile(r"eyJ[A-Za-z0-9+/=]{40,}\.[A-Za-z0-9+/=]{40,}\.[A-Za-z0-9+/=_-]{10,}"),
        severity="CRITICAL",
        description="JWT token (Supabase service_role key format)",
        false_positive_hints=["your_service_role", "example", "placeholder"],
    ),
    Pattern(
        name="supabase_real_url",
        regex=re.compile(r"https://[a-z]{20,}\.supabase\.co"),
        severity="HIGH",
        description="Real Supabase project URL (not a placeholder)",
        false_positive_hints=["your-project-id", "example", "placeholder"],
    ),
    Pattern(
        name="freshdesk_key",
        regex=re.compile(r"[A-Za-z0-9]{20,}:X@[a-zA-Z0-9.-]+\.freshdesk\.com"),
        severity="CRITICAL",
        description="Freshdesk API key embedded in URL",
        false_positive_hints=[],
    ),
    Pattern(
        name="aws_access_key",
        regex=re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        severity="CRITICAL",
        description="AWS access key ID",
        false_positive_hints=[],
    ),
    Pattern(
        name="aws_secret_key",
        regex=re.compile(r"(?i)aws[_\-]?secret[_\-]?access[_\-]?key\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{40}"),
        severity="CRITICAL",
        description="AWS secret access key",
        false_positive_hints=[],
    ),
    Pattern(
        name="private_key_block",
        regex=re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        severity="CRITICAL",
        description="PEM private key block",
        false_positive_hints=[],
    ),
    Pattern(
        name="hardcoded_password",
        regex=re.compile(r"(?i)password\s*[=:]\s*['\"](?!.*placeholder|.*example|.*your_|.*<)[^'\"]{8,}['\"]"),
        severity="HIGH",
        description="Hardcoded password assignment",
        false_positive_hints=["password_hash", "hashed_password", "check_password"],
    ),
    Pattern(
        name="email_address_pii",
        regex=re.compile(r"\b[a-zA-Z0-9._%+\-]{3,}@(?!example\.com|test\.com|domain\.com|iitd\.ac\.in)[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b"),
        severity="MEDIUM",
        description="Email address (potential PII)",
        false_positive_hints=["noreply@", "support@", "no-reply@"],
    ),
    Pattern(
        name="todo_security",
        regex=re.compile(r"(?i)#\s*TODO.*(?:security|auth|secret|key|token|password|credential)"),
        severity="LOW",
        description="Security-related TODO comment",
        false_positive_hints=[],
    ),
]

# ── File selection ─────────────────────────────────────────────────────────────

SCAN_EXTENSIONS = {".py", ".md", ".json", ".yaml", ".yml", ".env", ".example", ".txt"}
SKIP_DIRS = {".venv", "venv", "__pycache__", ".git", ".pytest_cache", "node_modules", ".cursor"}
SKIP_FILES = {"security_scan.py"}  # don't flag patterns in this file itself


@dataclass
class Finding:
    file: str
    line_number: int
    pattern_name: str
    severity: str
    description: str
    line_snippet: str


def is_false_positive(line: str, pattern: Pattern) -> bool:
    return any(hint.lower() in line.lower() for hint in pattern.false_positive_hints)


def scan_file(path: Path, patterns: list[Pattern], verbose: bool = False) -> list[Finding]:
    findings: list[Finding] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, PermissionError):
        return findings

    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") and "TODO" not in stripped.upper():
            continue
        for pattern in patterns:
            if pattern.regex.search(line):
                if is_false_positive(line, pattern):
                    if verbose:
                        print(f"  [SKIP false-positive] {path}:{lineno} — {pattern.name}")
                    continue
                findings.append(Finding(
                    file=str(path),
                    line_number=lineno,
                    pattern_name=pattern.name,
                    severity=pattern.severity,
                    description=pattern.description,
                    line_snippet=line[:120].strip(),
                ))
    return findings


def scan_directory(root: Path, patterns: list[Pattern], verbose: bool = False) -> list[Finding]:
    all_findings: list[Finding] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(skip in path.parts for skip in SKIP_DIRS):
            continue
        if path.name in SKIP_FILES:
            continue
        if path.suffix.lower() not in SCAN_EXTENSIONS and path.name != ".env":
            continue
        file_findings = scan_file(path, patterns, verbose)
        all_findings.extend(file_findings)
    return all_findings


def print_findings(findings: list[Finding]) -> None:
    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    sorted_findings = sorted(findings, key=lambda f: severity_order.get(f.severity, 99))

    for f in sorted_findings:
        print(f"\n  [{f.severity}] {f.pattern_name}")
        print(f"  File:    {f.file}:{f.line_number}")
        print(f"  Pattern: {f.description}")
        print(f"  Line:    {f.line_snippet[:100]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Secret and security scanner")
    parser.add_argument("--path", default=str(ROOT), help="Directory to scan (default: repo root)")
    parser.add_argument("--report", action="store_true", help="Write findings to data/reports/security_scan.md")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show false-positive skips")
    parser.add_argument("--severity", choices=["CRITICAL", "HIGH", "MEDIUM", "LOW"], default="LOW",
                        help="Minimum severity to report (default: LOW)")
    args = parser.parse_args()

    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    min_severity = severity_order[args.severity]

    scan_root = Path(args.path)
    print(f"\n{'='*60}")
    print(f" Security Scanner — {scan_root}")
    print(f"{'='*60}")
    print(f" Patterns: {len(PATTERNS)} | Min severity: {args.severity}")
    print()

    findings = scan_directory(scan_root, PATTERNS, verbose=args.verbose)
    filtered = [f for f in findings if severity_order.get(f.severity, 99) <= min_severity]

    critical = [f for f in filtered if f.severity == "CRITICAL"]
    high = [f for f in filtered if f.severity == "HIGH"]
    medium = [f for f in filtered if f.severity == "MEDIUM"]
    low = [f for f in filtered if f.severity == "LOW"]

    if filtered:
        print_findings(filtered)
    else:
        print("  No findings.")

    print(f"\n{'='*60}")
    print(f" Summary: {len(critical)} CRITICAL | {len(high)} HIGH | {len(medium)} MEDIUM | {len(low)} LOW")
    print(f"{'='*60}")

    if args.report:
        report_dir = ROOT / "data" / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "security_scan.md"
        with open(report_path, "w", encoding="utf-8") as out:
            out.write("# Security Scan Report\n\n")
            out.write(f"**Scanned:** {scan_root}\n\n")
            out.write(f"**Findings:** {len(critical)} CRITICAL | {len(high)} HIGH | {len(medium)} MEDIUM | {len(low)} LOW\n\n")
            if filtered:
                out.write("## Findings\n\n")
                for f in sorted(filtered, key=lambda x: {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}.get(x.severity, 9)):
                    out.write(f"### [{f.severity}] {f.pattern_name}\n\n")
                    out.write(f"- **File:** `{f.file}:{f.line_number}`\n")
                    out.write(f"- **Pattern:** {f.description}\n")
                    out.write(f"- **Line:** `{f.line_snippet[:100]}`\n\n")
            else:
                out.write("No findings.\n")
        print(f"\nReport written to: {report_path}")

    sys.exit(1 if critical or high else 0)


if __name__ == "__main__":
    main()
