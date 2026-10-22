"""Thin async GitHub REST client.

Covers issue reads, repo info, fork creation (the cross-repo PR route) and PR
creation. Public reads work without a token; everything that writes does not
work without one.
"""
from __future__ import annotations

import asyncio

import httpx

from .config import settings

GITHUB_API = "https://api.github.com"

# The login behind the token rarely changes during a server lifetime.
_viewer_login: str | None = None


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if settings.token:
        headers["Authorization"] = f"Bearer {settings.token}"
    return headers


async def _get(url: str, params: dict | None = None) -> dict | list:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=_headers(), params=params)
        resp.raise_for_status()
        return resp.json()


async def get_repo_info(owner: str, name: str) -> dict:
    return await _get(f"{GITHUB_API}/repos/{owner}/{name}")  # type: ignore[return-value]


async def get_issue(owner: str, name: str, number: int) -> dict:
    data = await _get(f"{GITHUB_API}/repos/{owner}/{name}/issues/{number}")
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected response reading issue #{number}")
    return data


async def get_issue_comments(owner: str, name: str, number: int, limit: int = 5) -> list[dict]:
    try:
        data = await _get(
            f"{GITHUB_API}/repos/{owner}/{name}/issues/{number}/comments",
            params={"per_page": limit},
        )
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001 - comments are optional context
        return []


async def create_pull_request(
    owner: str, name: str, *, title: str, body: str, head: str, base: str
) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{GITHUB_API}/repos/{owner}/{name}/pulls",
            headers=_headers(),
            json={"title": title, "body": body, "head": head, "base": base},
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"GitHub PR API error {resp.status_code}: {resp.text[:300]}")
        return resp.json()


async def get_viewer_login() -> str:
    """Login of the authenticated account (needed for `owner:branch` PR heads)."""
    global _viewer_login
    if _viewer_login:
        return _viewer_login
    if not settings.token:
        raise RuntimeError("No GitHub token configured.")
    data = await _get(f"{GITHUB_API}/user")
    login = (data or {}).get("login") if isinstance(data, dict) else None
    if not login:
        raise RuntimeError("Could not determine the GitHub login for this token.")
    _viewer_login = login
    return login


async def wait_for_repo(owner: str, name: str, timeout_s: int) -> dict:
    """Poll until a freshly created fork is actually usable."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    last_status = 0
    while asyncio.get_event_loop().time() < deadline:
        try:
            data = await _get(f"{GITHUB_API}/repos/{owner}/{name}")
            if isinstance(data, dict) and data.get("full_name"):
                return data
        except httpx.HTTPStatusError as exc:
            last_status = exc.response.status_code
        await asyncio.sleep(2)
    raise RuntimeError(f"Fork {owner}/{name} was not ready within {timeout_s}s (last status {last_status}).")


async def fork_repo(owner: str, name: str) -> tuple[str, str]:
    """Fork upstream `owner/name` into the token's account; returns (fork_owner, fork_name).

    Forking is asynchronous, so the caller must wait for the repo to appear.
    """
    login = await get_viewer_login()
    payload: dict = {"default_branch_only": True}
    # Ask for a distinct name when the fork would land in the same account as the
    # upstream; GitHub rejects (or ignores) a same-named fork there.
    fork_name = name
    if login.lower() == owner.lower():
        fork_name = f"{name}-fork"
        payload["name"] = fork_name
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{GITHUB_API}/repos/{owner}/{name}/forks",
            headers=_headers(),
            json=payload,
        )
    if resp.status_code not in (200, 201, 202):
        detail = resp.text[:240] if resp.text else ""
        raise RuntimeError(
            f"Could not fork {owner}/{name} (HTTP {resp.status_code}): {detail}. "
            "Forking needs a classic token with the `repo` scope (fine-grained tokens "
            "cannot create repositories) and the upstream must allow forking."
        )
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    full_name = (body or {}).get("full_name") if isinstance(body, dict) else None
    if full_name and "/" in full_name:
        fork_owner, fork_name = full_name.split("/", 1)
    else:
        fork_owner = login
    if f"{fork_owner}/{fork_name}".lower() == f"{owner}/{name}".lower():
        # Forking a repository you already own answers with the upstream itself:
        # your account is already the network root, so no second copy is created.
        raise RuntimeError(
            f"GitHub returned {fork_owner}/{fork_name} for the fork request, which is the "
            f"upstream itself - no separate fork was created. A repository cannot be forked "
            "into the account that already owns it."
        )
    await wait_for_repo(fork_owner, fork_name, settings.fork_wait_timeout_s)
    # A pre-existing fork can be years behind upstream; sync it best-effort so the
    # fix is not built on stale code. Diverged forks answer 409, which we ignore.
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            await client.post(
                f"{GITHUB_API}/repos/{fork_owner}/{fork_name}/merge-upstream",
                headers=_headers(),
                json={"branch": (body or {}).get("default_branch", "main") if isinstance(body, dict) else "main"},
            )
    except Exception:  # noqa: BLE001 - purely opportunistic
        pass
    return fork_owner, fork_name
