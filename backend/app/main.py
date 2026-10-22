"""FastAPI application entrypoint."""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routers import jobs as jobs_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)

app = FastAPI(
    title="Issue Solver",
    description="Agentic AI that turns a GitHub issue into a tested fix and a pull request.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(jobs_router.router)


@app.get("/api/health")
async def health() -> dict:
    """Configuration status for the UI badges. Never exposes key material."""
    return {
        "status": "ok",
        "providers": {
            # `model` is the candidate that will be tried first; `models` is the
            # whole fallback list, because free tiers retire names without warning.
            "primary": {
                "label": "MiniMax (OpenRouter)",
                "model": settings.primary_models[0] if settings.primary_models else "",
                "models": settings.primary_models,
                "configured": bool(settings.openrouter_api_key),
            },
            "fallback": {
                "label": "Gemini (fallback)",
                "model": settings.gemini_models[0] if settings.gemini_models else "",
                "models": settings.gemini_models,
                "configured": bool(settings.gemini_api_key),
            },
        },
        "github": {
            "token_configured": bool(settings.token),
            "classic_token": bool(settings.github_classic_token),
            "fine_grained_token": bool(settings.github_token),
            # Only a classic `repo` token can create a fork of someone else's repo.
            "can_fork": bool(settings.github_classic_token) and settings.allow_fork,
            "allow_fork": settings.allow_fork,
        },
        "limits": {
            "time_budget_s": settings.overall_timeout_s,
            "llm_calls_per_job": "2-3",
            "max_files_read": settings.max_files_read,
            "max_file_chars": settings.max_file_chars,
        },
    }
