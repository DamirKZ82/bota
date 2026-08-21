"""Транспорт до расширения 1С (ТЗ п.3.2, Приложение А, A.0.1).

Два режима с одинаковым интерфейсом:

* **direct** — оркестратор сам зовёт HTTP-сервис расширения `/hs/bota/v1/tools/…`.
  Для серверных баз с доступным адресом.
* **polling** — фоновое задание 1С забирает задачи из очереди оркестратора и
  отдаёт результаты. Для файловых баз и баз за NAT.

Цикл агента о разнице не знает: он вызывает `call()` и получает `ToolResponse`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from orchestrator.tools.envelope import ToolRequest, ToolResponse


class TransportError(Exception):
    """Сбой канала: запрос до базы не дошёл или ответ непригоден.

    Ошибки самой предметной области сюда не попадают — они приезжают в
    `ToolResponse.error` с кодом из A.0.2.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class TransportTimeout(TransportError):
    def __init__(self, tool: str, seconds: int) -> None:
        super().__init__(
            f"База 1С не ответила на «{tool}» за {seconds} с. "
            "Возможно, фоновое задание агента не запущено или сверка идёт дольше обычного.",
            retryable=True,
        )


class OneCTransport(ABC):
    """Канал вызова инструментов расширения."""

    @abstractmethod
    async def call(self, tenant_id: str, request: ToolRequest) -> ToolResponse:
        """Выполнить инструмент в базе и вернуть ответ в конверте A.0.2.

        Args:
            tenant_id: идентификатор базы (multi-tenant, ТЗ п.3.2).
            request: конверт с именем инструмента, параметрами и контекстом.

        Raises:
            TransportError: канал не сработал (сеть, таймаут, битый ответ).
        """
        raise NotImplementedError
