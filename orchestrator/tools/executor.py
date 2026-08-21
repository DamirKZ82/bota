"""Исполнитель инструментов: валидация → вызов 1С → валидация → ответ модели.

Порядок шагов и есть гарантия принципа «модель не имеет прямого доступа к базе»
(ТЗ п.3.1):

1. вход от модели проверяется контрактом — произвольные поля не пройдут;
2. вызывается только инструмент из реестра — произвольные запросы невозможны;
3. ответ 1С проверяется контрактом — модель не увидит поле, которого нет в схеме.

Маскирования здесь нет: его выполняет расширение 1С, где живёт таблица
псевдонимов (A.0.5). Оркестратор передаёт флаг `masking` в контексте вызова и
соответствий не знает — из его памяти и журнала утекать нечему.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from orchestrator.tools.enums import ErrorCode
from orchestrator.tools.envelope import CallContext, ToolRequest
from orchestrator.tools.registry import BY_NAME, ToolSpec
from orchestrator.transport.base import OneCTransport, TransportError, TransportTimeout


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """Результат вызова, готовый к отправке модели и в журнал."""

    tool: str
    ok: bool
    payload: str
    """JSON для tool_result."""

    duration_ms: int = 0
    error_code: ErrorCode | None = None
    plans: tuple[tuple[str, str], ...] = ()
    """Пары (plan_id, action) из ответа — для журнала предложенных планов."""


class ToolExecutor:
    def __init__(self, transport: OneCTransport) -> None:
        self._transport = transport

    async def execute(
        self,
        *,
        tenant_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        context: CallContext,
    ) -> ToolOutcome:
        started = time.monotonic()

        spec = BY_NAME.get(tool_name)
        if spec is None:
            return _error(
                tool_name,
                ErrorCode.NOT_FOUND,
                f"Инструмента «{tool_name}» не существует",
                started,
            )

        try:
            # by_alias — наружу уходят имена из Приложения А («from», «to»).
            request_args = json.loads(
                spec.input_model.model_validate(arguments).model_dump_json(by_alias=True)
            )
        except ValidationError as err:
            # Ошибку валидации отдаём модели: она может исправить параметры сама.
            return _error(
                tool_name, ErrorCode.BAD_ARGS, f"Неверные параметры: {_short(err)}", started
            )

        request = ToolRequest(tool=tool_name, args=request_args, context=context)

        try:
            response = await self._transport.call(tenant_id, request)
        except TransportTimeout as err:
            return _error(tool_name, ErrorCode.TIMEOUT, str(err), started)
        except TransportError as err:
            return _error(tool_name, ErrorCode.INTERNAL, str(err), started)

        if not response.ok:
            error = response.error
            return _error(
                tool_name,
                error.code if error else ErrorCode.INTERNAL,
                error.message if error else "Расширение вернуло ошибку без описания",
                started,
                details=error.details if error else None,
            )

        try:
            result = spec.output_model.model_validate(response.result or {})
        except ValidationError as err:
            # Это не ошибка модели, а рассинхрон контракта с расширением.
            return _error(
                tool_name,
                ErrorCode.INTERNAL,
                f"База вернула ответ, не соответствующий контракту: {_short(err)}",
                started,
            )

        payload = result.model_dump_json(by_alias=True)
        return ToolOutcome(
            tool=tool_name,
            ok=True,
            payload=payload,
            duration_ms=_elapsed(started),
            plans=_extract_plans(json.loads(payload)),
        )

    @staticmethod
    def spec(tool_name: str) -> ToolSpec | None:
        return BY_NAME.get(tool_name)


def _extract_plans(payload: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """Находит plan_id в ответе — на верхнем уровне и в пачке черновиков.

    Нужно, чтобы записать предложенный план в журнал, не разбирая каждый тип
    результата отдельно.
    """
    plans: list[tuple[str, str]] = []
    if isinstance(payload.get("plan_id"), str):
        plans.append((payload["plan_id"], str(payload.get("action", ""))))
    for item in payload.get("items", []) or []:
        if isinstance(item, dict) and isinstance(item.get("plan_id"), str):
            plans.append((item["plan_id"], "create_receipt_from_esf"))
    return tuple(plans)


def _elapsed(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _error(
    tool: str,
    code: ErrorCode,
    message: str,
    started: float,
    details: dict[str, Any] | None = None,
) -> ToolOutcome:
    body: dict[str, Any] = {"error": {"code": code.value, "message": message}}
    if details:
        body["error"]["details"] = details
    return ToolOutcome(
        tool=tool,
        ok=False,
        payload=json.dumps(body, ensure_ascii=False),
        duration_ms=_elapsed(started),
        error_code=code,
    )


def _short(err: ValidationError, limit: int = 3) -> str:
    parts = [
        f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in err.errors()[:limit]
    ]
    return "; ".join(parts)
