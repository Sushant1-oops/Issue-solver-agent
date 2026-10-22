r"""Seed (or re-seed) a throwaway repository with one bug, failing tests and an issue.

Useful for trying the agent end to end without touching a real project. The repo
must already exist and the GITHUB_TOKEN in backend/.env needs Contents + Issues
write access (fine-grained tokens cannot create repositories).

    .\.venv\Scripts\python.exe scripts\seed_demo_repo.py [owner/repo]

Pushing resets the target repo's default branch back to the buggy state, so it is
safe to run again before each demo.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import gitops  # noqa: E402
from app.config import settings  # noqa: E402

CALCULATOR = '''"""Temperature helpers used by the demo dashboard."""


def celsius_to_fahrenheit(celsius: float) -> float:
    """Convert a temperature from Celsius to Fahrenheit."""
    return celsius * 9 / 5 - 32


def average(values):
    """Return the arithmetic mean of a list of numbers."""
    if not values:
        return 0.0
    total = 0
    for value in values:
        total += value
    return total / len(values)
'''

CONFTEST = """import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
"""

TEST = """from calculator import average, celsius_to_fahrenheit


def test_boiling_point():
    assert celsius_to_fahrenheit(100) == 212


def test_zero_celsius():
    assert celsius_to_fahrenheit(0) == 32


def test_average():
    assert average([2, 4, 6]) == 4
"""

FILES = {
    "calculator.py": CALCULATOR,
    "conftest.py": CONFTEST,
    "requirements.txt": "# no runtime deps; the agent installs pytest itself\n",
    "tests/test_calculator.py": TEST,
    ".gitignore": "__pycache__/\n.pytest_cache/\n.venv/\n",
    "README.md": "# demo\n\nOne seeded bug (`celsius_to_fahrenheit`) and a failing test.\n",
}

ISSUE = {
    "title": "celsius_to_fahrenheit() is 64 degrees off",
    "body": (
        "The converter returns the wrong value.\r\n\r\n"
        "```\r\n>>> celsius_to_fahrenheit(0)\r\n-32\r\n"
        ">>> celsius_to_fahrenheit(100)\r\n148\r\n```\r\n\r\n"
        "Expected the standard formula `C * 9/5 + 32`, so `0` should give `32` and "
        "`100` should give `212`.\r\n\r\n"
        "`tests/test_calculator.py` covers both cases and fails on `main`."
    ),
}


async def seed(owner: str, name: str) -> None:
    dest = str(Path(__file__).resolve().parent.parent / "workspace" / f"seed-{name}")
    try:
        await gitops.shallow_clone(owner, name, dest)
        for rel, body in FILES.items():
            target = Path(dest) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        await gitops.commit_all(dest, "seed: calculator with a wrong offset plus failing tests")
        branch = await gitops.current_branch(dest)
        await gitops.push_branch(dest, owner, name, branch)
        print(f"seeded {owner}/{name} on {branch}")
    finally:
        gitops.prepare_workspace(dest)


def create_issue(owner: str, name: str) -> tuple[int, str]:
    headers = {"Authorization": f"Bearer {settings.token}", "Accept": "application/vnd.github+json"}
    url = f"https://api.github.com/repos/{owner}/{name}/issues"
    existing = httpx.get(f"{url}?state=open&per_page=1", headers=headers, timeout=60).json()
    if isinstance(existing, list) and existing:
        issue = existing[0]
        print(f"reusing issue #{issue['number']}")
        return issue["number"], issue["html_url"]
    res = httpx.post(url, headers=headers, json=ISSUE, timeout=60)
    res.raise_for_status()
    data = res.json()
    return data["number"], data["html_url"]


async def main() -> int:
    if len(sys.argv) > 1:
        owner, _, name = sys.argv[1].partition("/")
    else:
        owner, name = "Sushant1-oops", "issue-solver-demo"
    if not settings.token:
        print("A GitHub token is required (Contents + Issues write).")
        return 1
    await seed(owner, name)
    number, url = create_issue(owner, name)
    print(f"issue #{number}: {url}")
    print(f"NEXT: POST /api/jobs {{'repo_url':'https://github.com/{owner}/{name}','issue_number':{number}}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
