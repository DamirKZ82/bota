"""Зависимости FastAPI: аутентификация базы и компоненты запроса.

Multi-tenant: один оркестратор обслуживает много баз, каждая изолирована (ТЗ п.3.2).
Идентификация — токен базы в заголовке `Authorization`; в БД хранится только его
SHA-256, поэтому дамп таблицы не даёт доступа ни к одной базе.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request

from orchestrator.agent.loop import AgentLoop
from orchestrator.config import Settings, get_settings
from orchestrator.db.repo.tenants import Tenant
from orchestrator.db.repo.tenants import get_by_token as get_tenant_by_token
from orchestrator.errors import WarnException
from orchestrator.store import DialogStore

#: Демо-база для режима разработки (transport=mock), когда Postgres не поднят.
DEMO_TOKEN = "demo-token"
DEMO_TENANT = Tenant(
    id="demo",
    name="Демо-база",
    transport="mock",
    base_url=None,
    masking_enabled=True,
    signing_key=None,
)


async def resolve_tenant(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Tenant:
    if not authorization or not authorization.startswith("Bearer "):
        raise WarnException(401, "Нужен токен базы: заголовок Authorization: Bearer <токен>")

    token = authorization.removeprefix("Bearer ").strip()
    settings: Settings = get_settings()

    if settings.storage == "memory":
        if token != DEMO_TOKEN:
            raise WarnException(401, "Неизвестный токен базы")
        return DEMO_TENANT

    tenant = await get_tenant_by_token(token, cipher=request.app.state.cipher)
    if tenant is None:
        raise WarnException(401, "Неизвестный токен базы")
    return tenant


def get_agent(request: Request) -> AgentLoop:
    return request.app.state.agent  # type: ignore[no-any-return]


def get_store(request: Request) -> DialogStore:
    return request.app.state.store  # type: ignore[no-any-return]


SettingsDep = Annotated[Settings, Depends(get_settings)]
TenantDep = Annotated[Tenant, Depends(resolve_tenant)]
AgentDep = Annotated[AgentLoop, Depends(get_agent)]
StoreDep = Annotated[DialogStore, Depends(get_store)]
