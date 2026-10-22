"""LLM access with provider and model fallback.

Primary: a free model via OpenRouter. Fallback: the Gemini free tier. Each
provider may list several models, tried in order, because free tiers retire
model names (404) and shed traffic per model (503) - one stale string must not
fail a run that has already spent time forking, cloning and locating.

A 429 from the primary puts that provider on cooldown so the rest of the job
runs on the fallback without burning more rate-limited requests.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from functools import partial
from typing import Callable, TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from .config import settings

log = logging.getLogger("issue_solver.llm")

T = TypeVar("T", bound=BaseModel)

# OpenRouter free models are RPM/RPD limited; on a 429 skip the provider for a while.
PRIMARY_COOLDOWN_S = 600.0
# An overloaded model or an empty completion is usually momentary, so the same
# model gets one repeat after a short pause. Bounded on purpose: a run costs
# minutes, and the remaining candidates are already queued behind this one.
MAX_ATTEMPTS_PER_MODEL = 2
TRANSIENT_RETRY_DELAY_S = 2.0


class LLMError(RuntimeError):
    """Raised when every configured provider failed."""


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _is_rate_limit(exc: Exception) -> bool:
    text = str(exc).lower()
    cls = type(exc).__name__.lower()
    return "429" in text or "rate limit" in text or "ratelimit" in cls or "too many" in text


_TRANSIENT_MARKERS = (
    "503",
    "overloaded",
    "high demand",
    "empty response",
    "timed out",
    "timeout",
    "connection",
    "temporarily",
    "429",
    "rate limit",
    "too many",
)


def _is_transient(exc: Exception) -> bool:
    """Whether repeating the same call could plausibly succeed."""
    text = str(exc).lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def _describe_empty(result: object) -> str:
    """Why a completion arrived with no usable text: truncated, filtered or thinking-only."""
    bits: list[str] = []
    meta = getattr(result, "response_metadata", None)
    if isinstance(meta, dict):
        finish = meta.get("finish_reason") or meta.get("stop_reason")
        if finish:
            bits.append(f"finish_reason={finish}")
    content = getattr(result, "content", None)
    if isinstance(content, list):
        kinds = sorted({str(block.get("type")) for block in content if isinstance(block, dict)})
        if kinds:
            bits.append("blocks=" + ",".join(kinds))
    usage = getattr(result, "usage_metadata", None)
    if isinstance(usage, dict) and usage.get("output_tokens") is not None:
        bits.append(f"output_tokens={usage['output_tokens']}")
    return ", ".join(bits) or "no diagnostic metadata"


def _content_to_text(content: object) -> str:
    """Chat models return str or a list of content blocks depending on provider."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(str(block.get("text") or block.get("content") or ""))
        return "".join(parts)
    return str(content or "")


def parse_json_block(text: str) -> dict | None:
    """Best-effort extraction of a single JSON object from model output."""
    text = _THINK_RE.sub("", text)
    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    snippet = text[start : end + 1]
    try:
        return json.loads(snippet)
    except json.JSONDecodeError:
        # Tolerate trailing commas, a common model slip.
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", snippet))
        except json.JSONDecodeError:
            return None


class LLMService:
    def __init__(self) -> None:
        self._primary_cooldown_until = 0.0

    # -- provider factories -------------------------------------------------

    def _make_openrouter(self, model: str):
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model,
            api_key=settings.openrouter_api_key,  # type: ignore[arg-type]
            base_url=settings.openrouter_base_url,
            temperature=0.15,
            max_tokens=settings.primary_max_tokens,
            timeout=settings.llm_timeout_s,
            default_headers={
                "HTTP-Referer": "http://localhost:5173",
                "X-Title": "Issue Solver",
            },
        )

    def _make_gemini(self, model: str):
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=model,
            google_api_key=settings.gemini_api_key,  # type: ignore[arg-type]
            temperature=0.15,
            max_output_tokens=settings.gemini_max_tokens,
        )

    def providers(self) -> list[tuple[str, str, Callable[[], object]]]:
        """(provider, model, factory) candidates in preference order.

        Every configured model of a provider is listed before the next provider,
        so a retired or overloaded model name falls sideways instead of ending
        the run.
        """
        out: list[tuple[str, str, Callable[[], object]]] = []
        cooled = time.time() < self._primary_cooldown_until
        if settings.openrouter_api_key and not cooled:
            out += [
                ("openrouter", model, partial(self._make_openrouter, model))
                for model in settings.primary_models
            ]
        elif settings.openrouter_api_key:
            log.info("openrouter is on cooldown; skipping it this time")
        if settings.gemini_api_key:
            out += [
                ("gemini", model, partial(self._make_gemini, model))
                for model in settings.gemini_models
            ]
        return out

    # -- core calls ----------------------------------------------------------

    async def invoke_text(self, system: str, user: str) -> tuple[str, str]:
        """Return (text, provider_name). Tries every candidate in priority order."""
        candidates = self.providers()
        if not candidates:
            raise LLMError(
                "No LLM provider configured. Set OPENROUTER_API_KEY or GEMINI_API_KEY in backend/.env."
            )
        failures: list[str] = []
        for idx, (name, model, factory) in enumerate(candidates):
            last_candidate = idx == len(candidates) - 1
            for attempt in range(1, MAX_ATTEMPTS_PER_MODEL + 1):
                try:
                    llm = factory()  # type: ignore[operator]
                    messages = [SystemMessage(content=system), HumanMessage(content=user)]
                    result = await asyncio.wait_for(
                        llm.ainvoke(messages), timeout=settings.llm_timeout_s + 15
                    )
                    text = _content_to_text(result.content).strip()
                    if not text:
                        raise LLMError(
                            f"provider returned an empty response ({_describe_empty(result)})"
                        )
                    if failures:
                        log.info("recovered on %s/%s after %d failed attempt(s)",
                                 name, model, len(failures))
                    return text, name
                except Exception as exc:  # noqa: BLE001 - fall through to the next try
                    failures.append(f"{name}/{model}: {str(exc)[:200]}")
                    log.warning("provider %s model %s attempt %d failed: %s",
                                name, model, attempt, exc)
                    if name == "openrouter" and _is_rate_limit(exc):
                        self._primary_cooldown_until = time.time() + PRIMARY_COOLDOWN_S
                        log.warning("openrouter rate-limited; cooling down for %ss",
                                    int(PRIMARY_COOLDOWN_S))
                        break
                    if (
                        _is_transient(exc)
                        and attempt < MAX_ATTEMPTS_PER_MODEL
                        and last_candidate
                    ):
                        # Only pause on the final candidate: while others remain,
                        # falling through is faster than sleeping here.
                        await asyncio.sleep(TRANSIENT_RETRY_DELAY_S)
                        continue
                    break
        # Name every failure: a retired model and an exhausted key look different,
        # and the previous "last error" wording blamed GitHub for an LLM fault.
        detail = " | ".join(failures)
        raise LLMError(
            f"All {len(failures)} LLM attempts failed across "
            f"{len({c[0] for c in candidates})} provider(s): {detail}"
        )

    async def invoke_json(
        self, system: str, user: str, schema: type[T]
    ) -> tuple[T, str]:
        """Ask for a single JSON object and validate it against `schema`.

        One repair round-trip is allowed if the model emits invalid JSON.
        """
        last_text = ""
        for attempt in range(2):
            prompt = user
            if attempt == 1:
                prompt = (
                    user
                    + "\n\nYour previous response was not valid JSON. "
                      "Return ONLY the corrected JSON object, nothing else.\n"
                      f"Previous response (truncated): {last_text[:800]}"
                )
            text, provider = await self.invoke_text(system, prompt)
            last_text = text
            data = parse_json_block(text)
            if data is not None:
                try:
                    return schema.model_validate(data), provider
                except Exception:  # noqa: BLE001 - wrong shape, retry once
                    log.warning("JSON did not match %s", schema.__name__)
        raise LLMError(
            f"Model output could not be parsed as {schema.__name__} JSON: {last_text[:400]}"
        )
