r"""Offline end-to-end run of the agent graph.

Uses the real LangGraph wiring, real patcher, real git branch/commit and a real
pytest run against a throwaway repository. Only two things are faked: the LLM
(canned JSON) and every GitHub/git-network call. No API keys required and no
request ever leaves the machine.

Three submission routes are exercised: pushing to a repo you can write to,
forking somebody else's repo and opening a cross-repo PR, and a run that
cannot write at all (fix stays local). A fourth scenario covers PREFER_FORK
against your own repository, where GitHub returns the upstream instead of a
fork and the agent must fall back to a direct push without claiming a fork.
Seven extra assertions check the route decision itself, a group drives
LLMService directly (a retired model name must fall through to the next
candidate, and an exhausted chain must report every attempt), and a final
group checks that an unimportable test suite is reported as an environment
gap instead of a failing test.

    .\.venv\Scripts\python.exe tests_offline.py
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from app import github_api, gitops
from app.config import settings
from app.graph import build_solver_graph, resolve_submission
from app.jobs import Job
from app.state import AgentState

BUGGY = "def average(values):\n    return sum(values) / (len(values) + 1)\n"
RIGHT_FIX = "def average(values):\n    return sum(values) / len(values)\n"


def sh(args: list[str], cwd: str) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def make_fixture_repo(dest: str) -> None:
    """A tiny Python project whose only test fails until average() is fixed."""
    path = Path(dest)
    path.mkdir(parents=True, exist_ok=True)
    (path / "calculator.py").write_text(BUGGY, encoding="utf-8")
    (path / "requirements.txt").write_text("", encoding="utf-8")
    (path / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n\nsys.path.insert(0, str(Path(__file__).parent))\n",
        encoding="utf-8",
    )
    tests = path / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "test_average.py").write_text(
        "from calculator import average\n\n\ndef test_average():\n    assert average([2, 4]) == 3\n",
        encoding="utf-8",
    )
    sh(["git", "init", "-q", "-b", "main"], dest)
    sh(["git", "add", "-A"], dest)
    sh(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], dest)


class FakeLLM:
    """locate -> fix (wrong divisor) -> fix (repair), mirroring the real loop."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def invoke_json(self, system: str, user: str, schema):
        self.calls.append(schema.__name__)
        if schema.__name__ == "LocatePlan":
            payload = {
                "files": ["calculator.py"],
                "queries": ["average"],
                "reasoning": "the divisor in average() is off by one",
            }
        elif self.calls.count("FixPlan") == 1:
            # First attempt: a plausible but wrong edit -> tests must fail.
            payload = {
                "patches": [
                    {
                        "path": "calculator.py",
                        "action": "replace",
                        "old": "    return sum(values) / (len(values) + 1)",
                        "new": "    return sum(values) / (len(values) - 1)",
                    }
                ],
                "summary": "Adjust the divisor used by average().",
            }
        else:
            # Repair round, fed with the pytest failure output.
            payload = {
                "patches": [
                    {
                        "path": "calculator.py",
                        "action": "replace",
                        "old": "    return sum(values) / (len(values) - 1)",
                        "new": "    return sum(values) / len(values)",
                    }
                ],
                "summary": "Divide by the real element count.",
            }
        return schema.model_validate(payload), "fake"


def monkeypatch(
    fixture: str, *, viewer: str, push: bool, forkable: bool, fork_error: str | None = None
) -> dict:
    """Replace every network-facing helper, keeping git state real."""
    recorded: dict = {"pushed": [], "pr": {}, "forks": [], "cloned": []}

    async def fake_repo_info(owner, name):
        return {
            "default_branch": "main",
            "owner": {"login": owner},
            "permissions": {"push": push, "pull": True},
            "allow_forking": forkable,
        }

    async def fake_issue(owner, name, number):
        return {
            "title": "average() returns the wrong value",
            "body": "average([2, 4]) must be 3 but returns a smaller number.",
        }

    async def fake_comments(owner, name, number, limit: int = 5):
        return []

    async def fake_viewer():
        return viewer

    async def fake_fork(owner, name):
        recorded["forks"].append(f"{owner}/{name}")
        if fork_error:
            raise RuntimeError(fork_error)
        return viewer, name

    async def fake_clone(owner, name, dest):
        # Same cleanup the real shallow_clone performs, then a local copy.
        gitops.prepare_workspace(dest)
        shutil.copytree(fixture, dest, dirs_exist_ok=True)
        recorded["cloned"].append(f"{owner}/{name}")

    async def fake_push(repo_dir, owner, name, branch):
        recorded["pushed"].append({"repo": f"{owner}/{name}", "branch": branch})

    async def fake_pr(owner, name, *, title, body, head, base):
        url = f"https://github.com/{owner}/{name}/pull/1"
        recorded["pr"].update(
            title=title, body=body, head=head, base=base, repo=f"{owner}/{name}", url=url
        )
        return {"html_url": url, "number": 1}

    github_api.get_repo_info = fake_repo_info
    github_api.get_issue = fake_issue
    github_api.get_issue_comments = fake_comments
    github_api.get_viewer_login = fake_viewer
    github_api.fork_repo = fake_fork
    github_api.create_pull_request = fake_pr
    gitops.shallow_clone = fake_clone
    gitops.push_branch = fake_push
    return recorded


async def run_scenario(
    *, label: str, fixture: str, owner: str, viewer: str, push: bool, forkable: bool,
    expect_fork: bool, expect_submit: bool, prefer_fork: bool = False,
    fork_error: str | None = None,
) -> list[tuple[str, bool]]:
    """One full graph run against faked GitHub, returning its assertions."""
    fake = FakeLLM()
    recorded = monkeypatch(fixture, viewer=viewer, push=push, forkable=forkable, fork_error=fork_error)
    saved_prefer = settings.prefer_fork
    settings.prefer_fork = prefer_fork
    job = Job(
        id=f"{label}{int(time.time() * 1000) % 100000}",
        owner=owner,
        repo_name="calc",
        issue_number=7,
        repo_url=f"https://github.com/{owner}/calc",
    )
    try:
        graph = build_solver_graph(job, fake)
        final = await graph.ainvoke(
            AgentState(
                repo_url=job.repo_url,
                owner=owner,
                repo_name="calc",
                issue_number=7,
            )
        )

        final_code = (Path(job.workspace_dir) / "calculator.py").read_text(encoding="utf-8")
        commits = subprocess.run(
            ["git", "log", "--oneline"], cwd=job.workspace_dir, capture_output=True, text=True
        ).stdout.strip()
        diff_stat = subprocess.run(
            ["git", "show", "--stat", "--oneline", "HEAD"],
            cwd=job.workspace_dir, capture_output=True, text=True,
        ).stdout
        branch = final.get("branch", "")
        pr = recorded["pr"]

        checks: list[tuple[str, bool]] = [
            ("no state error", not final.get("error")),
            ("exactly one locate call", fake.calls.count("LocatePlan") == 1),
            ("bounded LLM calls (<=3)", len(fake.calls) <= 3),
            ("repair loop used", final.get("test_retries") == 1),
            ("tests passed", final.get("test_passed") is True),
            ("final code correct", final_code == RIGHT_FIX),
            ("commit created", "fix:" in commits),
            ("venv excluded from diff", ".venv" not in diff_stat),
        ]
        if expect_submit:
            fork_repo_name = f"{viewer}/calc"
            checks += [
                ("pushed once", len(recorded["pushed"]) == 1),
                ("pr url in state", final.get("pr_url") == pr.get("url")),
                ("pr body links issue", "Fixes #7" in pr.get("body", "")),
                ("pr reports passing tests", "**Tests:** passing" in pr.get("body", "")),
                ("pr targets the issue repo", pr.get("repo") == f"{owner}/calc"),
            ]
            if expect_fork:
                checks += [
                    ("upstream was forked", recorded["forks"] == [f"{owner}/calc"]),
                    ("fork was cloned", recorded["cloned"] == [fork_repo_name]),
                    ("branch pushed to the fork", recorded["pushed"][0]["repo"] == fork_repo_name),
                    ("pr head names the fork", pr.get("head") == f"{viewer}:{branch}"),
                    ("fork flag recorded in state", final.get("forked") is True),
                ]
            elif fork_error:
                # GitHub refused to fork a repo the account already owns: the run must
                # degrade to a direct push and must not claim it came from a fork.
                checks += [
                    ("fork was attempted once", recorded["forks"] == [f"{owner}/calc"]),
                    ("issue repo cloned", recorded["cloned"] == [f"{owner}/calc"]),
                    ("branch pushed to the issue repo", recorded["pushed"][0]["repo"] == f"{owner}/calc"),
                    ("pr head is a bare branch", pr.get("head") == branch),
                    ("state does not claim a fork", final.get("forked") is False),
                ]
            else:
                checks += [
                    ("no fork attempted", recorded["forks"] == []),
                    ("issue repo cloned", recorded["cloned"] == [f"{owner}/calc"]),
                    ("branch pushed to the issue repo", recorded["pushed"][0]["repo"] == f"{owner}/calc"),
                    ("pr head is a bare branch", pr.get("head") == branch),
                ]
        else:
            checks += [
                ("no fork attempted", recorded["forks"] == []),
                ("nothing pushed", recorded["pushed"] == []),
                ("no pull request opened", pr == {}),
                ("state reports no pr url", final.get("pr_url", "") == ""),
                ("fix still committed locally", "fix:" in commits),
            ]
        return checks
    finally:
        settings.prefer_fork = saved_prefer
        try:
            gitops.prepare_workspace(str(job.workspace_dir))
        except RuntimeError as exc:  # leftover scratch never fails the run
            print("cleanup warning:", exc)


def route_checks() -> list[tuple[str, bool]]:
    """The permission-based decision, without running the graph."""
    own = {"owner": {"login": "octo"}, "permissions": {"push": True}, "allow_forking": True}
    foreign_writable = {"owner": {"login": "other"}, "permissions": {"push": True}}
    foreign_readonly = {"owner": {"login": "other"}, "permissions": {"push": False}}
    fork_forbidden = {**foreign_readonly, "allow_forking": False}
    saved = (settings.allow_fork, settings.prefer_fork)
    try:
        settings.allow_fork, settings.prefer_fork = True, False
        results = [
            ("own repo -> direct", resolve_submission(own, "octo") == "direct"),
            ("foreign repo I can push -> direct", resolve_submission(foreign_writable, "octo") == "direct"),
            ("foreign read-only repo -> fork", resolve_submission(foreign_readonly, "octo") == "fork"),
            ("forking disabled -> preview", resolve_submission(fork_forbidden, "octo") == "preview"),
            ("no token -> preview", resolve_submission(foreign_readonly, None) == "preview"),
        ]
        settings.prefer_fork = True
        results.append(("PREFER_FORK forces the fork route", resolve_submission(own, "octo") == "fork"))
        settings.allow_fork = False
        results.append(("ALLOW_FORK=false keeps own repo direct", resolve_submission(own, "octo") == "direct"))
        return results
    finally:
        settings.allow_fork, settings.prefer_fork = saved


async def llm_checks() -> list[tuple[str, bool]]:
    """Drive LLMService's fallback list without building a chat client."""
    from app import llm as llm_mod
    from app.llm import LLMService

    class _Reply:
        def __init__(self, text: str) -> None:
            self.content = text

    class _Model:
        """Answers with canned text, or raises the fault a real provider would."""

        def __init__(self, outcome: object) -> None:
            self.outcome = outcome

        async def ainvoke(self, messages):
            if isinstance(self.outcome, BaseException):
                raise self.outcome
            return _Reply(str(self.outcome))

    def candidate(provider: str, model: str, outcome: object):
        return (provider, model, lambda: _Model(outcome))

    retired = RuntimeError(
        "Error calling model 'gemini-2.5-flash' (NOT_FOUND): 404 NOT_FOUND. "
        "This model is no longer available to new users."
    )
    busy = RuntimeError("503 This model is currently experiencing high demand")
    empty = llm_mod.LLMError("provider returned an empty response")

    results: list[tuple[str, bool]] = []
    saved_delay = llm_mod.TRANSIENT_RETRY_DELAY_S
    llm_mod.TRANSIENT_RETRY_DELAY_S = 0.0  # the retry pause is real; not worth waiting out
    try:
        svc = LLMService()
        svc.providers = lambda: [
            candidate("gemini", "gemini-2.5-flash", retired),
            candidate("gemini", "gemini-flash-lite-latest", '{"files": []}'),
        ]
        text, provider = await svc.invoke_text("sys", "user")
        results.append((
            "a retired model name falls through to the next model", text == '{"files": []}'
        ))
        results.append(("the surviving provider is reported", provider == "gemini"))

        svc.providers = lambda: [
            candidate("openrouter", "mm:free", empty),
            candidate("gemini", "gemini-flash-lite-latest", busy),
        ]
        try:
            await svc.invoke_text("sys", "user")
            results.append(("an exhausted chain raises", False))
            message = ""
        except llm_mod.LLMError as exc:
            message = str(exc)
        results.append(("an exhausted chain raises", message.startswith("All 3 LLM attempts")))
        results.append((
            "the error names every attempt",
            "empty response" in message and "high demand" in message,
        ))

        # The real candidate list, built from the configured keys only.
        live = LLMService()
        configured = {
            name
            for name, present in (
                ("openrouter", bool(settings.openrouter_api_key)),
                ("gemini", bool(settings.gemini_api_key)),
            )
            if present
        }
        offered = {name for name, _, _ in live.providers()}
        results.append(("configured providers are offered", offered == configured))
        results.append((
            "every candidate names a model",
            all(model for _, model, _ in live.providers()),
        ))
        live._primary_cooldown_until = time.time() + 60
        results.append((
            "a rate-limited primary is skipped while it cools down",
            {name for name, _, _ in live.providers()} == configured - {"openrouter"},
        ))
        return results
    finally:
        llm_mod.TRANSIENT_RETRY_DELAY_S = saved_delay


def env_checks() -> list[tuple[str, bool]]:
    """A suite that cannot start is an environment gap, never a failing test."""
    from app.testrunner import _environment_fault, _fault_result, _present_req_files

    results = [
        (
            "the missing module is named",
            _environment_fault(
                "\nImportError while loading conftest "
                "'C:/workspace/1392ce7e561a/tests/conftest.py'.\n"
                "tests\\__init__.py:2: in <module>\n    import betamax\n"
                "E   ModuleNotFoundError: No module named 'betamax'\n"
            )
            == "betamax",
        ),
        (
            "a conftest import error is an environment fault",
            _environment_fault("ImportError while loading conftest 'C:/w/tests/conftest.py'.")
            == "a test dependency",
        ),
        (
            "a node module gap is caught too",
            _environment_fault("Error: Cannot find module 'jest'") == "jest",
        ),
        (
            "a real assertion failure stays a test failure",
            _environment_fault("assert average([2, 4]) == 3\n1 failed in 0.02s") is None,
        ),
        (
            "an import raised inside a collected test stays a test failure",
            _environment_fault(
                "def test_load():\n>       import calculator\nE       ModuleNotFoundError: "
                "No module named 'calculator'\n1 failed\n"
            )
            is None,
        ),
        (
            "a relative node import is the patch's problem",
            _environment_fault("Error: Cannot find module './src/calc'") is None,
        ),
        (
            "passing output reports no fault",
            _environment_fault("4 passed in 0.11s") is None,
        ),
    ]
    fault = _fault_result("python -m pytest -x -q", "ModuleNotFoundError", "betamax")
    results.append(("the fault is reported as skipped, never as failed",
                    fault["skipped"] is True and fault["passed"] is None))
    results.append(("the fault names the suite command", fault["command"] == "python -m pytest -x -q"))

    scratch = Path(tempfile.mkdtemp(prefix="issuesolver-reqs-"))
    try:
        for name in ("requirements.txt", "dev-requirements.txt", "README.md"):
            (scratch / name).write_text("", encoding="utf-8")
        results.append((
            "test requirement files are picked up alongside the base one",
            _present_req_files(str(scratch)) == ["requirements.txt", "dev-requirements.txt"],
        ))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return results


async def main() -> int:
    scratch = Path(tempfile.mkdtemp(prefix="issuesolver-"))
    fixture = str(scratch / "fixture-repo")
    make_fixture_repo(fixture)

    scenarios = [
        # (label, issue owner, viewer, push right, forkable, expect_fork, expect_submit,
        #  prefer_fork, fork_error)
        ("direct", "octo", "octo", True, True, False, True, False, None),
        ("forked", "someone", "octo", False, True, True, True, False, None),
        ("preview", "someone", "", False, True, False, False, False, None),
        # PREFER_FORK against your own repo: GitHub hands back the upstream instead of a
        # fork, so the run must fall back to a direct push and stop claiming a fork.
        (
            "selffork",
            "octo", "octo", True, True, False, True, True,
            "GitHub returned octo/calc for the fork request, which is the upstream itself.",
        ),
    ]
    ok = True
    try:
        for (
            label, owner, viewer, push, forkable, expect_fork, expect_submit, prefer_fork, fork_error
        ) in scenarios:
            print(f"\n=== scenario: {label} ===")
            checks = await run_scenario(
                label=label, fixture=fixture, owner=owner, viewer=viewer, push=push,
                forkable=forkable, expect_fork=expect_fork, expect_submit=expect_submit,
                prefer_fork=prefer_fork, fork_error=fork_error,
            )
            for name, passed in checks:
                print(f"{'PASS' if passed else 'FAIL'}  {name}")
                ok = ok and passed
        print("\n=== route decisions ===")
        for name, passed in route_checks():
            print(f"{'PASS' if passed else 'FAIL'}  {name}")
            ok = ok and passed
        print("\n=== llm fallback ===")
        for name, passed in await llm_checks():
            print(f"{'PASS' if passed else 'FAIL'}  {name}")
            ok = ok and passed
        print("\n=== test environment ===")
        for name, passed in env_checks():
            print(f"{'PASS' if passed else 'FAIL'}  {name}")
            ok = ok and passed
        return 0 if ok else 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
