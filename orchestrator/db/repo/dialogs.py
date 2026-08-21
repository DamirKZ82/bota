"""Диалоги: рабочая история модели.

В таблицах лежит только то, что видела модель, — в псевдонимах. Маскирование и
обратную подстановку делает расширение 1С (A.0.5), отображаемую пользователю
переписку ведёт форма «Агент» там же. Реальных наименований здесь нет вовсе.
"""

from __future__ import annotations

import uuid
from typing import Any

from orchestrator.db.pool import execute_db, fetch_one, query_db
from orchestrator.llm.base import Turn
from orchestrator.llm.serde import turn_from_json, turn_to_json


async def create(
    *, tenant_id: str, user_key: str, organization_uuid: str | None = None
) -> str:
    dialog_id = str(uuid.uuid4())
    await execute_db(
        """
        INSERT INTO dialogs (id, tenant_id, user_key, organization_uuid)
        VALUES ($1, $2, $3, $4)
        """,
        dialog_id,
        tenant_id,
        user_key,
        organization_uuid,
    )
    return dialog_id


async def exists(*, tenant_id: str, dialog_id: str) -> bool:
    row = await fetch_one(
        "SELECT 1 AS ok FROM dialogs WHERE tenant_id = $1 AND id = $2 AND closed_at IS NULL",
        tenant_id,
        dialog_id,
    )
    return row is not None


async def load_turns(*, tenant_id: str, dialog_id: str) -> list[Turn]:
    rows = await query_db(
        """
        SELECT content
          FROM dialog_turns
         WHERE tenant_id = $1
           AND dialog_id = $2
         ORDER BY seq
        """,
        tenant_id,
        dialog_id,
    )
    return [turn_from_json(row["content"]) for row in rows]


async def append_turns(
    *, tenant_id: str, dialog_id: str, turns: list[Turn], from_seq: int
) -> None:
    """Дописать ходы, начиная с указанного номера.

    `from_seq` — сколько ходов уже сохранено. Цикл агента возвращает историю
    целиком, а пишем мы только хвост: перезаписывать сохранённое незачем.
    """
    if not turns:
        return

    payload: list[tuple[Any, ...]] = [
        (
            dialog_id,
            tenant_id,
            from_seq + offset,
            "user" if turn_to_json(turn)["role"] == "user" else "assistant",
            turn_to_json(turn),
        )
        for offset, turn in enumerate(turns)
    ]

    for dialog, tenant, seq, role, content in payload:
        await execute_db(
            """
            INSERT INTO dialog_turns (dialog_id, tenant_id, seq, role, content)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (dialog_id, seq) DO NOTHING
            """,
            dialog,
            tenant,
            seq,
            role,
            content,
        )

    await execute_db(
        "UPDATE dialogs SET last_activity_at = now() WHERE tenant_id = $1 AND id = $2",
        tenant_id,
        dialog_id,
    )


async def count_turns(*, tenant_id: str, dialog_id: str) -> int:
    row = await fetch_one(
        "SELECT count(*) AS n FROM dialog_turns WHERE tenant_id = $1 AND dialog_id = $2",
        tenant_id,
        dialog_id,
    )
    return int(row["n"]) if row else 0


async def close(*, tenant_id: str, dialog_id: str) -> None:
    await execute_db(
        "UPDATE dialogs SET closed_at = now() WHERE tenant_id = $1 AND id = $2",
        tenant_id,
        dialog_id,
    )
