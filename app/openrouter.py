"""Thin async client for the OpenRouter API — the only network layer in the app.

Everything the council does routes through :class:`OpenRouterClient.chat`, so
tests can swap in a stub with the same signature and never touch the network.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from .config import Settings

API_BASE = "https://openrouter.ai/api/v1"
CHAT_URL = f"{API_BASE}/chat/completions"
MODELS_URL = f"{API_BASE}/models"

# Errors worth retrying: rate limits and transient upstream failures.
_RETRY_STATUS = {429, 500, 502, 503, 504}


class OpenRouterError(RuntimeError):
    """A call to OpenRouter failed after exhausting retries."""


@dataclass(frozen=True)
class ModelInfo:
    id: str
    name: str


class OpenRouterClient:
    """Async OpenRouter wrapper with retry/backoff and a concurrency cap.

    The concurrency cap (a shared semaphore) keeps parallel council calls under
    the free tier's per-minute limits; the retry/backoff absorbs the 429s that
    still slip through.
    """

    def __init__(self, settings: Settings, *, max_retries: int = 4):
        self._settings = settings
        self._max_retries = max_retries
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)
        self._client = httpx.AsyncClient(timeout=settings.timeout)
        self._free_models: list[ModelInfo] | None = None

    # -- lifecycle ---------------------------------------------------------
    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "OpenRouterClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    # -- headers -----------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        if not self._settings.has_key:
            raise OpenRouterError(
                "OPENROUTER_API_KEY is not set. Copy .env.example to .env and add "
                "a free key from https://openrouter.ai/keys"
            )
        return {
            "Authorization": f"Bearer {self._settings.openrouter_api_key}",
            "Content-Type": "application/json",
            # Optional attribution headers OpenRouter recommends.
            "HTTP-Referer": self._settings.app_url,
            "X-Title": self._settings.app_title,
        }

    # -- chat --------------------------------------------------------------
    async def chat(self, model: str, messages: list[dict]) -> str:
        """Return the assistant text for one chat completion.

        Retries transient failures with exponential backoff. Raises
        :class:`OpenRouterError` on final failure so callers can drop that model
        from the round rather than crash the whole council.
        """
        payload = {"model": model, "messages": messages}
        last_error: Exception | None = None

        async with self._semaphore:
            for attempt in range(self._max_retries):
                try:
                    resp = await self._client.post(
                        CHAT_URL, headers=self._headers(), json=payload
                    )
                    if resp.status_code in _RETRY_STATUS:
                        raise _Retryable(f"HTTP {resp.status_code}: {resp.text[:200]}")
                    resp.raise_for_status()
                    data = resp.json()
                    return _extract_message(data)
                except (_Retryable, httpx.TransportError, httpx.TimeoutException) as exc:
                    last_error = exc
                    if attempt < self._max_retries - 1:
                        await asyncio.sleep(2 ** attempt)  # 1s, 2s, 4s, ...
                    continue
                except httpx.HTTPStatusError as exc:
                    # Non-retryable HTTP error (e.g. 400/401/404 bad model).
                    raise OpenRouterError(
                        f"{model}: HTTP {exc.response.status_code} "
                        f"{exc.response.text[:200]}"
                    ) from exc

        raise OpenRouterError(f"{model}: giving up after retries ({last_error})")

    # -- model catalog -----------------------------------------------------
    async def list_free_models(self, *, force: bool = False) -> list[ModelInfo]:
        """Fetch the catalog and keep only models that are free to prompt.

        Cached for the process lifetime unless ``force`` is given.
        """
        if self._free_models is not None and not force:
            return self._free_models

        resp = await self._client.get(MODELS_URL, headers=self._headers())
        resp.raise_for_status()
        models = resp.json().get("data", [])

        free: list[ModelInfo] = []
        for m in models:
            pricing = m.get("pricing") or {}
            if _is_free(pricing):
                free.append(ModelInfo(id=m["id"], name=m.get("name") or m["id"]))

        free.sort(key=lambda m: m.name.lower())
        self._free_models = free
        return free


class _Retryable(Exception):
    """Internal marker for a response we should retry."""


def _is_free(pricing: dict) -> bool:
    """A model is 'free' when both prompt and completion cost parse to zero."""

    def zero(value) -> bool:
        try:
            return float(value) == 0.0
        except (TypeError, ValueError):
            return False

    return zero(pricing.get("prompt")) and zero(pricing.get("completion"))


def _extract_message(data: dict) -> str:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise OpenRouterError(f"Unexpected response shape: {str(data)[:200]}") from exc
    if not isinstance(content, str) or not content.strip():
        raise OpenRouterError("Model returned an empty message")
    return content
