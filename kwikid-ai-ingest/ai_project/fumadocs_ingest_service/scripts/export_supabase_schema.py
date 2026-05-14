from __future__ import annotations

import os
import shlex
import subprocess
import shutil
from pathlib import Path
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:  # noqa: BLE001
    load_dotenv = None


def _load_env_file_fallback() -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


if load_dotenv:
    load_dotenv()
else:
    _load_env_file_fallback()


def _env(name: str) -> str | None:
    v = os.getenv(name)
    if v is None:
        return None
    v = v.strip()
    return v or None


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def _resolve_pg_dump() -> str:
    direct = shutil.which("pg_dump")
    if direct:
        return direct
    # Homebrew libpq path on Apple Silicon/macOS.
    candidate = Path("/opt/homebrew/opt/libpq/bin/pg_dump")
    if candidate.exists():
        return str(candidate)
    # Homebrew libpq path on Intel/macOS.
    candidate = Path("/usr/local/opt/libpq/bin/pg_dump")
    if candidate.exists():
        return str(candidate)
    return "pg_dump"


def _build_db_url_from_env() -> str | None:
    """
    Build SUPABASE_DB_URL from .env-style values.
    Requires at minimum:
      - SUPABASE_URL
      - SUPABASE_DB_PASSWORD
    Optional:
      - SUPABASE_DB_USER (default: postgres)
      - SUPABASE_DB_NAME (default: postgres)
      - SUPABASE_DB_PORT (default: 5432)
    """
    supabase_url = _env("SUPABASE_URL")
    db_password = _env("SUPABASE_DB_PASSWORD")
    if not supabase_url or not db_password:
        return None

    parsed = urlparse(supabase_url)
    host = parsed.hostname or ""
    # Expected: <project_ref>.supabase.co
    if not host.endswith(".supabase.co"):
        return None
    project_ref = host.split(".")[0]
    if not project_ref:
        return None

    db_user = _env("SUPABASE_DB_USER") or "postgres"
    db_name = _env("SUPABASE_DB_NAME") or "postgres"
    db_port = _env("SUPABASE_DB_PORT") or "5432"
    db_host = f"db.{project_ref}.supabase.co"
    return f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"


def main() -> int:
    """
    Export Supabase Postgres schema to a SQL file.

    IMPORTANT LIMITATION:
    - Supabase REST (SUPABASE_URL + service_role key) does not provide full DDL/schema.
    - You must use ONE of:
      (A) SUPABASE_DB_URL (Postgres connection string) -> uses pg_dump
      (B) Supabase CLI with SUPABASE_ACCESS_TOKEN + SUPABASE_PROJECT_REF -> uses `supabase db dump`
    """

    out_path = Path(_env("OUT_FILE") or "./supabase_schema.sql").resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    db_url = _env("SUPABASE_DB_URL") or _build_db_url_from_env()
    if db_url:
        # Requires pg_dump installed.
        cmd = [
            _resolve_pg_dump(),
            "--schema-only",
            "--no-owner",
            "--no-privileges",
            "--if-exists",
            "--clean",
            "--file",
            str(out_path),
            db_url,
        ]
        print(f"[export] Using pg_dump -> {out_path}")
        print(f"[export] Running: {shlex.join(cmd[:-1] + ['<SUPABASE_DB_URL>'])}")
        _run(cmd)
        return 0

    access_token = _env("SUPABASE_ACCESS_TOKEN")
    project_ref = _env("SUPABASE_PROJECT_REF")
    if access_token and project_ref:
        # Requires supabase CLI installed and authenticated via env var.
        env = dict(os.environ)
        env["SUPABASE_ACCESS_TOKEN"] = access_token

        cmd = [
            "supabase",
            "db",
            "dump",
            "--project-ref",
            project_ref,
            "--schema-only",
        ]
        print(f"[export] Using Supabase CLI -> {out_path}")
        print(f"[export] Running: {shlex.join(cmd)} > {out_path}")
        with out_path.open("w", encoding="utf-8") as f:
            subprocess.run(cmd, check=True, env=env, stdout=f)
        return 0

    print("Cannot export schema with only SUPABASE_URL + SUPABASE_KEY.")
    print("")
    print("Provide one of the following:")
    print("  - SUPABASE_DB_URL=postgresql://... (then re-run)")
    print("  - SUPABASE_URL + SUPABASE_DB_PASSWORD in .env (auto-build DB URL)")
    print("  - SUPABASE_ACCESS_TOKEN=... and SUPABASE_PROJECT_REF=... (Supabase CLI)")
    print("")
    print("Example (pg_dump):")
    print("  SUPABASE_DB_URL='postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres' \\")
    print("  OUT_FILE=./sql/current_schema.sql \\")
    print("  python scripts/export_supabase_schema.py")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

