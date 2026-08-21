"""Обратный транспорт поверх очереди в Postgres (A.0.1).

Отличие от очереди в памяти — в том, где ждёт вызывающая сторона. В памяти это
был `asyncio.Future`, существующий только в том процессе, что поставил задачу.
Здесь ожидание — опрос строки в таблице, поэтому задачу может поставить один
воркер, а результат принять другой.

Опрос, а не `LISTEN/NOTIFY`, сознательно: интервал в полсекунды на фоне сверки,
которая идёт десятки секунд, ничего не стоит, а `LISTEN` требует отдельного
соединения на процесс и переподключения после каждого обрыва.
"""

from __future__ import annotations

import asyncio
import json

import structlog

from orchestrator.db.repo import poll_tasks
from orchestrator.tools.envelope import ToolRequest, ToolResponse
from orchestrator.transport.base import OneCTransport, TransportError, TransportTimeout

log = structlog.get_logger(__name__)


class PgPollingTransport(OneCTransport):
    def __init__(self, *, timeout_seconds: int = 120, poll_interval: float = 0.5) -> None:
        self._timeout = timeout_seconds
        self._poll_interval = poll_interval

    async def call(self, tenant_id: str, request: ToolRequest) -> ToolResponse:
        task_id = await poll_tasks.enqueue(
            tenant_id=tenant_id,
            tool=request.tool,
            request=json.loads(request.model_dump_json(by_alias=True)),
        )
        try:
            return await asyncio.wait_for(
                self._await_response(tenant_id, task_id, request.tool),
                timeout=self._timeout,
            )
        except TimeoutError as err:
            await poll_tasks.expire(tenant_id=tenant_id, task_id=task_id)
            raise TransportTimeout(request.tool, self._timeout) from err

    async def _await_response(
        self, tenant_id: str, task_id: str, tool: str
    ) -> ToolResponse:
        while True:
            state = await poll_tasks.get_state(tenant_id=tenant_id, task_id=task_id)
            if state is None:
                raise TransportError(f"Задача «{tool}» исчезла из очереди")

            if state.status == "done":
                if state.response is None:
                    raise TransportError(f"«{tool}» завершился без ответа")
                try:
                    return ToolResponse.model_validate(state.response)
                except ValueError as err:
                    raise TransportError(
                        f"1С прислала непонятный ответ на «{tool}»: {err}"
                    ) from err
            if state.status == "failed":
                raise TransportError(
                    state.error_message or f"1С сообщила об ошибке в «{tool}»"
                )
            if state.status == "expired":
                raise TransportTimeout(tool, self._timeout)

            await asyncio.sleep(self._poll_interval)


async def requeue_stale_forever(interval_seconds: int = 60) -> None:
    """Фоновая задача: возвращает в очередь задачи, которые 1С забрала и не вернула."""
    while True:
        try:
            returned = await poll_tasks.requeue_stale()
            if returned:
                log.info("poll_tasks_requeued", count=returned)
        except Exception as err:  # фоновая задача не должна умирать
            log.warning("poll_tasks_requeue_failed", error=str(err))
        await asyncio.sleep(interval_seconds)
