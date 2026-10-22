"""In-memory job store, runner, and progress event fan-out (SSE source)."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import settings
from .graph import build_solver_graph
from .llm import LLMService
from .state import AgentState

log = logging.getLogger("issue_solver.jobs")

MAX_JOBS_KEPT = 50


@dataclass
class Job:
    id: str
    owner: str
    repo_name: str
    issue_number: int
    repo_url: str
    status: str = "queued"  # queued | running | done | error
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    events: list[dict] = field(default_factory=list)
    done: bool = False
    result: dict = field(default_factory=dict)

    @property
    def workspace_dir(self) -> Path:
        return settings.workspace_path / self.id

    def emit(self, event_type: str, data: dict[str, Any]) -> None:
        """Append a progress event; the SSE endpoint replays this list."""
        self.events.append({"type": event_type, "data": data, "ts": time.time()})

    def summary(self) -> dict:
        duration = None
        end = self.finished_at or (self.events[-1]["ts"] if self.done and self.events else None)
        if end:
            duration = round(end - self.created_at, 1)
        return {
            "id": self.id,
            "owner": self.owner,
            "repo_name": self.repo_name,
            "issue_number": self.issue_number,
            "repo_url": self.repo_url,
            "issue_url": f"{self.repo_url}/issues/{self.issue_number}",
            "status": self.status,
            "created_at": self.created_at,
            "done": self.done,
            "duration_s": duration,
            "result": self.result,
            "event_count": len(self.events),
        }


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.llm = LLMService()
        self._tasks: set[asyncio.Task] = set()

    def create(self, owner: str, repo_name: str, issue_number: int, repo_url: str) -> Job:
        job = Job(
            id=uuid.uuid4().hex[:12],
            owner=owner,
            repo_name=repo_name,
            issue_number=issue_number,
            repo_url=repo_url,
        )
        self.jobs[job.id] = job
        self._trim()
        return job

    def _trim(self) -> None:
        overflow = len(self.jobs) - MAX_JOBS_KEPT
        if overflow <= 0:
            return
        finished = sorted((j for j in self.jobs.values() if j.done), key=lambda j: j.created_at)
        for job in finished[:overflow]:
            self.jobs.pop(job.id, None)

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def list(self) -> list[dict]:
        return [
            job.summary()
            for job in sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)
        ]

    def start(self, job: Job) -> None:
        task = asyncio.create_task(self._run(job))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, job: Job) -> None:
        job.status = "running"
        job.emit(
            "log",
            {"message": f"Job started for {job.owner}/{job.repo_name} issue #{job.issue_number}"},
        )
        try:
            graph = build_solver_graph(job, self.llm)
            initial: AgentState = {
                "repo_url": job.repo_url,
                "owner": job.owner,
                "repo_name": job.repo_name,
                "issue_number": job.issue_number,
            }
            final = await asyncio.wait_for(
                graph.ainvoke(initial), timeout=settings.overall_timeout_s
            )
            if final.get("error"):
                job.status = "error"
                job.result = {"error": final["error"]}
                job.emit("error", {"message": final["error"]})
            else:
                job.status = "done"
                job.result = {
                    "pr_url": final.get("pr_url", ""),
                    "branch": final.get("branch", ""),
                    "summary": final.get("summary", ""),
                    "test_passed": final.get("test_passed"),
                    "tests_skipped": final.get("tests_skipped", False),
                    "test_command": final.get("test_command", ""),
                    # Names the missing dependency when the suite never started.
                    "test_reason": final.get("test_reason", ""),
                    "changed_files": final.get("applied_files", []),
                    "head_repo": final.get("head_repo", ""),
                    "forked": final.get("forked", False),
                }
                job.emit("done", job.result)
        except asyncio.TimeoutError:
            job.status = "error"
            message = (
                f"Job exceeded its {settings.overall_timeout_s}s time budget and was aborted."
            )
            job.result = {"error": message}
            job.emit("error", {"message": message})
        except Exception as exc:  # noqa: BLE001 - surface any node failure to the UI
            log.exception("job %s failed", job.id)
            message = f"{type(exc).__name__}: {exc}"
            job.status = "error"
            job.result = {"error": message}
            job.emit("error", {"message": message})
        finally:
            job.finished_at = time.time()
            job.done = True


job_manager = JobManager()
