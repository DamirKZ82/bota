"""Диалоги: рабочая история для модели и словарь псевдонимов.

В таблицах лежит только то, что видела модель, — в псевдонимах. Отображаемую
пользователю переписку ведёт форма «Агент» в 1С (ТЗ п.7), поэтому реальные
наименования сюда не попадают.

Исключение одно: `dialog_aliases`, где расшифровка псевдонимов хранится
зашифрованной. Без неё после перезапуска оркестратора нельзя вернуть в ответ
реальные наименования и диалог пришлось бы начинать заново.
"""

from __future__ import annotations

import uuid
from typing import Any

from orchestrator.db.crypto import Cipher
from orchestrator.db.pool import execute_db, fetch_one, query_db
from orchestrator.llm.base import Turn
from orchestrator.llm.serde import turn_from_json, turn_to_json
from orchestrator.masking.masker import MaskingSession


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
    """Закрыть диалог. Словарь псевдонимов стирается сразу, не дожидаясь ретеншна."""
    await execute_db(
        "DELETE FROM dialog_aliases WHERE tenant_id = $1 AND dialog_id = $2",
        tenant_id,
        dialog_id,
    )
    await execute_db(
        "UPDATE dialogs SET closed_at = now() WHERE tenant_id = $1 AND id = $2",
        tenant_id,
        dialog_id,
    )


# -- словарь псевдонимов ----------------------------------------------------


async def load_masking(
    *, tenant_id: str, dialog_id: str, cipher: Cipher, enabled: bool
) -> MaskingSession:
    rows = await query_db(
        """
        SELECT alias, value_enc
          FROM dialog_aliases
         WHERE tenant_id = $1
           AND dialog_id = $2
        """,
        tenant_id,
        dialog_id,
    )
    session = MaskingSession(enabled=enabled)
    session.restore(
        {row["alias"]: cipher.decrypt(bytes(row["value_enc"])) for row in rows}
    )
    return session


async def save_masking(
    *, tenant_id: str, dialog_id: str, cipher: Cipher, session: MaskingSession
) -> None:
    """Сохранить новые псевдонимы. Существующие не переписываются: они неизменны."""
    for alias, value in session.snapshot().items():
        await execute_db(
            """
            INSERT INTO dialog_aliases (dialog_id, tenant_id, alias, value_enc)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (dialog_id, alias) DO NOTHING
            """,
            dialog_id,
            tenant_id,
            alias,
            cipher.encrypt(value),
        )
