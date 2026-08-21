"""Абстракция провайдера LLM.

Вопрос 5 из раздела 10 ТЗ — допустимо ли отправлять данные во внешний LLM и нужен
ли локальный вариант — на момент старта не закрыт. Поэтому цикл агента работает
с этим интерфейсом, а не с SDK конкретного вендора: смена провайдера должна быть
заменой одного адаптера, а не переписыванием ядра.

Типы здесь намеренно свои, а не Anthropic-овские. Один раз потратив тридцать строк
на нейтральные блоки, мы не тащим формат конкретного вендора в журнал, в БД и в
маскировщик.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str


@dataclass(frozen=True, slots=True)
class ThinkingBlock:
    """Блок рассуждения. Хранится, чтобы вернуть модели без изменений на следующем шаге."""

    raw: Any


@dataclass(frozen=True, slots=True)
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False


AssistantBlock = TextBlock | ThinkingBlock | ToolUseBlock


@dataclass(frozen=True, slots=True)
class UserTurn:
    """Ход пользователя: либо текст, либо пачка результатов инструментов."""

    text: str | None = None
    tool_results: list[ToolResultBlock] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class AssistantTurn:
    blocks: list[AssistantBlock]
    stop_reason: str
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "".join(b.text for b in self.blocks if isinstance(b, TextBlock))

    @property
    def tool_uses(self) -> list[ToolUseBlock]:
        return [b for b in self.blocks if isinstance(b, ToolUseBlock)]


Turn = UserTurn | AssistantTurn


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]


class LLMProvider(ABC):
    """Минимум, который цикл агента требует от модели."""

    @abstractmethod
    async def complete(
        self,
        *,
        system: str,
        history: list[Turn],
        tools: list[ToolDefinition],
        effort: Literal["low", "medium", "high", "xhigh", "max"] = "high",
    ) -> AssistantTurn:
        """Один шаг диалога. Провайдер сам решает, как отобразить историю в свой формат."""
        raise NotImplementedError
