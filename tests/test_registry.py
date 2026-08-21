"""Проверки реестра инструментов: имена, схемы, отсутствие фазы 2."""

from __future__ import annotations

import re

import pytest

from orchestrator.tools.registry import BY_NAME, BY_ONEC_METHOD, TOOLS, anthropic_tools

ANTHROPIC_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def test_имена_инструментов_подходят_для_claude_api() -> None:
    for spec in TOOLS:
        assert ANTHROPIC_NAME_RE.match(spec.name), f"{spec.name} не пройдёт валидацию tool name"


def test_имена_уникальны_с_обеих_сторон() -> None:
    assert len(BY_NAME) == len(TOOLS)
    assert len(BY_ONEC_METHOD) == len(TOOLS)


def test_у_каждого_инструмента_есть_описание() -> None:
    for spec in TOOLS:
        assert len(spec.description) > 40, f"{spec.name}: описание слишком короткое для модели"


def test_схема_генерируется_и_является_объектом() -> None:
    for tool in anthropic_tools():
        assert tool["input_schema"]["type"] == "object"
        assert "properties" in tool["input_schema"]


def test_агенту_недоступно_применение_изменений() -> None:
    """Фазы 2 в реестре быть не должно — применяет только пользователь из 1С."""
    forbidden = ("apply", "применить", "provesti", "провести", "delete")
    for spec in TOOLS:
        low = f"{spec.name} {spec.onec_method}".lower()
        assert not any(word in low for word in forbidden), spec.name


def test_инструменты_записи_называются_с_префиксом_plan() -> None:
    for spec in TOOLS:
        if spec.writes and spec.name != "mark_reviewed":
            assert spec.name.startswith("plan_"), spec.name


@pytest.mark.parametrize("name", ["get_context", "reconcile_period", "get_discrepancy"])
def test_ключевые_инструменты_на_месте(name: str) -> None:
    assert name in BY_NAME
