"""Git CLI helpers. All calls run in a thread so the event loop stays responsive.

Network operations never fall back to ambient credentials: a push requires an
explicit GITHUB_TOKEN, so the agent cannot commit under a login cached in the
machine's credential manager.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import stat
import subprocess
from pathlib import Path

from .config import settings


def _git_env() -> dict[str, str]:
    """GIT_TERMINAL_PROMPT=0 makes git fail instead of asking for a login."""
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


def _force_remove(func, path, _exc) -> None:
    """git stores packfiles read-only on Windows; clear the bit and retry."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def prepare_workspace(dest: str) -> None:
    """Delete a stale clone so `git clone` can write into an empty path."""
    path = Path(dest)
    if not path.exists():
        return
    shutil.rmtree(path, ignore_errors=False, onexc=_force_remove)
    if path.exists():
        raise RuntimeError(
            f"Could not clear the previous workspace at {dest}. Close any program "
            "using those files (editor, terminal, antivirus) and start the job again."
        )


def _redact(text: str) -> str:
    for token in settings.tokens:
        if token and token in text:
            text = text.replace(token, "***")
    return text


async def run_cmd(
    args: list[str], cwd: str | Path | None = None, timeout: int = 60
) -> subprocess.CompletedProcess:
    def _sync() -> subprocess.CompletedProcess:
        return subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            errors="replace",
            env=_git_env(),
        )

    proc = await asyncio.to_thread(_sync)
    proc.stdout = _redact(proc.stdout or "")
    proc.stderr = _redact(proc.stderr or "")
    return proc


def _check(proc: subprocess.CompletedProcess, what: str) -> None:
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"{what} failed (exit {proc.returncode}):\n{detail[-600:]}")


def clone_url(owner: str, name: str) -> str:
    if settings.token:
        return f"https://x-access-token:{settings.token}@github.com/{owner}/{name}.git"
    return f"https://github.com/{owner}/{name}.git"


def add_local_excludes(repo_dir: str, patterns: list[str]) -> None:
    """Write to .git/info/exclude so generated dirs never enter the commit."""
    exclude = Path(repo_dir) / ".git" / "info" / "exclude"
    try:
        existing = exclude.read_text(encoding="utf-8", errors="replace") if exclude.exists() else ""
        missing = [p for p in patterns if p not in existing]
        if missing:
            with exclude.open("a", encoding="utf-8") as fh:
                for pattern in missing:
                    fh.write(f"\n{pattern}")
    except OSError:
        pass  # best effort


async def shallow_clone(owner: str, name: str, dest: str) -> None:
    await asyncio.to_thread(prepare_workspace, dest)
    args = [
        "git", "clone", "--depth", "1", "--single-branch", "--quiet",
        clone_url(owner, name), dest,
    ]
    proc = await run_cmd(args, timeout=settings.clone_timeout_s)
    _check(proc, f"git clone of {owner}/{name}")


async def create_branch(repo_dir: str, branch: str) -> None:
    proc = await run_cmd(["git", "checkout", "-q", "-b", branch], cwd=repo_dir, timeout=30)
    _check(proc, f"creating branch {branch}")


async def has_changes(repo_dir: str) -> bool:
    proc = await run_cmd(["git", "status", "--porcelain"], cwd=repo_dir, timeout=30)
    _check(proc, "git status")
    return bool(proc.stdout.strip())


async def current_branch(repo_dir: str) -> str:
    """Name of the checked-out branch ("main" for a freshly cloned empty repo)."""
    proc = await run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir, timeout=30)
    name = (proc.stdout or "").strip()
    return name if proc.returncode == 0 and name not in ("", "HEAD") else "main"


async def commit_all(repo_dir: str, message: str) -> None:
    proc = await run_cmd(["git", "add", "-A"], cwd=repo_dir, timeout=60)
    _check(proc, "git add")
    proc = await run_cmd(
        [
            "git",
            "-c", "user.name=Issue Solver Bot",
            "-c", "user.email=issue-solver@localhost",
            "commit", "-q", "-m", message,
        ],
        cwd=repo_dir,
        timeout=60,
    )
    _check(proc, "git commit")


async def push_branch(repo_dir: str, owner: str, name: str, branch: str) -> None:
    if not settings.token:
        raise RuntimeError(
            "No GitHub token is set, so the branch cannot be pushed and no pull request "
            "can be opened. Add GITHUB_CLASSIC_TOKEN (classic, `repo` scope) or "
            "GITHUB_TOKEN to backend/.env and run the issue again."
        )
    # An empty credential.helper overrides any login cached on this machine.
    proc = await run_cmd(
        [
            "git", "-c", "credential.helper=",
            "push", "--quiet", clone_url(owner, name), f"HEAD:refs/heads/{branch}",
        ],
        cwd=repo_dir,
        timeout=settings.clone_timeout_s,
    )
    _check(proc, f"pushing branch {branch}")
