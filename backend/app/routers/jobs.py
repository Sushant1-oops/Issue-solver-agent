"""Job REST endpoints plus the SSE progress stream."""
from __future__ import annotations

import asyncio
import json
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..jobs import job_manager

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_REPO_RE = re.compile(
    r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:/issues/(\d+))?/?$",
    re.IGNORECASE,
)
_SHORTHAND_RE = re.compile(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?$")


def parse_github_reference(url: str) -> tuple[str, str, int | None]:
    """Accept a repo URL, an issue URL, or an 'owner/repo' shorthand."""
    cleaned = re.sub(r"^git\+", "", (url or "").strip())
    match = _REPO_RE.search(cleaned) or _SHORTHAND_RE.match(cleaned)
    if not match:
        raise HTTPException(
            status_code=422,
            detail="Could not find a GitHub repository. Use https://github.com/owner/repo/issues/123",
        )
    owner, name = match.group(1), match.group(2)
    number = int(match.group(3)) if match.group(3) else None
    if name.lower() == "issues":
        raise HTTPException(status_code=422, detail="Repository name missing in the URL.")
    return owner, name, number


class JobCreate(BaseModel):
    repo_url: str = Field(..., description="GitHub repository URL or issue URL")
    issue_number: int | None = None


@router.post("")
async def create_job(payload: JobCreate) -> dict:
    owner, name, parsed_number = parse_github_reference(payload.repo_url)
    number = payload.issue_number or parsed_number
    if not number or number < 1:
        raise HTTPException(
            status_code=422,
            detail="An issue number is required: paste the issue URL or pass issue_number.",
        )
    job = job_manager.create(owner, name, number, f"https://github.com/{owner}/{name}")
    job_manager.start(job)
    return {"job_id": job.id, "status": job.status}


@router.get("")
async def list_jobs() -> list[dict]:
    return job_manager.list()


@router.get("/{job_id}")
async def get_job(job_id: str) -> dict:
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job.summary()


@router.get("/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    """Server-Sent Events: replays stored events (so refreshes lose nothing)."""
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    async def stream():
        idx = 0
        while True:
            events = job.events
            while idx < len(events):
                event = events[idx]
                idx += 1
                payload = json.dumps(event, separators=(",", ":"), default=str)
                yield f"data: {payload}\n\n"
            if job.done:
                break
            await asyncio.sleep(0.25)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
