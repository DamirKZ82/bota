"""Хранилище диалогов: две реализации за одним интерфейсом.

`MemoryDialogStore` нужен для разработки на моках — оркестратор поднимается без
Postgres. `PgDialogStore` — рабочий режим: история переживает перезапуск, и
диалог может продолжиться на другом воркере.

Обе хранят одно и то же: замаскированную рабочую историю и словарь псевдонимов.
Отображаемая пользователю переписка живёт в 1С.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from orchestrator.db.crypto import Cipher
from orchestrator.db.repo import dialogs as repo
from orchestrator.llm.base import Turn
from orchestrator.masking.masker import MaskingSession


class DialogStore(Protocol):
    async def open(
        self, *, tenant_id: str, user_key: str, dialog_id: str | None
    ) -> str: ...

    async def load(
        self, *, tenant_id: str, dialog_id: str, masking_enabled: bool
    ) -> tuple[list[Turn], MaskingSession]: ...

    async def save(
        self,
        *,
        tenant_id: str,
        dialog_id: str,
        turns: list[Turn],
        already_saved: int,
        masking: MaskingSession,
    ) -> None: ...

    async def close(self, *, tenant_id: str, dialog_id: str) -> None: ...


class MemoryDialogStore:
    """Для локальной отладки. Всё теряется вместе с процессом — и это нормально."""

    def __init__(self) -> None:
        self._turns: dict[str, list[Turn]] = {}
        self._masking: dict[str, MaskingSession] = {}

    async def open(self, *, tenant_id: str, user_key: str, dialog_id: str | None) -> str:
        return dialog_id or str(uuid.uuid4())

    async def load(
        self, *, tenant_id: str, dialog_id: str, masking_enabled: bool
    ) -> tuple[list[Turn], MaskingSession]:
        masking = self._masking.setdefault(
            dialog_id, MaskingSession(enabled=masking_enabled)
        )
        return list(self._turns.get(dialog_id, [])), masking

    async def save(
        self,
        *,
        tenant_id: str,
        dialog_id: str,
        turns: list[Turn],
        already_saved: int,
        masking: MaskingSession,
    ) -> None:
        self._turns[dialog_id] = turns
        self._masking[dialog_id] = masking

    async def close(self, *, tenant_id: str, dialog_id: str) -> None:
        self._turns.pop(dialog_id, None)
        self._masking.pop(dialog_id, None)


class PgDialogStore:
    def __init__(self, cipher: Cipher) -> None:
        self._cipher = cipher

    async def open(self, *, tenant_id: str, user_key: str, dialog_id: str | None) -> str:
        if dialog_id and await repo.exists(tenant_id=tenant_id, dialog_id=dialog_id):
            return dialog_id
        return await repo.create(tenant_id=tenant_id, user_key=user_key)

    async def load(
        self, *, tenant_id: str, dialog_id: str, masking_enabled: bool
    ) -> tuple[list[Turn], MaskingSession]:
        turns = await repo.load_turns(tenant_id=tenant_id, dialog_id=dialog_id)
        masking = await repo.load_masking(
            tenant_id=tenant_id,
            dialog_id=dialog_id,
            cipher=self._cipher,
            enabled=masking_enabled,
        )
        return turns, masking

    async def save(
        self,
        *,
        tenant_id: str,
        dialog_id: str,
        turns: list[Turn],
        already_saved: int,
        masking: MaskingSession,
    ) -> None:
        await repo.append_turns(
            tenant_id=tenant_id,
            dialog_id=dialog_id,
            turns=turns[already_saved:],
            from_seq=already_saved,
        )
        await repo.save_masking(
            tenant_id=tenant_id,
            dialog_id=dialog_id,
            cipher=self._cipher,
            session=masking,
        )

    async def close(self, *, tenant_id: str, dialog_id: str) -> None:
        await repo.close(tenant_id=tenant_id, dialog_id=dialog_id)
