"""Хранилище диалогов: две реализации за одним интерфейсом.

`MemoryDialogStore` нужен для разработки на моках — оркестратор поднимается без
Postgres. `PgDialogStore` — рабочий режим: история переживает перезапуск, и
диалог может продолжиться на другом воркере.

Хранится только рабочая история модели, а она уже в псевдонимах: маскирование
выполняет расширение 1С (A.0.5). Отображаемая пользователю переписка живёт там же.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from orchestrator.db.repo import dialogs as repo
from orchestrator.llm.base import Turn


class DialogStore(Protocol):
    async def open(
        self, *, tenant_id: str, user_key: str, dialog_id: str | None
    ) -> str: ...

    async def load(self, *, tenant_id: str, dialog_id: str) -> list[Turn]: ...

    async def save(
        self, *, tenant_id: str, dialog_id: str, turns: list[Turn], already_saved: int
    ) -> None: ...

    async def close(self, *, tenant_id: str, dialog_id: str) -> None: ...


class MemoryDialogStore:
    """Для локальной отладки. Всё теряется вместе с процессом — и это нормально."""

    def __init__(self) -> None:
        self._turns: dict[str, list[Turn]] = {}

    async def open(self, *, tenant_id: str, user_key: str, dialog_id: str | None) -> str:
        return dialog_id or str(uuid.uuid4())

    async def load(self, *, tenant_id: str, dialog_id: str) -> list[Turn]:
        return list(self._turns.get(dialog_id, []))

    async def save(
        self, *, tenant_id: str, dialog_id: str, turns: list[Turn], already_saved: int
    ) -> None:
        self._turns[dialog_id] = turns

    async def close(self, *, tenant_id: str, dialog_id: str) -> None:
        self._turns.pop(dialog_id, None)


class PgDialogStore:
    async def open(self, *, tenant_id: str, user_key: str, dialog_id: str | None) -> str:
        if dialog_id and await repo.exists(tenant_id=tenant_id, dialog_id=dialog_id):
            return dialog_id
        return await repo.create(tenant_id=tenant_id, user_key=user_key)

    async def load(self, *, tenant_id: str, dialog_id: str) -> list[Turn]:
        return await repo.load_turns(tenant_id=tenant_id, dialog_id=dialog_id)

    async def save(
        self, *, tenant_id: str, dialog_id: str, turns: list[Turn], already_saved: int
    ) -> None:
        await repo.append_turns(
            tenant_id=tenant_id,
            dialog_id=dialog_id,
            turns=turns[already_saved:],
            from_seq=already_saved,
        )

    async def close(self, *, tenant_id: str, dialog_id: str) -> None:
        await repo.close(tenant_id=tenant_id, dialog_id=dialog_id)
