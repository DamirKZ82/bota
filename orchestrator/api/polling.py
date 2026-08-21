"""Обратный транспорт: маршруты, которые опрашивает фоновое задание 1С (A.0.1).

    GET  /api/v1/bases/{base_id}/tasks?wait=25
    POST /api/v1/bases/{base_id}/tasks/{task_id}/result
    POST /api/v1/bases/{base_id}/tasks/{task_id}/error

Тело задачи и результата — конверты `ToolRequest` / `ToolResponse`, те же, что и
в прямом режиме: расширение обрабатывает их одинаково.

Взятая задача арендуется на `lease_seconds`. Если 1С не вернёт ответ за это
время, задача уйдёт обратно в очередь — падение фонового задания не оставляет
бухгалтера ждать ответа, которого не будет.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, Field

from orchestrator.api.deps import SettingsDep, TenantDep
from orchestrator.db.repo import poll_tasks
from orchestrator.errors import WarnException
from orchestrator.tools.envelope import ToolRequest, ToolResponse

router = APIRouter(prefix="/api/v1/bases", tags=["polling"])

#: Шаг опроса очереди внутри длинного ожидания.
_TICK_SECONDS = 1.0


class TaskView(BaseModel):
    task_id: str
    request: ToolRequest


class ResultIn(BaseModel):
    response: ToolResponse


class ErrorIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


def _check_mode(settings: SettingsDep) -> None:
    if settings.transport != "polling":
        raise WarnException(
            409, "Оркестратор работает не в режиме поллинга, очередь не используется"
        )


def _check_base(base_id: str, tenant: TenantDep) -> None:
    """Токен и путь должны указывать на одну и ту же базу.

    Иначе база с валидным токеном могла бы читать чужую очередь, подставив
    другой base_id в URL.
    """
    if base_id != tenant.id:
        raise WarnException(403, "Токен выдан другой базе")


@router.get("/{base_id}/tasks", response_model=TaskView | None)
async def next_task(
    base_id: str,
    tenant: TenantDep,
    settings: SettingsDep,
    wait: Annotated[float, Query(ge=0, le=60)] = 25.0,
    lease_seconds: Annotated[int, Query(ge=30, le=900)] = 180,
) -> Response | TaskView:
    """Длинный опрос: держим соединение до `wait` секунд, пока не появится задача."""
    _check_mode(settings)
    _check_base(base_id, tenant)

    deadline = asyncio.get_running_loop().time() + max(0.0, float(wait))
    while True:
        task = await poll_tasks.lease(tenant_id=tenant.id, lease_seconds=lease_seconds)
        if task is not None:
            return TaskView(
                task_id=task.id, request=ToolRequest.model_validate(task.request)
            )
        if asyncio.get_running_loop().time() >= deadline:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        await asyncio.sleep(_TICK_SECONDS)


@router.post("/{base_id}/tasks/{task_id}/result", status_code=202)
async def submit_result(
    base_id: str,
    task_id: str,
    body: ResultIn,
    tenant: TenantDep,
    settings: SettingsDep,
) -> dict[str, str]:
    _check_mode(settings)
    _check_base(base_id, tenant)
    accepted = await poll_tasks.complete(
        tenant_id=tenant.id,
        task_id=task_id,
        response=body.response.model_dump(mode="json", by_alias=True),
    )
    if not accepted:
        # Чаще всего это опоздавший ответ: аренда истекла и задачу уже вернули
        # в очередь. Молча принимать его нельзя — он может быть посчитан дважды.
        raise WarnException(409, "Задача уже закрыта или не арендована этой базой")
    return {"status": "accepted"}


@router.post("/{base_id}/tasks/{task_id}/error", status_code=202)
async def submit_error(
    base_id: str,
    task_id: str,
    body: ErrorIn,
    tenant: TenantDep,
    settings: SettingsDep,
) -> dict[str, str]:
    _check_mode(settings)
    _check_base(base_id, tenant)
    if not await poll_tasks.fail(
        tenant_id=tenant.id, task_id=task_id, message=body.message
    ):
        raise WarnException(409, "Задача уже закрыта или не арендована этой базой")
    return {"status": "accepted"}
