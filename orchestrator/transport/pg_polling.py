"""Обратный транспорт поверх очереди в Postgres.

Отличие от `polling.py` (очередь в памяти) — в том, где ждёт вызывающая сторона.
В памяти это был `asyncio.Future`, который существует только в том процессе, что
поставил задачу. Здесь ожидание — опрос строки в таблице, поэтому задачу может
поставить один воркер, а результат принять другой.

Опрос, а не `LISTEN/NOTIFY`, сознательно: интервал в полсекунды на фоне сверки,
которая идёт десятки секунд, ничего не стоит, а `LISTEN` требует отдельного
соединения на процесс и переподключения после обрыва.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from orchestrator.db.repo import poll_tasks
from orchestrator.transport.base import OneCTransport, ToolCallError, ToolTimeout

log = structlog.get_logger(__name__)


class PgPollingTransport(OneCTransport):
    def __init__(self, *, timeout_seconds: int = 120, poll_interval: float = 0.5) -> None:
        self._timeout = timeout_seconds
        self._poll_interval = poll_interval

    async def call(
        self, tenant_id: str, onec_method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        task_id = await poll_tasks.enqueue(
            tenant_id=tenant_id, onec_method=onec_method, params=params
        )
        try:
            return await asyncio.wait_for(
                self._await_result(tenant_id, task_id, onec_method),
                timeout=self._timeout,
            )
        except TimeoutError as err:
            await poll_tasks.expire(tenant_id=tenant_id, task_id=task_id)
            raise ToolTimeout(onec_method, self._timeout) from err

    async def _await_result(
        self, tenant_id: str, task_id: str, onec_method: str
    ) -> dict[str, Any]:
        while True:
            state = await poll_tasks.get_state(tenant_id=tenant_id, task_id=task_id)
            if state is None:
                raise ToolCallError(f"Задача «{onec_method}» исчезла из очереди")

            if state.status == "done":
                if state.result is None:
                    raise ToolCallError(f"«{onec_method}» завершился без результата")
                return state.result
            if state.status == "failed":
                raise ToolCallError(
                    state.error_message or f"1С сообщила об ошибке в «{onec_method}»"
                )
            if state.status == "expired":
                raise ToolTimeout(onec_method, self._timeout)

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
