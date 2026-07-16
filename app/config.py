"""Application configuration, loaded from environment / a local .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Load .env if present. Real environment variables always take precedence.
load_dotenv()


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    openrouter_api_key: str
    max_concurrency: int = 2
    timeout: float = 90.0
    app_url: str = "http://localhost:8000"
    app_title: str = "AI Council"

    @property
    def has_key(self) -> bool:
        return bool(self.openrouter_api_key)


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def load_settings() -> Settings:
    """Build a Settings object from the environment.

    Does not raise if the key is missing — that is validated at request time so
    the server (and the offline test suite) can start without a key.
    """
    return Settings(
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", "").strip(),
        max_concurrency=max(1, _int_env("OPENROUTER_MAX_CONCURRENCY", 2)),
        timeout=_float_env("OPENROUTER_TIMEOUT", 90.0),
        app_url=os.getenv("OPENROUTER_APP_URL", "http://localhost:8000").strip(),
        app_title=os.getenv("OPENROUTER_APP_TITLE", "AI Council").strip(),
    )


settings = load_settings()
