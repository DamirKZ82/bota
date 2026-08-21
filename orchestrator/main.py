"""Точка входа оркестратора.

Запуск:  uvicorn orchestrator.main:app --reload

Режим определяется переменной `TRANSPORT`:
  mock    — разработка: без базы 1С и без Postgres, диалоги в памяти;
  polling — очередь задач в Postgres, 1С забирает их фоновым заданием;
  direct  — оркестратор сам вызывает HTTP-сервис расширения.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI

from orchestrator.agent.loop import AgentLoop
from orchestrator.api import chat, errors, polling
from orchestrator.config import get_settings
from orchestrator.db.crypto import Cipher
from orchestrator.db.migrate import migrate
from orchestrator.db.pool import close_pool, init_pool
from orchestrator.db.repo.tenants import get_by_token
from orchestrator.errors import ErrorException
from orchestrator.llm.anthropic_provider import AnthropicProvider
from orchestrator.store import DialogStore, MemoryDialogStore, PgDialogStore
from orchestrator.tools.executor import ToolExecutor
from orchestrator.tools.registry import TOOLS
from orchestrator.transport.base import OneCTransport
from orchestrator.transport.direct import DirectTransport, TenantEndpoint
from orchestrator.transport.mock import MockTransport
from orchestrator.transport.pg_polling import PgPollingTransport, requeue_stale_forever

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    background: list[asyncio.Task[Any]] = []

    if settings.storage == "postgres":
        if not settings.encryption_key:
            raise ErrorException(
                RuntimeError(
                    "Не задан ENCRYPTION_KEY — без него нельзя хранить словарь "
                    "псевдонимов. Сгенерировать: openssl rand -base64 32"
                )
            )
        app.state.cipher = Cipher.from_base64(settings.encryption_key)
        await migrate(settings.database_url)
        await init_pool(settings.database_url)
        store: DialogStore = PgDialogStore(app.state.cipher)
    else:
        app.state.cipher = None
        store = MemoryDialogStore()

    transport = _build_transport(settings.transport, settings.tool_timeout_seconds)
    if settings.transport == "polling":
        background.append(asyncio.create_task(requeue_stale_forever()))

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
    app.state.store = store
    app.state.transport_mode = settings.transport

    log.info("orchestrator_started", transport=settings.transport, storage=settings.storage)
    try:
        yield
    finally:
        for task in background:
            task.cancel()
        if settings.storage == "postgres":
            await close_pool()


def _build_transport(mode: str, timeout_seconds: int) -> OneCTransport:
    if mode == "polling":
        return PgPollingTransport(timeout_seconds=timeout_seconds)
    if mode == "direct":
        return DirectTransport(_resolve_endpoint, timeout_seconds=timeout_seconds)
    return MockTransport()


async def _resolve_endpoint(tenant_id: str) -> TenantEndpoint | None:
    """Адрес и ключ подписи базы берутся из БД на каждый вызов, а не при старте."""
    settings = get_settings()
    cipher = Cipher.from_base64(settings.encryption_key or "")
    tenant = await get_by_token(tenant_id, cipher=cipher)
    if tenant is None or tenant.base_url is None or tenant.signing_key is None:
        return None
    return TenantEndpoint(
        tenant_id=tenant.id,
        base_url=tenant.base_url,
        token="",
        signing_key=tenant.signing_key,
    )


app = FastAPI(
    title="Бота — оркестратор ИИ-агента бухгалтера",
    description="Сверка поступлений и ЭСФ для «1С:Бухгалтерия для Казахстана» 3.0",
    version="0.1.0",
    lifespan=lifespan,
)

errors.install(app)
app.include_router(chat.router)
app.include_router(polling.router)


@app.get("/health", tags=["service"])
async def health() -> dict[str, object]:
    settings = get_settings()
    return {
        "status": "ok",
        "transport": getattr(app.state, "transport_mode", "unknown"),
        "storage": settings.storage,
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
