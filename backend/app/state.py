"""LangGraph state schema for the solver agent."""
from __future__ import annotations

from typing import TypedDict


class AgentState(TypedDict, total=False):
    # Inputs
    repo_url: str
    owner: str
    repo_name: str
    issue_number: int

    # Issue context
    issue_title: str
    issue_body: str
    issue_comments: list[str]
    default_branch: str

    # Repository workspace
    repo_dir: str
    branch: str
    repo_map: str

    # Where the fix branch is pushed (differs from the issue repo when forked)
    submit_mode: str  # direct | fork | preview
    submit_fallback: str  # mode to use if creating the fork fails
    push_owner: str
    push_name: str
    forked: bool
    head_repo: str

    # Locating relevant code
    candidate_files: list[str]
    search_queries: list[str]
    search_results: str
    file_contents: dict[str, str]
    original_contents: dict[str, str]

    # Fixing
    applied_count: int
    applied_files: list[str]
    total_changed: list[str]
    failed_patches: list[dict]
    summary: str
    apply_attempts: int

    # Testing / repair loop
    test_command: str
    test_output: str
    test_passed: bool | None
    tests_skipped: bool
    test_retries: int
    # Plain-language explanation shown when the suite could not be started.
    test_reason: str
    repair_context: str

    # Output
    pr_url: str
    error: str
