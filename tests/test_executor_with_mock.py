"""Сквозная проверка контрактов Приложения А на мок-данных.

Главная ценность теста: он ловит рассинхрон между тем, что отдаёт «база», и тем,
что описано в Приложении. Когда мок заменят реальным расширением 1С, этот же
набор станет приёмочным тестом HTTP-сервиса.
"""

from __future__ import annotations

import json

import pytest

from orchestrator.tools.enums import ErrorCode
from orchestrator.tools.envelope import CallContext, ToolRequest, ToolResponse
from orchestrator.tools.executor import ToolExecutor
from orchestrator.tools.registry import TOOLS
from orchestrator.transport.base import OneCTransport, TransportTimeout
from orchestrator.transport.mock import MockTransport

CONTEXT = CallContext(user_id="user-1", session_id="session-1", masking=False)

#: Параметры, с которыми каждый инструмент вызывается в тесте.
ARGS: dict[str, dict[str, object]] = {
    "get_context": {},
    "reconcile_period": {
        "organization": "org-0001",
        "from": "2026-04-01",
        "to": "2026-06-30",
    },
    "list_discrepancies": {"calc_id": "calc-2026q2-0001"},
    "get_discrepancy": {"id": "d1a2b3c4e5f6a7b8"},
    "get_document": {"uuid": "doc-receipt-145"},
    "find_esf_candidates": {"receipt_uuid": "doc-receipt-145"},
    "find_receipt_candidates": {"esf_uuid": "doc-esf-9042"},
    "get_counterparty": {"bin": "123456789012"},
    "get_vat_turnover": {
        "organization": "org-0001",
        "from": "2026-04-01",
        "to": "2026-06-30",
    },
    "get_journal": {"from": "2026-04-01", "to": "2026-06-30"},
    "plan_set_link": {"receipt_uuid": "doc-receipt-145", "esf_uuid": "doc-esf-8891"},
    "plan_adjust_lines": {"discrepancy_id": "d1a2b3c4e5f6a7b8"},
    "plan_set_vat_mode": {"receipt_uuid": "doc-receipt-208", "vat_included": False},
    "plan_create_correction": {
        "discrepancy_id": "b7c8d9e0f1a2b3c4",
        "reason": "закрытый период",
    },
    "plan_create_receipt_from_esf": {"esf_uuid": "doc-esf-9042"},
    "plan_create_receipts_bulk": {"esf_uuids": ["doc-esf-9042"]},
    "mark_reviewed": {"discrepancy_id": "d1a2b3c4e5f6a7b8", "comment": "принято"},
    "get_settings": {},
    "open_object": {"uuid": "doc-esf-8891"},
}


@pytest.fixture
def executor() -> ToolExecutor:
    return ToolExecutor(MockTransport())


def test_у_каждого_инструмента_реестра_есть_проверка() -> None:
    """Новый инструмент нельзя добавить, не проверив его на моке."""
    assert {spec.name for spec in TOOLS} == set(ARGS)


@pytest.mark.parametrize("tool_name", sorted(ARGS))
async def test_каждый_инструмент_проходит_валидацию_контракта(
    executor: ToolExecutor, tool_name: str
) -> None:
    outcome = await executor.execute(
        tenant_id="demo", tool_name=tool_name, arguments=ARGS[tool_name], context=CONTEXT
    )
    assert outcome.ok, outcome.payload


async def test_неизвестный_инструмент_возвращает_ошибку_а_не_падает(
    executor: ToolExecutor,
) -> None:
    outcome = await executor.execute(
        tenant_id="demo", tool_name="drop_database", arguments={}, context=CONTEXT
    )
    assert not outcome.ok
    assert outcome.error_code is ErrorCode.NOT_FOUND


async def test_неверные_параметры_возвращаются_модели_для_исправления(
    executor: ToolExecutor,
) -> None:
    outcome = await executor.execute(
        tenant_id="demo",
        tool_name="get_counterparty",
        arguments={"bin": "123"},  # БИН должен быть ровно 12 цифр
        context=CONTEXT,
    )
    assert not outcome.ok
    assert outcome.error_code is ErrorCode.BAD_ARGS


async def test_ошибка_расширения_доносится_с_кодом(executor: ToolExecutor) -> None:
    """Код нужен модели: PERIOD_CLOSED и BAD_ARGS требуют разных действий."""

    class ClosedPeriodTransport(OneCTransport):
        async def call(self, tenant_id: str, request: ToolRequest) -> ToolResponse:
            return ToolResponse.failure(
                request.tool,
                ErrorCode.PERIOD_CLOSED,
                "Период закрыт датой запрета 30.06.2026",
                {"forbid_date": "2026-06-30"},
            )

    outcome = await ToolExecutor(ClosedPeriodTransport()).execute(
        tenant_id="demo",
        tool_name="plan_adjust_lines",
        arguments={"discrepancy_id": "d1a2b3c4e5f6a7b8"},
        context=CONTEXT,
    )
    assert not outcome.ok
    assert outcome.error_code is ErrorCode.PERIOD_CLOSED
    body = json.loads(outcome.payload)
    assert body["error"]["details"]["forbid_date"] == "2026-06-30"


async def test_таймаут_канала_превращается_в_код_timeout() -> None:
    class DeadTransport(OneCTransport):
        async def call(self, tenant_id: str, request: ToolRequest) -> ToolResponse:
            raise TransportTimeout(request.tool, 120)

    outcome = await ToolExecutor(DeadTransport()).execute(
        tenant_id="demo", tool_name="get_context", arguments={}, context=CONTEXT
    )
    assert outcome.error_code is ErrorCode.TIMEOUT


async def test_копеечное_расхождение_r1_доезжает_до_модели(executor: ToolExecutor) -> None:
    """Ключевой кейс продукта: 112,00 против 112,01 и паттерн R1 при ставке 16 %."""
    outcome = await executor.execute(
        tenant_id="demo",
        tool_name="get_discrepancy",
        arguments={"id": "d1a2b3c4e5f6a7b8"},
        context=CONTEXT,
    )
    card = json.loads(outcome.payload)
    assert card["code"] == "D14"
    assert card["diagnosis"]["pattern"] == "R1"

    vat = next(f for f in card["header_diff"] if f["field"] == "vat")
    assert (vat["receipt"], vat["esf"], vat["diff"]) == ("112.00", "112.01", "-0.01")
    assert vat["within_tolerance"] is True


async def test_черновик_по_эсф_блокируется_пока_есть_неуверенные_строки(
    executor: ToolExecutor,
) -> None:
    """A.9.5: can_apply = false, пока строка требует выбора номенклатуры."""
    blocked = json.loads(
        (
            await executor.execute(
                tenant_id="demo",
                tool_name="plan_create_receipt_from_esf",
                arguments={"esf_uuid": "doc-esf-9042"},
                context=CONTEXT,
            )
        ).payload
    )
    assert blocked["can_apply"] is False
    assert blocked["draft"]["attention_count"] == 1
    assert blocked["block_reason"]

    resolved = json.loads(
        (
            await executor.execute(
                tenant_id="demo",
                tool_name="plan_create_receipt_from_esf",
                arguments={
                    "esf_uuid": "doc-esf-9042",
                    "overrides": [{"esf_line": 2, "item": "item-2002"}],
                },
                context=CONTEXT,
            )
        ).payload
    )
    assert resolved["can_apply"] is True
    assert resolved["draft"]["attention_count"] == 0


async def test_план_попадает_в_список_для_журнала(executor: ToolExecutor) -> None:
    outcome = await executor.execute(
        tenant_id="demo",
        tool_name="plan_set_link",
        arguments=ARGS["plan_set_link"],
        context=CONTEXT,
    )
    assert outcome.plans == (("plan-link-0001", "set_link"),)


async def test_контекст_вызова_доезжает_до_расширения() -> None:
    """Расширение маскирует ответ по session_id — контекст обязан дойти в целости."""
    seen: list[ToolRequest] = []

    class SpyTransport(OneCTransport):
        async def call(self, tenant_id: str, request: ToolRequest) -> ToolResponse:
            seen.append(request)
            return await MockTransport().call(tenant_id, request)

    await ToolExecutor(SpyTransport()).execute(
        tenant_id="demo",
        tool_name="get_context",
        arguments={},
        context=CallContext(user_id="u-7", session_id="s-42", masking=True),
    )
    assert seen[0].context.session_id == "s-42"
    assert seen[0].context.masking is True


async def test_даты_уходят_в_1с_под_именами_из_приложения() -> None:
    """`from` и `to` внутри Python зовутся иначе — наружу должны идти как в A.2."""
    seen: list[ToolRequest] = []

    class SpyTransport(OneCTransport):
        async def call(self, tenant_id: str, request: ToolRequest) -> ToolResponse:
            seen.append(request)
            return await MockTransport().call(tenant_id, request)

    await ToolExecutor(SpyTransport()).execute(
        tenant_id="demo",
        tool_name="reconcile_period",
        arguments=ARGS["reconcile_period"],
        context=CONTEXT,
    )
    assert seen[0].args["from"] == "2026-04-01"
    assert seen[0].args["to"] == "2026-06-30"
