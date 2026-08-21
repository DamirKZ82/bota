"""Настройки оркестратора. Читаются из окружения / .env (см. .env.example)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- LLM ---
    llm_provider: Literal["anthropic", "local"] = "anthropic"
    anthropic_api_key: str | None = None
    llm_model: str = "claude-opus-5"
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    llm_max_tokens: int = 16000

    # --- База данных ---
    database_url: str = "postgresql+asyncpg://bota:bota@localhost:5432/bota"

    # --- Оркестратор ---
    max_tool_calls: int = 30
    """Лимит вызовов инструментов на один запрос пользователя (ТЗ п.6.1)."""

    masking_enabled: bool = True
    tool_timeout_seconds: int = 120
    journal_retention_months: int = 12

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
