"""Транспорт до расширения 1С (ТЗ п.3.2).

Два режима с одинаковым интерфейсом:

* **direct** — оркестратор сам зовёт HTTP-сервис расширения. Для серверных баз
  с доступным адресом.
* **polling** — фоновое задание в 1С забирает задачи из очереди оркестратора и
  отдаёт результаты. Для файловых баз и баз за NAT.

Цикл агента о разнице не знает — он вызывает `call()` и ждёт результат.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ToolCallError(Exception):
    """Инструмент не отработал. Текст уходит модели как tool_result с is_error."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class ToolTimeout(ToolCallError):
    """1С не ответила за отведённое время."""

    def __init__(self, method: str, seconds: int) -> None:
        super().__init__(
            f"База 1С не ответила на «{method}» за {seconds} с. "
            "Возможно, фоновое задание агента не запущено или сверка идёт дольше обычного.",
            retryable=True,
        )


class OneCTransport(ABC):
    """Канал вызова экспортных функций расширения."""

    @abstractmethod
    async def call(self, tenant_id: str, onec_method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Вызвать функцию расширения и вернуть её JSON-результат.

        Args:
            tenant_id: идентификатор базы (multi-tenant, ТЗ п.3.2).
            onec_method: русское имя экспортной функции из реестра инструментов.
            params: параметры, уже провалидированные контрактом.

        Raises:
            ToolCallError: любая ошибка вызова, включая таймаут.
        """
        raise NotImplementedError
