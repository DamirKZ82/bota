"""Обратный транспорт: маршруты, которые опрашивает фоновое задание 1С (ТЗ п.3.2).

Протокол на стороне 1С:

1. `GET /v1/poll/next` — вернёт задачу или 204, если работы нет;
2. выполнить экспортную функцию с именем `onec_method`;
3. `POST /v1/poll/{task_id}/result` или `/error`.

Взятая задача арендуется на `lease_seconds`. Если 1С не вернёт результат за это
время, задача уйдёт обратно в очередь — так падение фонового задания не оставляет
бухгалтера ждать ответа, которого не будет.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field

from orchestrator.api.deps import SettingsDep, TenantDep
from orchestrator.db.repo import poll_tasks
from orchestrator.errors import WarnException

router = APIRouter(prefix="/v1/poll", tags=["polling"])


class TaskView(BaseModel):
    task_id: str
    onec_method: str = Field(description="Имя экспортной функции расширения")
    params: dict[str, Any]


class ResultIn(BaseModel):
    result: dict[str, Any]


class ErrorIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


def _require_polling(settings: SettingsDep) -> None:
    if settings.transport != "polling":
        raise WarnException(
            409, "Оркестратор работает не в режиме поллинга, очередь не используется"
        )


@router.get("/next", response_model=TaskView | None)
async def next_task(
    tenant: TenantDep,
    settings: SettingsDep,
    lease_seconds: int = 180,
) -> Response | TaskView:
    _require_polling(settings)
    task = await poll_tasks.lease(tenant_id=tenant.id, lease_seconds=lease_seconds)
    if task is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return TaskView(task_id=task.id, onec_method=task.onec_method, params=task.params)


@router.post("/{task_id}/result", status_code=202)
async def submit_result(
    task_id: str, body: ResultIn, tenant: TenantDep, settings: SettingsDep
) -> dict[str, str]:
    _require_polling(settings)
    if not await poll_tasks.complete(
        tenant_id=tenant.id, task_id=task_id, result=body.result
    ):
        # Чаще всего это опоздавший ответ: аренда истекла и задачу уже вернули
        # в очередь. Молча принимать такой результат нельзя — он может быть
        # посчитан дважды.
        raise WarnException(409, "Задача уже закрыта или не арендована этой базой")
    return {"status": "accepted"}


@router.post("/{task_id}/error", status_code=202)
async def submit_error(
    task_id: str, body: ErrorIn, tenant: TenantDep, settings: SettingsDep
) -> dict[str, str]:
    _require_polling(settings)
    if not await poll_tasks.fail(
        tenant_id=tenant.id, task_id=task_id, message=body.message
    ):
        raise WarnException(409, "Задача уже закрыта или не арендована этой базой")
    return {"status": "accepted"}
