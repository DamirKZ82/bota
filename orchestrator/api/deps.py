"""Зависимости FastAPI: аутентификация базы и сборка компонентов запроса.

Multi-tenant: один оркестратор обслуживает много баз, каждая изолирована (ТЗ п.3.2).
Идентификация — токен базы в заголовке `Authorization`.

Реестр тенантов сейчас в памяти. Заменяется на таблицу в Postgres без изменения
сигнатур: `resolve_tenant` остаётся единственной точкой, где токен превращается
в `tenant_id`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from orchestrator.agent.loop import AgentLoop
from orchestrator.config import Settings, get_settings


@dataclass(frozen=True, slots=True)
class Tenant:
    id: str
    name: str
    masking_enabled: bool


# Заглушка реестра баз. Пилотные базы добавляются сюда до появления таблицы.
_TENANTS: dict[str, Tenant] = {
    "demo-token": Tenant(id="demo", name="Демо-база", masking_enabled=True),
}


async def resolve_tenant(
    authorization: Annotated[str | None, Header()] = None,
) -> Tenant:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Нужен токен базы: заголовок Authorization: Bearer <токен>",
        )
    tenant = _TENANTS.get(authorization.removeprefix("Bearer ").strip())
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неизвестный токен базы",
        )
    return tenant


def get_agent(request: Request) -> AgentLoop:
    return request.app.state.agent  # type: ignore[no-any-return]


SettingsDep = Annotated[Settings, Depends(get_settings)]
TenantDep = Annotated[Tenant, Depends(resolve_tenant)]
AgentDep = Annotated[AgentLoop, Depends(get_agent)]
