from __future__ import annotations

import subprocess
from pathlib import Path


def _run_git(args: list[str], cwd: Path | None = None, *, timeout_s: int = 120) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_s,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {result.returncode}): {err or 'no output'}"
        ) from None
    return result.stdout.strip()


def sync_repo(repo_url: str, repo_branch: str, local_path: str, *, command_timeout_s: int = 120) -> tuple[Path, str]:
    repo_path = Path(local_path).resolve()
    repo_path.parent.mkdir(parents=True, exist_ok=True)

    if not repo_path.exists():
        _run_git(["clone", "--branch", repo_branch, "--single-branch", repo_url, str(repo_path)], timeout_s=command_timeout_s)
    else:
        _run_git(["fetch", "origin"], cwd=repo_path, timeout_s=command_timeout_s)
        _run_git(["checkout", repo_branch], cwd=repo_path, timeout_s=command_timeout_s)
        _run_git(["pull", "--ff-only", "origin", repo_branch], cwd=repo_path, timeout_s=command_timeout_s)

    commit_sha = _run_git(["rev-parse", "HEAD"], cwd=repo_path, timeout_s=command_timeout_s)
    return repo_path, commit_sha

