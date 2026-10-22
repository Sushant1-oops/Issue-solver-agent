"""LangGraph agent: issue -> clone -> index -> locate -> read -> fix -> test -> PR.

The graph is linear with two bounded loops:
  * apply_fix retries once when no patch applied cleanly (repair context included)
  * run_tests -> apply_fix retries once when tests fail
Only 2-3 LLM calls happen per job to stay inside free-tier rate limits.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from . import github_api, gitops
from .codebase import (
    build_repo_map,
    list_text_files,
    read_file_capped,
    resolve_candidate,
    search_files,
)
from .config import settings
from .llm import LLMService
from .patcher import apply_patches
from .prompts import FIX_SYSTEM, LOCATE_SYSTEM
from .schemas import FixPlan, LocatePlan
from .state import AgentState
from .testrunner import detect_and_run

log = logging.getLogger("issue_solver.agent")

REPO_MAP_PROMPT_CAP = 6000
SEARCH_PROMPT_CAP = 5000
PATHS_PROMPT_CAP = 2500
FILE_CONTENTS_PROMPT_CAP = 40000
MAX_PATCHES_PER_ROUND = 8


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... [truncated]"


def _can_write(repo: dict, viewer: str | None) -> bool:
    """Whether this account may push to the repository (own repos always can)."""
    if not viewer:
        return False
    permissions = repo.get("permissions") or {}
    login = ((repo.get("owner") or {}).get("login") or "").lower()
    return bool(permissions.get("push")) or (bool(login) and login == viewer.lower())


def resolve_submission(repo: dict, viewer: str | None) -> str:
    """Decide how the fix can be submitted, before any LLM call is spent.

    "direct"  - the account can push straight to the issue's repository
    "fork"    - it cannot, so fork the repository and open a cross-repo PR
    "preview" - nothing can be submitted; still produce and show the fix
    """
    if not viewer:
        return "preview"
    writable = _can_write(repo, viewer)
    if writable and not settings.prefer_fork:
        return "direct"
    # No push right (or PREFER_FORK for a self-test): fork unless that is disallowed.
    if settings.allow_fork and repo.get("allow_forking", True):
        return "fork"
    return "direct" if writable else "preview"


def _issue_block(state: AgentState) -> str:
    parts = [f"ISSUE #{state.get('issue_number', '?')}: {state.get('issue_title', '')}"]
    body = state.get("issue_body") or ""
    if body:
        parts += ["", body]
    comments = state.get("issue_comments") or []
    if comments:
        parts += ["", "ISSUE COMMENTS:"]
        for idx, comment in enumerate(comments, start=1):
            parts.append(f"({idx}) {comment[:700]}")
    return "\n".join(parts)


def build_solver_graph(job: Any, llm: LLMService):
    """Compile a graph bound to one job so nodes can stream progress events."""
    emit: Callable[[str, dict], None] = job.emit

    # ---------------------------------------------------------- nodes

    async def fetch_issue(state: AgentState) -> dict:
        emit("step", {"name": "fetch_issue", "status": "running"})
        owner, name, number = state["owner"], state["repo_name"], state["issue_number"]
        repo, issue, comments = await asyncio.gather(
            github_api.get_repo_info(owner, name),
            github_api.get_issue(owner, name, number),
            github_api.get_issue_comments(owner, name, number),
        )
        title = (issue.get("title") or "").strip()
        body = _clip((issue.get("body") or "").strip(), settings.issue_body_max_chars)
        comment_texts = [
            (c.get("body") or "").strip()[:800] for c in comments[:5] if (c.get("body") or "").strip()
        ]
        default_branch = repo.get("default_branch") or "main"
        viewer = await github_api.get_viewer_login() if settings.token else None
        mode = resolve_submission(repo, viewer)
        emit(
            "log",
            {
                "message": {
                    "direct": f"Submission plan: push a branch straight to {owner}/{name} as {viewer}.",
                    "fork": (
                        f"No write access to {owner}/{name}; plan is to fork it into {viewer} "
                        "and open the pull request from the fork."
                    ),
                    "preview": (
                        "No usable GitHub token, or this repository cannot be pushed to or "
                        "forked: the fix is still produced and tested locally, but no pull "
                        "request can be opened."
                    ),
                }[mode]
            },
        )
        emit(
            "issue",
            {
                "title": title,
                "number": number,
                "url": f"https://github.com/{owner}/{name}/issues/{number}",
                "repository": f"{owner}/{name}",
                "default_branch": default_branch,
            },
        )
        emit("log", {"message": f"Read issue #{number}: {title[:160]}"})
        emit("log", {"message": f"Base branch: {default_branch}, comments used: {len(comment_texts)}"})
        emit("step", {"name": "fetch_issue", "status": "done"})
        return {
            "issue_title": title,
            "issue_body": body,
            "issue_comments": comment_texts,
            "default_branch": default_branch,
            "submit_mode": mode,
            # Where a failed fork leaves the run: push directly if we may, else preview.
            "submit_fallback": "direct" if _can_write(repo, viewer) else "preview",
            "push_owner": owner,
            "push_name": name,
        }

    async def clone_repo(state: AgentState) -> dict:
        emit("step", {"name": "clone_repo", "status": "running"})
        dest = str(job.workspace_dir)
        push_owner, push_name = state["owner"], state["repo_name"]
        forked = False
        submit_mode = state.get("submit_mode") or "direct"

        if submit_mode == "fork":
            # Forking is async; fork_repo() waits until the repo can be cloned.
            try:
                push_owner, push_name = await github_api.fork_repo(
                    state["owner"], state["repo_name"]
                )
                forked = True
                emit("log", {"message": f"Forked into {push_owner}/{push_name}; the PR will target "
                                        f"{state['owner']}/{state['repo_name']}."})
            except Exception as exc:  # noqa: BLE001 - keep producing the fix, report why PR fails
                submit_mode = state.get("submit_fallback") or "preview"
                if submit_mode == "direct":
                    emit(
                        "log",
                        {"message": f"No separate fork was created ({str(exc)[:220]}). Falling back "
                                    f"to pushing the branch directly to "
                                    f"{state['owner']}/{state['repo_name']}."},
                    )
                else:
                    emit(
                        "log",
                        {"message": f"Fork failed ({str(exc)[:200]}). The fix is still produced and "
                                    "tested locally, but no pull request can be opened."},
                    )

        # shallow_clone clears any stale clone itself (read-only git objects included).
        await gitops.shallow_clone(push_owner, push_name, dest)
        gitops.add_local_excludes(dest, [".venv/", "node_modules/"])
        branch = f"ai-fix/issue-{state['issue_number']}-{job.id[:8]}"
        await gitops.create_branch(dest, branch)
        emit("log", {"message": "Shallow clone complete (depth 1, single branch)."})
        emit("log", {"message": f"Working branch created: {branch}"})
        if submit_mode == "preview":
            emit(
                "log",
                {
                    "message": "Warning: this run cannot write to GitHub - the fix will be written "
                    "and tested, but no pull request will be opened."
                },
            )
        emit("step", {"name": "clone_repo", "status": "done"})
        return {
            "repo_dir": dest,
            "branch": branch,
            "submit_mode": submit_mode,
            "push_owner": push_owner,
            "push_name": push_name,
            "forked": forked,
        }

    async def map_repo(state: AgentState) -> dict:
        emit("step", {"name": "map_repo", "status": "running"})
        repo_map = await asyncio.to_thread(build_repo_map, state["repo_dir"])
        first_line = repo_map.splitlines()[0] if repo_map else "no files"
        emit("log", {"message": f"Indexed codebase: {first_line}"})
        emit("step", {"name": "map_repo", "status": "done"})
        return {"repo_map": repo_map}

    async def locate(state: AgentState) -> dict:
        emit("step", {"name": "locate", "status": "running"})
        user = (
            _issue_block(state)
            + "\n\nREPOSITORY FILE MAP:\n"
            + _clip(state.get("repo_map", ""), REPO_MAP_PROMPT_CAP)
        )
        plan, provider = await llm.invoke_json(LOCATE_SYSTEM, user, LocatePlan)
        files = [f for f in plan.files if f][:6]
        queries = [q for q in plan.queries if q][:3]
        emit("log", {"message": f"[{provider}] Candidate files: {', '.join(files) or '(none)'}"})
        if plan.reasoning:
            emit("log", {"message": f"Locator reasoning: {_clip(plan.reasoning, 400)}"})
        emit("step", {"name": "locate", "status": "done"})
        return {"candidate_files": files, "search_queries": queries}

    async def gather(state: AgentState) -> dict:
        emit("step", {"name": "gather", "status": "running"})
        repo_dir = state["repo_dir"]
        files = await asyncio.to_thread(list_text_files, repo_dir)

        search_blobs: list[str] = []
        for query in state.get("search_queries", []):
            blob = await asyncio.to_thread(search_files, repo_dir, files, query)
            search_blobs.append(f"QUERY /{query}/\n{blob}")
        search_results = _clip("\n\n".join(search_blobs), SEARCH_PROMPT_CAP + 1000)

        # Candidate paths first, then files that showed up in search hits.
        resolved: list[str] = []
        for cand in state.get("candidate_files", []):
            real = await asyncio.to_thread(resolve_candidate, cand, files)
            if real and real not in resolved:
                resolved.append(real)
        for line in search_results.splitlines():
            if ":" in line:
                path = line.split(":", 1)[0]
                if path in files and path not in resolved:
                    resolved.append(path)
        to_read = resolved[: settings.max_files_read]

        contents: dict[str, str] = {}
        originals: dict[str, str] = {}
        for rel in to_read:
            text = await asyncio.to_thread(read_file_capped, repo_dir, rel)
            contents[rel] = text
            originals[rel] = text

        emit(
            "log",
            {"message": f"Loaded {len(to_read)} file(s) into context: {', '.join(to_read) or 'none'}"},
        )
        if not to_read:
            emit("log", {"message": "Warning: no candidate files resolved; the model may only create new files."})
        emit("step", {"name": "gather", "status": "done"})
        return {
            "file_contents": contents,
            "original_contents": originals,
            "search_results": search_results,
        }

    async def apply_fix(state: AgentState) -> dict:
        emit("step", {"name": "apply_fix", "status": "running"})
        attempts = state.get("apply_attempts", 0) + 1
        user = _fix_user_prompt(state)
        plan, provider = await llm.invoke_json(FIX_SYSTEM, user, FixPlan)

        applied, failed = await asyncio.to_thread(
            apply_patches, state["repo_dir"], plan.patches[:MAX_PATCHES_PER_ROUND]
        )

        originals = dict(state.get("original_contents") or {})
        contents = dict(state.get("file_contents") or {})
        for item in applied:
            rel = item["path"]
            if rel not in originals:
                originals[rel] = item["old_content"] or ""
            contents[rel] = item["new_content"] or ""
            emit(
                "patch",
                {
                    "path": rel,
                    "action": item["action"],
                    "old_content": originals[rel],
                    "new_content": item["new_content"] or "",
                    "failed": False,
                },
            )
        for item in failed:
            emit("patch", {"path": item["path"], "failed": True, "reason": item["reason"]})

        if failed:
            reasons = "; ".join(f"{f['path']}: {f['reason']}" for f in failed)[:500]
            emit("log", {"message": f"Warning: {len(failed)} patch(es) did not apply — {reasons}"})
        emit("log", {"message": f"[{provider}] Applied {len(applied)} patch(es) on attempt {attempts}."})
        if plan.summary:
            emit("log", {"message": f"Fix summary: {_clip(plan.summary, 600)}"})

        update: dict = {
            "applied_count": len(applied),
            "applied_files": [a["path"] for a in applied],
            "total_changed": sorted(
                set(state.get("total_changed", [])) | {a["path"] for a in applied}
            ),
            "failed_patches": failed,
            "file_contents": contents,
            "original_contents": originals,
            "apply_attempts": attempts,
        }
        if plan.summary:
            update["summary"] = plan.summary

        if not applied:
            if state.get("total_changed"):
                # A repair round added nothing, but an earlier fix is already on disk:
                # validate and ship that instead of throwing the run away.
                emit("log", {"message": "Repair round produced no new patches; keeping the existing changes."})
                update["repair_context"] = ""
            elif attempts < settings.max_apply_attempts:
                reasons = "; ".join(f"{f['path']}: {f['reason']}" for f in failed)[:1200] or "no patches returned"
                update["repair_context"] = (
                    "NONE of your previous patches could be applied. Reasons: "
                    f"{reasons}. Re-read the exact file contents and copy 'old' snippets "
                    "character-for-character (or create a new file instead)."
                )
                emit("log", {"message": "Retrying the fix with stricter patch instructions..."})
            else:
                update["error"] = "The model did not produce any applicable patches after 2 attempts."
        else:
            update["repair_context"] = ""
        emit("step", {"name": "apply_fix", "status": "done"})
        return update

    async def run_tests(state: AgentState) -> dict:
        emit("step", {"name": "run_tests", "status": "running"})
        result = await detect_and_run(state["repo_dir"], changed_files=state.get("applied_files"))
        emit(
            "test",
            {
                "command": result["command"],
                "passed": result["passed"],
                "skipped": result["skipped"],
                "output": result["output"],
                "environment_fault": result.get("environment_fault", ""),
            },
        )
        retries = state.get("test_retries", 0)
        update: dict = {
            "test_command": result["command"] or "",
            "test_output": result["output"],
            "test_passed": result["passed"],
            "tests_skipped": result["skipped"],
            "test_reason": result.get("reason", ""),
        }

        if result["skipped"]:
            fault = result.get("environment_fault") or ""
            emit(
                "log",
                {"message": f"Tests skipped: the suite cannot be imported (missing {fault}); "
                            "reporting the fix as unverified rather than failing."}
                if fault
                else {"message": f"Tests skipped: {_clip(result['output'], 240)}"},
            )
            update["repair_context"] = ""
        elif result["passed"]:
            emit("log", {"message": f"Tests passed ({result['command']})."})
            update["repair_context"] = ""
        else:
            emit("log", {"message": f"Tests failed ({result['command']})."})
            if retries < settings.max_test_retries:
                update["test_retries"] = retries + 1
                update["repair_context"] = (
                    "The repository tests FAILED after your previous patch. Repair the fix so "
                    "these tests pass, without reverting working parts.\n"
                    f"Command: {result['command']}\nOutput (tail):\n{_clip(result['output'], 3000)}"
                )
                emit("log", {"message": "Sending failure output back to the model for one repair round."})
            else:
                update["repair_context"] = ""
                emit("log", {"message": "Repair budget exhausted; the PR will be opened with failing tests flagged."})
        emit("step", {"name": "run_tests", "status": "done"})
        return update

    async def create_pr(state: AgentState) -> dict:
        emit("step", {"name": "create_pr", "status": "running"})
        repo_dir, branch = state["repo_dir"], state["branch"]
        push_owner = state.get("push_owner") or state["owner"]
        push_name = state.get("push_name") or state["repo_name"]
        forked = bool(state.get("forked"))
        if not await gitops.has_changes(repo_dir):
            raise RuntimeError("No file changes were produced, so there is nothing to commit.")

        number = state["issue_number"]
        title = f"fix: {_clip(state.get('issue_title', 'issue fix'), 60)} (#{number})"
        await gitops.commit_all(repo_dir, f"{title}\n\nAI-generated fix for issue #{number}.\n\nFixes #{number}")

        if state.get("submit_mode") == "preview" or not settings.token:
            emit(
                "log",
                {"message": f"Fix committed locally on branch {branch} in {repo_dir}. "
                            "Nothing was pushed, so no pull request was opened."},
            )
            emit("step", {"name": "create_pr", "status": "done"})
            return {"pr_url": "", "head_repo": "", "forked": False}

        emit("log", {"message": f"Pushing {branch} to {push_owner}/{push_name}..."})
        await gitops.push_branch(repo_dir, push_owner, push_name, branch)

        # A pull request from a fork must name its head as "fork-owner:branch". When
        # the fork and the upstream sit in the same account, the fully qualified
        # "owner/repo:branch" form disambiguates it, so try that as a fallback. A
        # brand new fork can also be invisible to the PR API for a few seconds.
        head = f"{push_owner}:{branch}" if forked else branch
        candidates = [head, f"{push_owner}/{push_name}:{branch}"] if forked else [head]
        pr: dict | None = None
        last_error: RuntimeError | None = None
        for idx, candidate in enumerate(candidates):
            try:
                pr = await github_api.create_pull_request(
                    state["owner"],
                    state["repo_name"],
                    title=title,
                    body=_pr_body(state),
                    head=candidate,
                    base=state.get("default_branch") or "main",
                )
                head = candidate
                break
            except RuntimeError as exc:  # noqa: BLE001 - one bounded retry, then report
                last_error = exc
                if idx + 1 < len(candidates):
                    emit("log", {"message": f"Pull request rejected with head {candidate}: "
                                            f"{str(exc)[:160]}. Retrying in 5s..."})
                    await asyncio.sleep(5)
        if pr is None:
            raise last_error or RuntimeError("Could not create the pull request.")
        url = pr.get("html_url", "")
        head_repo = f"{push_owner}/{push_name}"
        emit(
            "pr",
            {
                "url": url,
                "title": title,
                "branch": branch,
                "number": pr.get("number"),
                "head_repo": head_repo,
                "forked": forked,
            },
        )
        via = f" from fork {head_repo}" if forked else ""
        emit("log", {"message": f"Pull request opened{via} (head {head}): {url}"})
        emit("step", {"name": "create_pr", "status": "done"})
        return {"pr_url": url, "head_repo": head_repo, "forked": forked}

    async def abort(state: AgentState) -> dict:
        message = state.get("error") or "The agent stopped without producing a fix."
        emit("log", {"message": f"Aborting: {message}"})
        return {"error": message}

    # ------------------------------------------------------ routing

    def after_apply(state: AgentState) -> str:
        # Ship whatever is on disk, even if the latest round added nothing new.
        if state.get("applied_count", 0) > 0 or state.get("total_changed"):
            return "run_tests"
        if state.get("apply_attempts", 0) < settings.max_apply_attempts:
            return "apply_fix"
        return "abort"

    def after_tests(state: AgentState) -> str:
        if state.get("tests_skipped") or state.get("test_passed"):
            return "create_pr"
        if state.get("test_retries", 0) <= settings.max_test_retries and state.get("repair_context"):
            return "apply_fix"
        return "create_pr"

    # ------------------------------------------------------- wiring

    graph = StateGraph(AgentState)
    graph.add_node("fetch_issue", fetch_issue)
    graph.add_node("clone_repo", clone_repo)
    graph.add_node("map_repo", map_repo)
    graph.add_node("locate", locate)
    graph.add_node("gather", gather)
    graph.add_node("apply_fix", apply_fix)
    graph.add_node("run_tests", run_tests)
    graph.add_node("create_pr", create_pr)
    graph.add_node("abort", abort)

    graph.add_edge(START, "fetch_issue")
    graph.add_edge("fetch_issue", "clone_repo")
    graph.add_edge("clone_repo", "map_repo")
    graph.add_edge("map_repo", "locate")
    graph.add_edge("locate", "gather")
    graph.add_edge("gather", "apply_fix")
    graph.add_conditional_edges(
        "apply_fix", after_apply, {"run_tests": "run_tests", "apply_fix": "apply_fix", "abort": "abort"}
    )
    graph.add_conditional_edges(
        "run_tests", after_tests, {"create_pr": "create_pr", "apply_fix": "apply_fix"}
    )
    graph.add_edge("create_pr", END)
    graph.add_edge("abort", END)
    return graph.compile()


def _fix_user_prompt(state: AgentState) -> str:
    parts = [_issue_block(state), ""]

    repair = state.get("repair_context") or ""
    if repair:
        parts += ["REPAIR CONTEXT (address this before anything else):", _clip(repair, 3500), ""]

    search = state.get("search_results") or ""
    if search:
        parts += ["SEARCH RESULTS:", _clip(search, SEARCH_PROMPT_CAP), ""]

    contents = state.get("file_contents") or {}
    if contents:
        parts.append("CURRENT FILE CONTENTS:")
        budget = FILE_CONTENTS_PROMPT_CAP
        for rel, text in contents.items():
            if budget <= 0:
                parts.append("[additional files omitted from context]")
                break
            chunk = _clip(text, budget)
            budget -= len(chunk)
            parts.append(f"----- FILE: {rel} -----")
            parts.append(chunk)
            parts.append("")
    else:
        parts.append("CURRENT FILE CONTENTS: (none resolved — create new files only if justified)")
        parts.append("")

    repo_map = state.get("repo_map", "")
    if repo_map:
        path_lines = [
            ln.strip()
            for ln in repo_map.splitlines()
            if ln.strip() and not ln.startswith(("Total", "Project"))
        ]
        parts.append("REPOSITORY PATHS (for context and new-file placement):")
        parts.append(_clip("\n".join(path_lines), PATHS_PROMPT_CAP))

    return "\n".join(parts)


def _pr_body(state: AgentState) -> str:
    lines = ["## AI-generated fix", ""]
    summary = (state.get("summary") or "").strip()
    if summary:
        lines += [summary, ""]
    lines.append(f"Fixes #{state['issue_number']}")
    lines.append("")

    if state.get("tests_skipped"):
        reason = (state.get("test_reason") or "").strip()
        if reason:
            lines.append(f"**Tests:** skipped — {reason}")
        else:
            lines.append("**Tests:** skipped — no runnable test setup was detected in this repository.")
    elif state.get("test_passed"):
        lines.append(f"**Tests:** passing (`{state.get('test_command', 'tests')}`)")
    else:
        lines.append(
            f"**Tests:** still failing after a repair round (`{state.get('test_command', 'tests')}`). "
            "Please review before merging."
        )
    files = state.get("applied_files") or []
    if files:
        lines += ["", "**Files changed:**"] + [f"- `{f}`" for f in files]
    lines += ["", "---", "Generated by Issue Solver (MiniMax via OpenRouter, Gemini fallback)."]
    return "\n".join(lines)
