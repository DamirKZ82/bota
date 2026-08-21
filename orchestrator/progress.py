"""Индикация текущего шага для формы «Агент» (ТЗ п.8).

Ответ агента занимает десятки секунд — это устройство продукта, а не недоработка:
цепочка вызовов последовательна, каждый шаг зависит от предыдущего. Поэтому
пользователю показывают, что происходит прямо сейчас.

Механизм — опрос, а не стриминг. 1С не умеет потоково читать HTTP-ответ, зато
прекрасно опрашивает адрес раз в секунду. Тот же приём работает и при нескольких
процессах оркестратора, если прогресс лежит в БД.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Literal, Protocol

Status = Literal["running", "done", "failed"]

#: Что показывать пользователю на каждом инструменте. Формулировки — от первого
#: лица и на языке бухгалтера: он видит их в форме, а не в логе.
STEP_LABELS: dict[str, str] = {
    "get_context": "Читаю контекст базы",
    "reconcile_period": "Сверяю поступления и ЭСФ за период",
    "list_discrepancies": "Получаю список расхождений",
    "get_discrepancy": "Разбираю расхождение",
    "get_document": "Читаю документ",
    "find_esf_candidates": "Ищу подходящую ЭСФ",
    "find_receipt_candidates": "Ищу подходящее поступление",
    "get_counterparty": "Проверяю контрагента",
    "get_vat_turnover": "Сверяю обороты НДС",
    "get_journal": "Смотрю журнал действий",
    "get_settings": "Читаю настройки сверки",
    "open_object": "Готовлю ссылку на документ",
    "plan_set_link": "Готовлю план установки связи",
    "plan_adjust_lines": "Готовлю план правки строк",
    "plan_set_vat_mode": "Готовлю пересчёт по флагу «Сумма включает НДС»",
    "plan_create_correction": "Готовлю проект корректировки",
    "plan_create_receipt_from_esf": "Готовлю черновик поступления по ЭСФ",
    "plan_create_receipts_bulk": "Готовлю черновики поступлений",
    "mark_reviewed": "Ставлю пометку «рассмотрено»",
}

THINKING = "Обдумываю ответ"
ANSWERING = "Формулирую ответ"


def label_for(tool: str) -> str:
    """Метка шага. Незнакомый инструмент не должен ломать индикацию."""
    return STEP_LABELS.get(tool, f"Выполняю {tool}")


@dataclass
class Progress:
    """Состояние одного запроса пользователя."""

    request_id: str
    dialog_id: str
    status: Status = "running"
    step_no: int = 0
    step_label: str = THINKING
    tool: str | None = None
    answer: str | None = None
    error_message: str | None = None
    started_at: dt.datetime | None = None
    updated_at: dt.datetime | None = None
    calls: list[str] = field(default_factory=list)


class ProgressStore(Protocol):
    async def start(
        self, *, tenant_id: str, request_id: str, dialog_id: str, user_key: str
    ) -> None: ...

    async def step(
        self, *, tenant_id: str, request_id: str, step_no: int, label: str, tool: str | None
    ) -> None: ...

    async def finish(
        self, *, tenant_id: str, request_id: str, answer: str, calls: list[str]
    ) -> None: ...

    async def fail(self, *, tenant_id: str, request_id: str, message: str) -> None: ...

    async def get(self, *, tenant_id: str, request_id: str) -> Progress | None: ...


class MemoryProgressStore:
    """Для режима разработки: живёт внутри процесса."""

    def __init__(self) -> None:
        self._items: dict[str, Progress] = {}

    async def start(
        self, *, tenant_id: str, request_id: str, dialog_id: str, user_key: str
    ) -> None:
        now = dt.datetime.now(dt.UTC)
        self._items[request_id] = Progress(
            request_id=request_id,
            dialog_id=dialog_id,
            started_at=now,
            updated_at=now,
        )

    async def step(
        self, *, tenant_id: str, request_id: str, step_no: int, label: str, tool: str | None
    ) -> None:
        item = self._items.get(request_id)
        if item is None:
            return
        item.step_no = step_no
        item.step_label = label
        item.tool = tool
        item.updated_at = dt.datetime.now(dt.UTC)

    async def finish(
        self, *, tenant_id: str, request_id: str, answer: str, calls: list[str]
    ) -> None:
        item = self._items.get(request_id)
        if item is None:
            return
        item.status = "done"
        item.answer = answer
        item.calls = calls
        item.step_label = "Готово"
        item.updated_at = dt.datetime.now(dt.UTC)

    async def fail(self, *, tenant_id: str, request_id: str, message: str) -> None:
        item = self._items.get(request_id)
        if item is None:
            return
        item.status = "failed"
        item.error_message = message
        item.updated_at = dt.datetime.now(dt.UTC)

    async def get(self, *, tenant_id: str, request_id: str) -> Progress | None:
        return self._items.get(request_id)
