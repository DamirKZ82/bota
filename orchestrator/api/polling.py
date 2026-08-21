"""Обратный транспорт: маршруты, которые опрашивает фоновое задание 1С (ТЗ п.3.2).

Протокол на стороне 1С простой:

1. `GET /v1/poll/next` — долгий опрос, вернёт задачу или 204;
2. выполнить экспортную функцию с именем `onec_method`;
3. `POST /v1/poll/{task_id}/result` или `/error`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from orchestrator.api.deps import TenantDep
from orchestrator.transport.polling import PollingTransport

router = APIRouter(prefix="/v1/poll", tags=["polling"])


class TaskView(BaseModel):
    task_id: str
    onec_method: str = Field(description="Имя экспортной функции расширения")
    params: dict[str, Any]


class ResultIn(BaseModel):
    result: dict[str, Any]


class ErrorIn(BaseModel):
    message: str


def _transport(request: Request) -> PollingTransport:
    transport = getattr(request.app.state, "polling", None)
    if not isinstance(transport, PollingTransport):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Оркестратор работает в прямом режиме, очередь не используется",
        )
    return transport


@router.get("/next", response_model=TaskView | None)
async def next_task(
    request: Request,
    tenant: TenantDep,
    wait: float = 10.0,
) -> Response | TaskView:
    task = await _transport(request).lease(tenant.id, wait_seconds=wait)
    if task is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return TaskView(task_id=task.id, onec_method=task.onec_method, params=task.params)


@router.post("/{task_id}/result", status_code=202)
async def submit_result(
    task_id: str, body: ResultIn, request: Request, tenant: TenantDep
) -> dict[str, str]:
    _transport(request).complete(task_id, body.result)
    return {"status": "accepted"}


@router.post("/{task_id}/error", status_code=202)
async def submit_error(
    task_id: str, body: ErrorIn, request: Request, tenant: TenantDep
) -> dict[str, str]:
    _transport(request).fail(task_id, body.message)
    return {"status": "accepted"}
