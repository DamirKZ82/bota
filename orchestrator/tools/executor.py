"""Исполнитель инструментов: валидация → вызов 1С → валидация → маскирование.

Порядок шагов здесь и есть гарантия принципа «модель не имеет прямого доступа к
базе» (ТЗ п.3.1):

1. вход от модели проверяется контрактом — произвольные поля не пройдут;
2. вызывается только метод из реестра — произвольные запросы невозможны;
3. ответ 1С проверяется контрактом — модель не увидит поле, которого нет в схеме;
4. результат маскируется до того, как попадёт куда-либо ещё.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from orchestrator.masking.masker import MaskingSession
from orchestrator.tools.registry import BY_NAME, ToolSpec
from orchestrator.transport.base import OneCTransport, ToolCallError


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """Результат вызова, готовый к отправке модели и в журнал."""

    tool: str
    ok: bool
    payload: str
    """Замаскированный JSON — то, что уходит в tool_result."""

    raw_payload: dict[str, Any] | None = None
    """Немаскированный ответ. Живёт только в памяти запроса, в журнал не пишется."""


class ToolExecutor:
    def __init__(self, transport: OneCTransport) -> None:
        self._transport = transport

    async def execute(
        self,
        *,
        tenant_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        masking: MaskingSession,
    ) -> ToolOutcome:
        spec = BY_NAME.get(tool_name)
        if spec is None:
            return _error(tool_name, f"Инструмента «{tool_name}» не существует")

        try:
            request = spec.input_model.model_validate(arguments)
        except ValidationError as exc:
            # Ошибку валидации отдаём модели: она может исправить параметры сама.
            return _error(tool_name, f"Неверные параметры: {_short(exc)}")

        params = json.loads(request.model_dump_json())

        try:
            raw = await self._transport.call(tenant_id, spec.onec_method, params)
        except ToolCallError as exc:
            return _error(tool_name, str(exc))

        try:
            response = spec.output_model.model_validate(raw)
        except ValidationError as exc:
            # Это не ошибка модели, а рассинхрон контракта с расширением.
            return _error(
                tool_name,
                f"База вернула ответ, не соответствующий контракту «{spec.onec_method}»: {_short(exc)}",
            )

        clean = json.loads(response.model_dump_json())
        masked = masking.mask(clean)
        return ToolOutcome(
            tool=tool_name,
            ok=True,
            payload=json.dumps(masked, ensure_ascii=False, sort_keys=True),
            raw_payload=clean,
        )

    @staticmethod
    def spec(tool_name: str) -> ToolSpec | None:
        return BY_NAME.get(tool_name)


def _error(tool: str, message: str) -> ToolOutcome:
    return ToolOutcome(
        tool=tool,
        ok=False,
        payload=json.dumps({"error": message}, ensure_ascii=False),
    )


def _short(exc: ValidationError, limit: int = 3) -> str:
    parts = [
        f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:limit]
    ]
    return "; ".join(parts)
