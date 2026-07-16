"""Thin async client for the OpenRouter API — the only network layer in the app.

Everything the council does routes through :class:`OpenRouterClient.chat`, so
tests can swap in a stub with the same signature and never touch the network.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass

import httpx

from .config import Settings

API_BASE = "https://openrouter.ai/api/v1"
CHAT_URL = f"{API_BASE}/chat/completions"
MODELS_URL = f"{API_BASE}/models"

# Errors worth retrying: rate limits and transient upstream failures.
_RETRY_STATUS = {429, 500, 502, 503, 504}
# Longest we'll wait between retries, even if Retry-After asks for more.
_MAX_BACKOFF = 20.0


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

    def __init__(self, settings: Settings, *, max_retries: int = 5):
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
        rate_limited = False

        async with self._semaphore:
            for attempt in range(self._max_retries):
                try:
                    resp = await self._client.post(
                        CHAT_URL, headers=self._headers(), json=payload
                    )
                    if resp.status_code in _RETRY_STATUS:
                        raise _Retryable(resp.status_code, _retry_after(resp))
                    resp.raise_for_status()
                    data = resp.json()
                    return _extract_message(data)
                except _Retryable as exc:
                    last_error = exc
                    rate_limited = exc.status == 429
                    if attempt < self._max_retries - 1:
                        await asyncio.sleep(_backoff(attempt, exc.retry_after))
                    continue
                except (httpx.TransportError, httpx.TimeoutException) as exc:
                    last_error = exc
                    if attempt < self._max_retries - 1:
                        await asyncio.sleep(_backoff(attempt, None))
                    continue
                except httpx.HTTPStatusError as exc:
                    # Non-retryable HTTP error (e.g. 400/401/404 bad model).
                    raise OpenRouterError(
                        f"{short_model(model)}: HTTP {exc.response.status_code} "
                        f"{exc.response.text[:160]}"
                    ) from exc

        if rate_limited:
            raise OpenRouterError(
                f"{short_model(model)}: temporarily rate-limited on OpenRouter's free "
                "tier — it withdrew from this round. Try again shortly, or pick fewer or "
                "different models."
            )
        raise OpenRouterError(f"{short_model(model)}: no response after retries ({last_error}).")

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
    """Internal marker for a response we should retry (carries status + hint)."""

    def __init__(self, status: int, retry_after: float | None):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.retry_after = retry_after


def _retry_after(resp: httpx.Response) -> float | None:
    """Parse the Retry-After header (seconds) if the provider sent one."""
    raw = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _backoff(attempt: int, retry_after: float | None) -> float:
    """Honor Retry-After (capped), else exponential backoff with jitter."""
    if retry_after is not None:
        return min(retry_after, _MAX_BACKOFF)
    base = min(2.0 ** attempt, _MAX_BACKOFF)   # 1, 2, 4, 8, 16 …
    return base + random.uniform(0, 0.75 * base)   # jitter to de-sync parallel calls


def short_model(model_id: str) -> str:
    """A compact, human name for a model id (drops the org and :free suffix)."""
    name = model_id.split("/")[-1] if "/" in model_id else model_id
    return name.replace(":free", "")


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
