"""Цикл tool calling (ТЗ п.6.1).

Цикл написан вручную, а не взят из `tool_runner` SDK, по трём причинам, каждая
из которых для этого продукта существенна:

* инструменты исполняются не здесь, а в базе 1С через транспорт, который может
  быть очередью с поллингом;
* каждый вызов уходит в конверте с контекстом сессии (A.0.2) — по `session_id`
  расширение подбирает таблицу псевдонимов для маскирования;
* лимит в 30 вызовов — не защита от зацикливания, а обещание пользователю: при
  превышении он должен получить промежуточный результат и вопрос, а не обрыв.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from orchestrator.agent.prompts import SYSTEM_PROMPT
from orchestrator.llm.base import (
    AssistantTurn,
    LLMProvider,
    ToolDefinition,
    ToolResultBlock,
    Turn,
    UserTurn,
)
from orchestrator.tools.enums import ErrorCode
from orchestrator.tools.envelope import CallContext
from orchestrator.tools.executor import ToolExecutor
from orchestrator.tools.registry import TOOLS, ToolSpec, readable_tools


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    """Запись о вызове для блока «Что я проверил» и для журнала (ТЗ п.8)."""

    tool: str
    ok: bool
    arguments: dict[str, object]
    duration_ms: int = 0
    error_code: ErrorCode | None = None
    plans: tuple[tuple[str, str], ...] = ()


@dataclass
class AgentResult:
    text: str
    calls: list[ToolCallRecord] = field(default_factory=list)
    truncated: bool = False
    """True, если упёрлись в лимит вызовов и ответ промежуточный."""

    usage: dict[str, int] = field(default_factory=dict)


class AgentLoop:
    def __init__(
        self,
        *,
        provider: LLMProvider,
        executor: ToolExecutor,
        max_tool_calls: int = 30,
        effort: Literal["low", "medium", "high", "xhigh", "max"] = "high",
    ) -> None:
        self._provider = provider
        self._executor = executor
        self._max_tool_calls = max_tool_calls
        self._effort = effort

    async def run(
        self,
        *,
        tenant_id: str,
        user_message: str,
        context: CallContext,
        history: list[Turn] | None = None,
        allow_write_plans: bool = True,
    ) -> tuple[AgentResult, list[Turn]]:
        """Отработать один запрос пользователя.

        Возвращает результат и обновлённую историю — её нужно сохранить, чтобы
        следующий запрос в том же диалоге видел контекст.
        """
        turns: list[Turn] = list(history or [])
        turns.append(UserTurn(text=user_message))

        specs: tuple[ToolSpec, ...] = TOOLS if allow_write_plans else readable_tools()
        tools = [
            ToolDefinition(
                name=s.name,
                description=s.description,
                input_schema=s.input_schema(),
            )
            for s in specs
        ]

        calls: list[ToolCallRecord] = []
        usage_total: dict[str, int] = {}
        truncated = False

        while True:
            answer: AssistantTurn = await self._provider.complete(
                system=SYSTEM_PROMPT,
                history=turns,
                tools=tools,
                effort=self._effort,
            )
            _accumulate(usage_total, answer.usage)
            turns.append(answer)

            if answer.stop_reason == "refusal":
                return (
                    AgentResult(
                        text=(
                            "Не удалось обработать запрос. Переформулируйте его "
                            "или обратитесь к администратору."
                        ),
                        calls=calls,
                        usage=usage_total,
                    ),
                    turns,
                )

            tool_uses = answer.tool_uses
            if not tool_uses:
                break

            if len(calls) + len(tool_uses) > self._max_tool_calls:
                # Лимит достигнут: сообщаем модели об этом её же каналом, чтобы она
                # сформулировала промежуточный итог и вопрос о продолжении.
                truncated = True
                turns.append(
                    UserTurn(
                        tool_results=[
                            ToolResultBlock(
                                tool_use_id=tu.id,
                                content=(
                                    f'{{"error": {{"code": "LIMIT_EXCEEDED", "message": '
                                    f'"Достигнут лимит в {self._max_tool_calls} вызовов. '
                                    'Подведи промежуточный итог по уже полученным данным '
                                    'и спроси пользователя, продолжать ли."}}}}'
                                ),
                                is_error=True,
                            )
                            for tu in tool_uses
                        ]
                    )
                )
                continue

            results: list[ToolResultBlock] = []
            for use in tool_uses:
                outcome = await self._executor.execute(
                    tenant_id=tenant_id,
                    tool_name=use.name,
                    arguments=use.input,
                    context=context,
                )
                calls.append(
                    ToolCallRecord(
                        tool=use.name,
                        ok=outcome.ok,
                        arguments=use.input,
                        duration_ms=outcome.duration_ms,
                        error_code=outcome.error_code,
                        plans=outcome.plans,
                    )
                )
                results.append(
                    ToolResultBlock(
                        tool_use_id=use.id,
                        content=outcome.payload,
                        is_error=not outcome.ok,
                    )
                )

            # Все результаты — одним пользовательским ходом; дробить их нельзя,
            # иначе модель перестанет вызывать инструменты параллельно.
            turns.append(UserTurn(tool_results=results))

        return (
            AgentResult(
                text=answer.text,
                calls=calls,
                truncated=truncated,
                usage=usage_total,
            ),
            turns,
        )


def _accumulate(total: dict[str, int], delta: dict[str, int]) -> None:
    for key, value in delta.items():
        total[key] = total.get(key, 0) + value
