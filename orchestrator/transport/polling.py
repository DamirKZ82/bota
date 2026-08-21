"""Обратный режим: очередь задач, которую забирает фоновое задание 1С.

Для файловых баз и баз за NAT прямой вызов невозможен, поэтому инициатива у 1С:
она периодически (5–15 с во время активного диалога, ТЗ п.3.2) спрашивает
«есть ли работа», выполняет и присылает результат.

Реализация в памяти — очередь живёт внутри одного процесса оркестратора. Для
нескольких воркеров её нужно заменить на таблицу в Postgres с `FOR UPDATE SKIP
LOCKED`; интерфейс при этом не меняется.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from orchestrator.transport.base import OneCTransport, ToolCallError, ToolTimeout


@dataclass
class QueuedTask:
    id: str
    tenant_id: str
    onec_method: str
    params: dict[str, Any]
    future: asyncio.Future[dict[str, Any]] = field(repr=False)


class PollingTransport(OneCTransport):
    def __init__(self, *, timeout_seconds: int = 120) -> None:
        self._timeout = timeout_seconds
        self._pending: dict[str, asyncio.Queue[QueuedTask]] = defaultdict(asyncio.Queue)
        self._in_flight: dict[str, QueuedTask] = {}

    async def call(
        self, tenant_id: str, onec_method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        task = QueuedTask(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            onec_method=onec_method,
            params=params,
            future=loop.create_future(),
        )
        await self._pending[tenant_id].put(task)
        try:
            return await asyncio.wait_for(task.future, timeout=self._timeout)
        except TimeoutError as exc:
            self._in_flight.pop(task.id, None)
            raise ToolTimeout(onec_method, self._timeout) from exc

    # -- сторона 1С ---------------------------------------------------------

    async def lease(self, tenant_id: str, wait_seconds: float = 10.0) -> QueuedTask | None:
        """Выдать 1С следующую задачу. Долгий опрос, чтобы не долбить каждые 5 с впустую."""
        try:
            task = await asyncio.wait_for(
                self._pending[tenant_id].get(), timeout=wait_seconds
            )
        except TimeoutError:
            return None
        self._in_flight[task.id] = task
        return task

    def complete(self, task_id: str, result: dict[str, Any]) -> None:
        """1С прислала результат."""
        task = self._in_flight.pop(task_id, None)
        if task is None or task.future.done():
            return
        task.future.set_result(result)

    def fail(self, task_id: str, message: str) -> None:
        """1С сообщила об ошибке выполнения."""
        task = self._in_flight.pop(task_id, None)
        if task is None or task.future.done():
            return
        task.future.set_exception(ToolCallError(message))
