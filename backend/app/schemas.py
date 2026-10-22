"""Pydantic schemas for structured LLM output."""
from pydantic import BaseModel, Field


class LocatePlan(BaseModel):
    """Result of the locate step: which files matter and what to search for."""

    files: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    reasoning: str = ""


class Patch(BaseModel):
    """A single search/replace patch."""

    path: str
    action: str = "replace"  # replace | create | delete
    old: str = ""
    new: str = ""


class FixPlan(BaseModel):
    """Result of the fix step: patches plus a PR-ready summary."""

    patches: list[Patch] = Field(default_factory=list)
    summary: str = ""
