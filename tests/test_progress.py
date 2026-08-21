"""Индикация шагов: агент сообщает, чем занят, форма это показывает (ТЗ п.8).

Проверяется без обращения к модели и без Postgres: провайдер подставной,
хранилище прогресса — в памяти.
"""

from __future__ import annotations

import asyncio
from typing import Literal

import httpx
import pytest

from orchestrator.agent.loop import AgentLoop
from orchestrator.llm.base import (
    AssistantTurn,
    LLMProvider,
    TextBlock,
    ToolDefinition,
    ToolUseBlock,
    Turn,
)
from orchestrator.progress import ANSWERING, MemoryProgressStore, label_for
from orchestrator.tools.envelope import CallContext
from orchestrator.tools.executor import ToolExecutor
from orchestrator.transport.mock import MockTransport

CONTEXT = CallContext(user_id="u-1", session_id="s-1", masking=False)


class ScriptedProvider(LLMProvider):
    def __init__(self, script: list[AssistantTurn]) -> None:
        self._script = script
        self.calls = 0

    async def complete(
        self,
        *,
        system: str,
        history: list[Turn],
        tools: list[ToolDefinition],
        effort: Literal["low", "medium", "high", "xhigh", "max"] = "high",
    ) -> AssistantTurn:
        turn = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        return turn


def _two_step_provider() -> ScriptedProvider:
    return ScriptedProvider(
        [
            AssistantTurn(
                blocks=[ToolUseBlock(id="tu_1", name="get_context", input={})],
                stop_reason="tool_use",
            ),
            AssistantTurn(
                blocks=[
                    ToolUseBlock(
                        id="tu_2",
                        name="reconcile_period",
                        input={
                            "organization": "org-0001",
                            "from": "2026-04-01",
                            "to": "2026-06-30",
                        },
                    )
                ],
                stop_reason="tool_use",
            ),
            AssistantTurn(
                blocks=[TextBlock(text="Расхождений на 3,47 ₸.")],
                stop_reason="end_turn",
            ),
        ]
    )


def test_метки_шагов_на_языке_бухгалтера() -> None:
    assert label_for("reconcile_period") == "Сверяю поступления и ЭСФ за период"
    assert label_for("plan_create_receipt_from_esf") == "Готовлю черновик поступления по ЭСФ"
    # Незнакомый инструмент не должен ломать индикацию.
    assert label_for("совершенно_новый") == "Выполняю совершенно_новый"


async def test_цикл_сообщает_шаг_до_вызова_инструмента() -> None:
    """Пользователь должен видеть, чем агент занят сейчас, а не чем занимался."""
    steps: list[tuple[int, str, str | None]] = []

    async def report(step_no: int, label: str, tool: str | None) -> None:
        steps.append((step_no, label, tool))

    agent = AgentLoop(
        provider=_two_step_provider(),
        executor=ToolExecutor(MockTransport()),
    )
    await agent.run(
        tenant_id="demo",
        user_message="Сверь период",
        context=CONTEXT,
        on_step=report,
    )

    labels = [label for _, label, _ in steps]
    assert labels[0] == "Читаю контекст базы"
    assert "Сверяю поступления и ЭСФ за период" in labels
    # После круга вызовов агент думает над ответом — это тоже видно пользователю.
    assert labels[-1] == ANSWERING
    assert [tool for _, _, tool in steps][:2] == ["get_context", None]


async def test_хранилище_прогресса_отдаёт_текущий_шаг_и_ответ() -> None:
    store = MemoryProgressStore()
    await store.start(
        tenant_id="demo", request_id="r-1", dialog_id="d-1", user_key="buh"
    )

    state = await store.get(tenant_id="demo", request_id="r-1")
    assert state is not None
    assert state.status == "running"
    assert state.step_label == "Обдумываю ответ"

    await store.step(
        tenant_id="demo",
        request_id="r-1",
        step_no=1,
        label="Сверяю поступления и ЭСФ за период",
        tool="reconcile_period",
    )
    state = await store.get(tenant_id="demo", request_id="r-1")
    assert state is not None and state.step_label == "Сверяю поступления и ЭСФ за период"

    await store.finish(
        tenant_id="demo", request_id="r-1", answer="Готово", calls=["reconcile_period"]
    )
    state = await store.get(tenant_id="demo", request_id="r-1")
    assert state is not None
    assert (state.status, state.answer, state.calls) == (
        "done",
        "Готово",
        ["reconcile_period"],
    )


async def test_ошибка_агента_не_оставляет_форму_ждать_вечно() -> None:
    store = MemoryProgressStore()
    await store.start(
        tenant_id="demo", request_id="r-2", dialog_id="d-1", user_key="buh"
    )
    await store.fail(tenant_id="demo", request_id="r-2", message="база недоступна")

    state = await store.get(tenant_id="demo", request_id="r-2")
    assert state is not None
    assert state.status == "failed"
    assert state.error_message == "база недоступна"


@pytest.fixture
async def client() -> httpx.AsyncClient:
    """Приложение в режиме разработки: без Postgres и без обращения к модели."""
    import os

    os.environ["TRANSPORT"] = "mock"
    from orchestrator.config import get_settings

    get_settings.cache_clear()
    from orchestrator.main import app

    async with app.router.lifespan_context(app):
        app.state.agent = AgentLoop(
            provider=_two_step_provider(),
            executor=ToolExecutor(MockTransport()),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as http_client:
            yield http_client

    get_settings.cache_clear()
    os.environ.pop("TRANSPORT", None)


async def test_фоновый_запрос_отвечает_сразу_и_доводит_дело_до_конца(
    client: httpx.AsyncClient,
) -> None:
    """Форма 1С не должна висеть на запросе, который идёт минуту."""
    headers = {"Authorization": "Bearer demo-token"}

    accepted = await client.post(
        "/v1/chat/ask/background",
        json={"message": "Сверь 2 квартал", "user_id": "buh"},
        headers=headers,
    )
    assert accepted.status_code == 202
    request_id = accepted.json()["request_id"]

    for _ in range(50):
        state = (
            await client.get(f"/v1/chat/progress/{request_id}", headers=headers)
        ).json()
        if state["status"] != "running":
            break
        await asyncio.sleep(0.05)

    assert state["status"] == "done"
    assert state["answer"] == "Расхождений на 3,47 ₸."
    assert state["calls"] == ["get_context", "reconcile_period"]


async def test_прогресс_чужого_запроса_не_отдаётся(client: httpx.AsyncClient) -> None:
    unknown = await client.get(
        "/v1/chat/progress/00000000-0000-0000-0000-000000000000",
        headers={"Authorization": "Bearer demo-token"},
    )
    assert unknown.status_code == 404
