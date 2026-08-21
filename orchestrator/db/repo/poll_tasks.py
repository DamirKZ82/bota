"""Очередь обратного транспорта в Postgres (A.0.1, режим поллинга).

Заменяет очередь в памяти процесса: с ней оркестратор мог работать только в один
воркер, потому что задача, поставленная одним процессом, была не видна другому.

В очереди лежат конверты `ToolRequest` / `ToolResponse` целиком (A.0.2) — те же
объекты, что и в прямом режиме, поэтому 1С обрабатывает их одинаково.

Выдача задачи — `FOR UPDATE SKIP LOCKED`: два воркера, одновременно спросившие
работу для одной базы, получат разные задачи, а не подерутся за одну.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from orchestrator.db.pool import execute_db, fetch_one, query_db


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    tool: str
    request: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TaskState:
    status: str
    response: dict[str, Any] | None
    error_message: str | None


async def enqueue(*, tenant_id: str, tool: str, request: dict[str, Any]) -> str:
    task_id = str(uuid.uuid4())
    await execute_db(
        """
        INSERT INTO poll_tasks (id, tenant_id, tool, request)
        VALUES ($1, $2, $3, $4)
        """,
        task_id,
        tenant_id,
        tool,
        request,
    )
    return task_id


async def lease(*, tenant_id: str, lease_seconds: int = 180) -> Task | None:
    """Выдать 1С следующую задачу и взять её в аренду."""
    row = await fetch_one(
        """
        WITH next_task AS (
            SELECT id
              FROM poll_tasks
             WHERE tenant_id = $1
               AND status = 'pending'
             ORDER BY created_at
             FOR UPDATE SKIP LOCKED
             LIMIT 1
        )
        UPDATE poll_tasks AS t
           SET status           = 'leased',
               attempts         = t.attempts + 1,
               leased_at        = now(),
               lease_expires_at = now() + make_interval(secs => $2)
          FROM next_task
         WHERE t.id = next_task.id
        RETURNING t.id, t.tool, t.request
        """,
        tenant_id,
        lease_seconds,
    )
    if row is None:
        return None
    return Task(id=str(row["id"]), tool=row["tool"], request=row["request"])


async def complete(*, tenant_id: str, task_id: str, response: dict[str, Any]) -> bool:
    rows = await query_db(
        """
        UPDATE poll_tasks
           SET status = 'done', response = $3, completed_at = now()
         WHERE tenant_id = $1
           AND id = $2
           AND status = 'leased'
        RETURNING id
        """,
        tenant_id,
        task_id,
        response,
    )
    return bool(rows)


async def fail(*, tenant_id: str, task_id: str, message: str) -> bool:
    rows = await query_db(
        """
        UPDATE poll_tasks
           SET status = 'failed', error_message = $3, completed_at = now()
         WHERE tenant_id = $1
           AND id = $2
           AND status = 'leased'
        RETURNING id
        """,
        tenant_id,
        task_id,
        message,
    )
    return bool(rows)


async def get_state(*, tenant_id: str, task_id: str) -> TaskState | None:
    row = await fetch_one(
        """
        SELECT status, response, error_message
          FROM poll_tasks
         WHERE tenant_id = $1
           AND id = $2
        """,
        tenant_id,
        task_id,
    )
    if row is None:
        return None
    return TaskState(
        status=row["status"],
        response=row["response"],
        error_message=row["error_message"],
    )


async def expire(*, tenant_id: str, task_id: str) -> None:
    """Пометить задачу протухшей, когда ждать ответа больше нет смысла."""
    await execute_db(
        """
        UPDATE poll_tasks
           SET status = 'expired', completed_at = now()
         WHERE tenant_id = $1
           AND id = $2
           AND status IN ('pending', 'leased')
        """,
        tenant_id,
        task_id,
    )


async def requeue_stale() -> int:
    """Вернуть в очередь задачи, аренда которых истекла.

    Случается, когда 1С забрала задачу и упала, не прислав результат. Вызывается
    по расписанию; после трёх попыток задача признаётся безнадёжной.
    """
    rows = await query_db(
        """
        UPDATE poll_tasks
           SET status = CASE WHEN attempts >= 3 THEN 'expired' ELSE 'pending' END,
               leased_at = NULL,
               lease_expires_at = NULL,
               completed_at = CASE WHEN attempts >= 3 THEN now() END
         WHERE status = 'leased'
           AND lease_expires_at < now()
        RETURNING id
        """
    )
    return len(rows)
