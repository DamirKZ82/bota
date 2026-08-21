"""Журнал вызовов и изменений (ТЗ п.8: всё логируется, хранение 12 месяцев).

Пока — запись в structlog. Постоянное хранилище появится вместе со схемой БД;
интерфейс `Journal.record()` при этом не изменится.

Важное свойство: сюда попадают уже замаскированные данные. Немаскированный ответ
1С не покидает памяти обработки запроса.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass
from typing import Any, Protocol

import structlog

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class JournalEntry:
    tenant_id: str
    dialog_id: str
    user: str
    kind: str
    """tool_call | plan_created | plan_applied | answer"""

    tool: str | None
    ok: bool
    details: dict[str, Any]
    at: dt.datetime


class Journal(Protocol):
    async def record(self, entry: JournalEntry) -> None: ...


class LogJournal:
    """Реализация по умолчанию — пишет в лог."""

    async def record(self, entry: JournalEntry) -> None:
        log.info("journal", **asdict(entry))
