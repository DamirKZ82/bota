"""Сериализация ходов диалога в JSON и обратно.

Нужна, чтобы историю можно было сохранить в БД и продолжить диалог после
перезапуска оркестратора или на другом воркере. Формат — нейтральный (типы из
`llm/base.py`), а не формат конкретного вендора: смена провайдера модели не
должна делать сохранённые диалоги нечитаемыми.
"""

from __future__ import annotations

from typing import Any

from orchestrator.llm.base import (
    AssistantBlock,
    AssistantTurn,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    Turn,
    UserTurn,
)


def turn_to_json(turn: Turn) -> dict[str, Any]:
    if isinstance(turn, UserTurn):
        return {
            "role": "user",
            "text": turn.text,
            "tool_results": [
                {
                    "tool_use_id": r.tool_use_id,
                    "content": r.content,
                    "is_error": r.is_error,
                }
                for r in turn.tool_results
            ],
        }
    return {
        "role": "assistant",
        "stop_reason": turn.stop_reason,
        "usage": turn.usage,
        "blocks": [_block_to_json(b) for b in turn.blocks],
    }


def turn_from_json(payload: dict[str, Any]) -> Turn:
    if payload.get("role") == "user":
        return UserTurn(
            text=payload.get("text"),
            tool_results=[
                ToolResultBlock(
                    tool_use_id=r["tool_use_id"],
                    content=r["content"],
                    is_error=r.get("is_error", False),
                )
                for r in payload.get("tool_results", [])
            ],
        )
    return AssistantTurn(
        blocks=[_block_from_json(b) for b in payload.get("blocks", [])],
        stop_reason=payload.get("stop_reason", "end_turn"),
        usage=payload.get("usage", {}),
    )


def _block_to_json(block: AssistantBlock) -> dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ToolUseBlock):
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    # Блок рассуждения возвращается модели без изменений, поэтому и хранится как есть.
    return {"type": "thinking", "raw": block.raw}


def _block_from_json(payload: dict[str, Any]) -> AssistantBlock:
    kind = payload.get("type")
    if kind == "text":
        return TextBlock(text=payload["text"])
    if kind == "tool_use":
        return ToolUseBlock(id=payload["id"], name=payload["name"], input=payload["input"])
    return ThinkingBlock(raw=payload["raw"])
