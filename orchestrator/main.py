"""Точка входа оркестратора.

Запуск:  uvicorn orchestrator.main:app --reload
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from orchestrator.agent.loop import AgentLoop
from orchestrator.api import chat, polling
from orchestrator.config import get_settings
from orchestrator.llm.anthropic_provider import AnthropicProvider
from orchestrator.tools.executor import ToolExecutor
from orchestrator.tools.registry import TOOLS
from orchestrator.transport.mock import MockTransport
from orchestrator.transport.polling import PollingTransport


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()

    # Пока расширения 1С нет, транспорт по умолчанию — мок. Переключается
    # переменной окружения BOTA_TRANSPORT=polling|direct.
    mode = os.getenv("BOTA_TRANSPORT", "mock")
    if mode == "polling":
        transport: Any = PollingTransport(timeout_seconds=settings.tool_timeout_seconds)
        app.state.polling = transport
    else:
        transport = MockTransport()

    provider = AnthropicProvider(
        api_key=settings.anthropic_api_key,
        model=settings.llm_model,
        max_tokens=settings.llm_max_tokens,
    )
    app.state.agent = AgentLoop(
        provider=provider,
        executor=ToolExecutor(transport),
        max_tool_calls=settings.max_tool_calls,
        effort=settings.llm_effort,
    )
    app.state.transport_mode = mode
    yield


app = FastAPI(
    title="Бота — оркестратор ИИ-агента бухгалтера",
    description="Сверка поступлений и ЭСФ для «1С:Бухгалтерия для Казахстана» 3.0",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(chat.router)
app.include_router(polling.router)


@app.get("/health", tags=["service"])
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "transport": getattr(app.state, "transport_mode", "unknown"),
        "tools": len(TOOLS),
    }


@app.get("/v1/tools", tags=["service"])
async def list_tools() -> list[dict[str, str]]:
    """Реестр инструментов — для сверки контрактов с разработчиком расширения 1С."""
    return [
        {
            "name": spec.name,
            "onec_method": spec.onec_method,
            "writes": str(spec.writes),
            "description": spec.description,
        }
        for spec in TOOLS
    ]
