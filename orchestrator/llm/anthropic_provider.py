"""Адаптер Claude API поверх LLMProvider.

Решения, которые стоит знать при чтении:

* **Стриминг всегда.** Сверка периода и разбор пачки расхождений дают длинные
  ответы; нестриминговый запрос с большим `max_tokens` упирается в HTTP-таймаут.
  Читаем финальное сообщение через `get_final_message()`.
* **Адаптивное мышление.** Классификация причины расхождения — рассуждение,
  а не пересказ; на Claude Opus 5 мышление включено по умолчанию, задаём явно.
* **Серверные фолбэки на отказ.** Если запрос будет отклонён политикой, API
  повторит его на резервной модели в рамках того же вызова, вместо того чтобы
  вернуть бухгалтеру пустой ответ.
"""

from __future__ import annotations

import json
from typing import Any, Literal

import anthropic

from orchestrator.llm.base import (
    AssistantBlock,
    AssistantTurn,
    LLMProvider,
    TextBlock,
    ThinkingBlock,
    ToolDefinition,
    ToolUseBlock,
    Turn,
    UserTurn,
)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider(LLMProvider):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "claude-opus-5",
        max_tokens: int = 16000,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        # Без api_key SDK сам разрешит учётные данные из окружения или профиля.
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    async def complete(
        self,
        *,
        system: str,
        history: list[Turn],
        tools: list[ToolDefinition],
        effort: Literal["low", "medium", "high", "xhigh", "max"] = "high",
    ) -> AssistantTurn:
        async with self._client.beta.messages.stream(
            model=self._model,
            max_tokens=self._max_tokens,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            # Системный промпт стабилен и кэшируется; за ним идёт тоже стабильный
            # список инструментов, а волатильная история — уже после кэш-точки.
            system=[
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=[
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                }
                for t in tools
            ],
            messages=[_to_api_message(turn) for turn in history],
        ) as stream:
            message = await stream.get_final_message()

        blocks: list[AssistantBlock] = []
        for block in message.content:
            if block.type == "text":
                blocks.append(TextBlock(text=block.text))
            elif block.type == "thinking":
                blocks.append(ThinkingBlock(raw=block.model_dump()))
            elif block.type == "tool_use":
                blocks.append(
                    ToolUseBlock(
                        id=block.id,
                        name=block.name,
                        # Вход всегда парсится как JSON, а не матчится строкой —
                        # экранирование в tool_use.input у моделей 4.6+ отличается.
                        input=dict(block.input) if isinstance(block.input, dict) else {},
                    )
                )

        return AssistantTurn(
            blocks=blocks,
            stop_reason=message.stop_reason or "end_turn",
            usage={
                "input_tokens": message.usage.input_tokens,
                "output_tokens": message.usage.output_tokens,
                "cache_read_input_tokens": getattr(
                    message.usage, "cache_read_input_tokens", 0
                )
                or 0,
            },
        )


def _to_api_message(turn: Turn) -> dict[str, Any]:
    """Отображение нейтрального хода в формат Messages API."""
    if isinstance(turn, UserTurn):
        if turn.tool_results:
            # Все результаты параллельных вызовов — одним user-сообщением;
            # разбивать их на несколько сообщений нельзя.
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": r.tool_use_id,
                        "content": r.content,
                        "is_error": r.is_error,
                    }
                    for r in turn.tool_results
                ],
            }
        return {"role": "user", "content": turn.text or ""}

    content: list[dict[str, Any]] = []
    for block in turn.blocks:
        if isinstance(block, ThinkingBlock):
            content.append(block.raw)
        elif isinstance(block, TextBlock):
            content.append({"type": "text", "text": block.text})
        elif isinstance(block, ToolUseBlock):
            content.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                }
            )
    return {"role": "assistant", "content": content}


def dumps(payload: Any) -> str:
    """JSON для tool_result: без ASCII-эскейпов, со стабильным порядком ключей.

    Стабильный порядок нужен не для красоты — расходящийся порядок ключей ломает
    префиксный кэш и делает повторные запросы дороже.
    """
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
