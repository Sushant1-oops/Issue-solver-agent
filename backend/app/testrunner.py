"""Detect the repository's test setup and run tests with hard timeouts.

Everything is best-effort: installs and runs are bounded so a heavy repo can
never blow the 2-3 minute job budget. Unrecognized projects skip testing.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from .config import settings
from .gitops import add_local_excludes, run_cmd

OUTPUT_TAIL = 3500

PY_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")

# Test dependencies usually live somewhere other than requirements.txt, and an
# unimportable suite is an environment gap rather than a failing test.
REQ_FILES = (
    "requirements.txt",
    "dev-requirements.txt",
    "requirements-dev.txt",
    "test-requirements.txt",
    "requirements-test.txt",
    "requirements/dev.txt",
    "requirements/tests.txt",
    "requirements/test.txt",
)

_MISSING_MODULE = re.compile(
    r"(?:ModuleNotFoundError: No module named|Cannot find module)\s+'([^']+)'",
    re.IGNORECASE,
)
# pytest phrases these only when a test module could not even be imported, which
# is an environment gap. An error raised inside a test that did run stays a real
# failure, so nothing else here may classify a plain traceback as a fault.
_IMPORT_FAULTS = (
    "ImportError while loading conftest",
    "ImportError while importing test module",
    "errors during collection",
    "ERROR collecting",
)


def _present_req_files(repo_dir: str) -> list[str]:
    return [relative for relative in REQ_FILES if (Path(repo_dir) / relative).exists()]


def _environment_fault(output: str) -> str | None:
    """Name what the suite could not import, or None when the tests genuinely ran."""
    text = output or ""
    match = _MISSING_MODULE.search(text)
    module = match.group(1) if match else ""
    if any(marker in text for marker in _IMPORT_FAULTS):
        return module or "a test dependency"
    # node names missing dependencies bare and the repository's own files by path,
    # so a dotted or rooted name points at the patch rather than the environment.
    if module and "Cannot find module" in text and not module.startswith((".", "/", "\\")):
        return module
    return None


def _fault_result(command: str, output: str, fault: str) -> dict:
    return {
        "command": command,
        "output": output,
        "passed": None,
        "skipped": True,
        "environment_fault": fault,
        "reason": (
            f"The suite could not be imported (missing: {fault}), so this fix is "
            "unverified by the repository's own tests."
        ),
    }


def _tail(text: str) -> str:
    text = text or ""
    return text[-OUTPUT_TAIL:] if len(text) > OUTPUT_TAIL else text


async def _try_cmd(args: list[str], cwd: str, timeout: int) -> subprocess.CompletedProcess:
    try:
        return await run_cmd(args, cwd=cwd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args, returncode=124, stdout="", stderr=f"[command timed out after {timeout}s]"
        )
    except (FileNotFoundError, OSError) as exc:
        return subprocess.CompletedProcess(
            args, returncode=127, stdout="", stderr=f"[ unavailable: {exc} ]"
        )


def _npm_prefix(args: list[str]) -> list[str]:
    """npm is a .cmd shim on Windows; run it through cmd.exe."""
    if os.name == "nt":
        return ["cmd", "/c", *args]
    return args


def _js_test_command(repo_dir: str) -> str | None:
    """Return the npm test command if the repo defines a meaningful one."""
    pkg = Path(repo_dir) / "package.json"
    if not pkg.exists():
        return None
    try:
        data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return None
    script = (data.get("scripts") or {}).get("test")
    if not isinstance(script, str) or not script.strip():
        return None
    if "no test specified" in script:
        return None
    return "npm test"


def _is_python_project(repo_dir: str) -> bool:
    p = Path(repo_dir)
    if any((p / m).exists() for m in PY_MARKERS):
        return True
    # Bounded check: only look inside conventional test directories.
    for tests_dir in ("tests", "test"):
        d = p / tests_dir
        if d.is_dir() and any(d.rglob("test_*.py")):
            return True
    return False


def _venv_python(repo_dir: str) -> str:
    if os.name == "nt":
        return str(Path(repo_dir) / ".venv" / "Scripts" / "python.exe")
    return str(Path(repo_dir) / ".venv" / "bin" / "python")


async def _ensure_venv(repo_dir: str) -> str | None:
    """Create an isolated venv inside the clone (kept out of git via info/exclude)."""
    add_local_excludes(repo_dir, [".venv/", "node_modules/"])
    py = _venv_python(repo_dir)
    if not Path(py).exists():
        proc = await _try_cmd([sys.executable, "-m", "venv", ".venv"], repo_dir, 90)
        if proc.returncode != 0:
            return None
    return py if Path(py).exists() else None


async def _run_python_tests(repo_dir: str) -> dict:
    venv_py = await _ensure_venv(repo_dir)
    if not venv_py:
        return {
            "command": "pytest",
            "output": "Could not create a virtualenv for the repository; tests skipped.",
            "passed": None,
            "skipped": True,
        }

    req_files = _present_req_files(repo_dir)
    if req_files:
        # One pip invocation for every requirements file: a single resolver pass is
        # far cheaper against the install deadline than one call per file.
        args = [venv_py, "-m", "pip", "install", "-q", "--disable-pip-version-check"]
        for relative in req_files:
            args += ["-r", relative]
        await _try_cmd(args, repo_dir, settings.install_timeout_s)  # best effort

    check = await _try_cmd([venv_py, "-c", "import pytest"], repo_dir, 30)
    if check.returncode != 0:
        await _try_cmd(
            [venv_py, "-m", "pip", "install", "-q", "--disable-pip-version-check", "pytest"],
            repo_dir,
            settings.install_timeout_s,
        )
        check = await _try_cmd([venv_py, "-c", "import pytest"], repo_dir, 30)
    if check.returncode != 0:
        return {
            "command": "pytest",
            "output": "pytest is not available inside the repository environment; tests skipped.",
            "passed": None,
            "skipped": True,
        }

    command = "python -m pytest -x -q"
    proc = await _try_cmd(
        [venv_py, "-m", "pytest", "-x", "-q"], repo_dir, settings.test_timeout_s
    )
    output = _tail((proc.stdout or "") + "\n" + (proc.stderr or ""))

    if proc.returncode == 5:  # no tests collected
        return {
            "command": command,
            "output": (output or "").strip() + "\npytest collected no tests; treated as skipped.",
            "passed": None,
            "skipped": True,
        }
    if proc.returncode != 0:
        fault = _environment_fault(output)
        if fault:
            return _fault_result(command, output, fault)
    return {"command": command, "output": output, "passed": proc.returncode == 0, "skipped": False}


async def _run_js_tests(repo_dir: str) -> dict:
    if not (Path(repo_dir) / "node_modules").exists():
        install = ["npm", "ci"] if (Path(repo_dir) / "package-lock.json").exists() else ["npm", "install", "--no-audit", "--no-fund"]
        proc = await _try_cmd(
            _npm_prefix([*install, "--loglevel=error"]), repo_dir, settings.install_timeout_s
        )
        if proc.returncode != 0:
            return {
                "command": "npm test",
                "output": "Dependency install failed or timed out; tests skipped.\n"
                          + _tail((proc.stdout or "") + (proc.stderr or "")),
                "passed": None,
                "skipped": True,
            }

    proc = await _try_cmd(_npm_prefix(["npm", "test", "--silent"]), repo_dir, settings.test_timeout_s)
    output = _tail((proc.stdout or "") + "\n" + (proc.stderr or ""))
    if proc.returncode != 0:
        fault = _environment_fault(output)
        if fault:
            return _fault_result("npm test", output, fault)
    return {"command": "npm test", "output": output, "passed": proc.returncode == 0, "skipped": False}


async def detect_and_run(repo_dir: str, changed_files: list[str] | None = None) -> dict:
    """Pick a test framework, preferring the language of the patched files."""
    changed = changed_files or []
    py_hit = any(f.endswith(".py") for f in changed)
    js_hit = any(f.endswith((".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")) for f in changed)

    has_js = _js_test_command(repo_dir) is not None
    has_py = _is_python_project(repo_dir)

    if py_hit and has_py:
        return await _run_python_tests(repo_dir)
    if js_hit and has_js:
        return await _run_js_tests(repo_dir)
    if has_py:
        return await _run_python_tests(repo_dir)
    if has_js:
        return await _run_js_tests(repo_dir)

    return {
        "command": None,
        "output": "No pytest or npm test setup detected in this repository; tests skipped.",
        "passed": None,
        "skipped": True,
    }
