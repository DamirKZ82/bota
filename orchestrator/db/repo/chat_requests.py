"""Состояние запроса к агенту в Postgres — реализация ProgressStore.

Каждое обновление шага это один UPDATE. За запрос их 4–7, то есть нагрузка
ничтожна по сравнению с самим обращением к модели.
"""

from __future__ import annotations

from orchestrator.db.pool import execute_db, fetch_one
from orchestrator.progress import Progress


class PgProgressStore:
    async def start(
        self, *, tenant_id: str, request_id: str, dialog_id: str, user_key: str
    ) -> None:
        await execute_db(
            """
            INSERT INTO chat_requests (request_id, tenant_id, dialog_id, user_key)
            VALUES ($1, $2, $3, $4)
            """,
            request_id,
            tenant_id,
            dialog_id,
            user_key,
        )

    async def step(
        self, *, tenant_id: str, request_id: str, step_no: int, label: str, tool: str | None
    ) -> None:
        await execute_db(
            """
            UPDATE chat_requests
               SET step_no = $3, step_label = $4, tool = $5, updated_at = now()
             WHERE tenant_id = $1
               AND request_id = $2
               AND status = 'running'
            """,
            tenant_id,
            request_id,
            step_no,
            label,
            tool,
        )

    async def finish(
        self, *, tenant_id: str, request_id: str, answer: str, calls: list[str]
    ) -> None:
        await execute_db(
            """
            UPDATE chat_requests
               SET status = 'done', answer = $3, calls = $4,
                   step_label = 'Готово', updated_at = now(), finished_at = now()
             WHERE tenant_id = $1
               AND request_id = $2
            """,
            tenant_id,
            request_id,
            answer,
            calls,
        )

    async def fail(self, *, tenant_id: str, request_id: str, message: str) -> None:
        await execute_db(
            """
            UPDATE chat_requests
               SET status = 'failed', error_message = $3,
                   updated_at = now(), finished_at = now()
             WHERE tenant_id = $1
               AND request_id = $2
            """,
            tenant_id,
            request_id,
            message,
        )

    async def get(self, *, tenant_id: str, request_id: str) -> Progress | None:
        row = await fetch_one(
            """
            SELECT request_id, dialog_id, status, step_no, step_label, tool,
                   answer, calls, error_message, started_at, updated_at
              FROM chat_requests
             WHERE tenant_id = $1
               AND request_id = $2
            """,
            tenant_id,
            request_id,
        )
        if row is None:
            return None
        return Progress(
            request_id=str(row["request_id"]),
            dialog_id=str(row["dialog_id"]),
            status=row["status"],
            step_no=row["step_no"],
            step_label=row["step_label"],
            tool=row["tool"],
            answer=row["answer"],
            error_message=row["error_message"],
            started_at=row["started_at"],
            updated_at=row["updated_at"],
            calls=list(row["calls"] or []),
        )
