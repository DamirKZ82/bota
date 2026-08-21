"""Конверт запроса и ответа инструмента (Приложение А, A.0.2).

Один формат для обоих транспортов: в прямом режиме это тело HTTP-запроса к
расширению, в режиме поллинга — тело задачи в очереди. Инструменты о разнице
не знают.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.tools.enums import RETRYABLE_ERRORS, ErrorCode


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class CallContext(Envelope):
    """Контекст вызова: кто спрашивает и нужно ли маскировать ответ.

    Маскирование выполняет расширение 1С — таблица псевдонимов живёт там
    (A.0.5). Оркестратор только передаёт флаг и никогда не видит соответствия
    «псевдоним → реальное значение».
    """

    user_id: str = Field(description="UUID пользователя 1С")
    session_id: str = Field(description="UUID диалога; ключ таблицы псевдонимов в 1С")
    locale: str = Field(default="ru")
    masking: bool = True


class ToolRequest(Envelope):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    context: CallContext


class ToolWarning(Envelope):
    code: str
    message: str


class ToolError(Envelope):
    code: ErrorCode
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False


class ToolResponse(Envelope):
    ok: bool
    tool: str
    request_id: str | None = None
    duration_ms: int = 0
    result: dict[str, Any] | None = None
    warnings: list[ToolWarning] = Field(default_factory=list)
    error: ToolError | None = None

    @classmethod
    def failure(
        cls,
        tool: str,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> ToolResponse:
        """Ошибка, сформированная оркестратором, а не 1С.

        Такое бывает, когда запрос до базы не дошёл: неизвестный инструмент,
        неверные параметры, таймаут очереди.
        """
        return cls(
            ok=False,
            tool=tool,
            error=ToolError(
                code=code,
                message=message,
                details=details or {},
                retryable=code in RETRYABLE_ERRORS,
            ),
        )
