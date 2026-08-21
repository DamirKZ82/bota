"""Цикл агента на подставном провайдере — без обращения к настоящей модели."""

from __future__ import annotations

from typing import Literal

from orchestrator.agent.loop import AgentLoop
from orchestrator.llm.base import (
    AssistantTurn,
    LLMProvider,
    TextBlock,
    ToolDefinition,
    ToolUseBlock,
    Turn,
)
from orchestrator.tools.envelope import CallContext
from orchestrator.tools.executor import ToolExecutor
from orchestrator.transport.mock import MockTransport

CONTEXT = CallContext(user_id="user-1", session_id="session-1", masking=False)


class ScriptedProvider(LLMProvider):
    """Отдаёт заранее заданную последовательность ходов."""

    def __init__(self, script: list[AssistantTurn]) -> None:
        self._script = script
        self.calls = 0
        self.last_tools: list[ToolDefinition] = []

    async def complete(
        self,
        *,
        system: str,
        history: list[Turn],
        tools: list[ToolDefinition],
        effort: Literal["low", "medium", "high", "xhigh", "max"] = "high",
    ) -> AssistantTurn:
        self.last_tools = tools
        turn = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        return turn


def _loop(provider: LLMProvider, *, max_tool_calls: int = 30) -> AgentLoop:
    return AgentLoop(
        provider=provider,
        executor=ToolExecutor(MockTransport()),
        max_tool_calls=max_tool_calls,
    )


async def test_цикл_вызывает_инструмент_и_возвращает_текст() -> None:
    provider = ScriptedProvider(
        [
            AssistantTurn(
                blocks=[ToolUseBlock(id="tu_1", name="get_context", input={})],
                stop_reason="tool_use",
            ),
            AssistantTurn(
                blocks=[TextBlock(text="Организация одна, период 2 квартал 2026.")],
                stop_reason="end_turn",
            ),
        ]
    )
    result, turns = await _loop(provider).run(
        tenant_id="demo",
        user_message="Что за база?",
        context=CONTEXT,
    )

    assert result.text.startswith("Организация одна")
    assert [c.tool for c in result.calls] == ["get_context"]
    assert not result.truncated
    # История пригодна для продолжения диалога.
    assert len(turns) == 4


async def test_лимит_вызовов_даёт_промежуточный_ответ_а_не_обрыв() -> None:
    """ТЗ п.6.1: при превышении лимита вернуть результат и спросить, продолжать ли."""
    provider = ScriptedProvider(
        [
            AssistantTurn(
                blocks=[ToolUseBlock(id="tu_1", name="get_context", input={})],
                stop_reason="tool_use",
            ),
            AssistantTurn(
                blocks=[ToolUseBlock(id="tu_2", name="get_context", input={})],
                stop_reason="tool_use",
            ),
            AssistantTurn(
                blocks=[TextBlock(text="Проверил часть. Продолжать?")],
                stop_reason="end_turn",
            ),
        ]
    )
    result, _ = await _loop(provider, max_tool_calls=1).run(
        tenant_id="demo",
        user_message="Сверь всё",
        context=CONTEXT,
    )

    assert result.truncated
    assert len(result.calls) == 1
    assert "Продолжать" in result.text


async def test_режим_только_чтение_прячет_планы_изменений() -> None:
    provider = ScriptedProvider(
        [AssistantTurn(blocks=[TextBlock(text="Готово")], stop_reason="end_turn")]
    )
    await _loop(provider).run(
        tenant_id="demo",
        user_message="Покажи расхождения",
        context=CONTEXT,
        allow_write_plans=False,
    )
    names = {t.name for t in provider.last_tools}
    assert not any(n.startswith("plan_") for n in names)
    assert "get_discrepancy" in names


async def test_псевдонимы_из_1с_остаются_в_ответе_нетронутыми() -> None:
    """Маскирует и раскрывает псевдонимы 1С (A.0.5); оркестратор их не трогает."""
    provider = ScriptedProvider(
        [
            AssistantTurn(
                blocks=[
                    TextBlock(text="По Контрагент-A1 расхождение 0,01 ₸ по НДС.")
                ],
                stop_reason="end_turn",
            )
        ]
    )
    result, _ = await _loop(provider).run(
        tenant_id="demo",
        user_message="Что с контрагентом?",
        context=CallContext(user_id="u", session_id="s", masking=True),
    )
    assert result.text == "По Контрагент-A1 расхождение 0,01 ₸ по НДС."


async def test_ошибка_инструмента_не_роняет_диалог() -> None:
    provider = ScriptedProvider(
        [
            AssistantTurn(
                blocks=[
                    ToolUseBlock(id="tu_1", name="get_counterparty", input={"bin": "нет"})
                ],
                stop_reason="tool_use",
            ),
            AssistantTurn(
                blocks=[TextBlock(text="Не удалось найти контрагента, уточните БИН.")],
                stop_reason="end_turn",
            ),
        ]
    )
    result, _ = await _loop(provider).run(
        tenant_id="demo",
        user_message="Проверь контрагента",
        context=CONTEXT,
    )
    assert result.calls[0].ok is False
    assert "уточните БИН" in result.text
