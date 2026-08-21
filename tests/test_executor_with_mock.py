"""Сквозная проверка контрактов на мок-данных.

Главная ценность теста: он ловит рассинхрон между тем, что отдаёт «база», и тем,
что описано в Приложении А. Когда мок заменят на реальное расширение 1С, этот же
набор станет приёмочным тестом HTTP-сервиса.
"""

from __future__ import annotations

import json

import pytest

from orchestrator.masking.masker import MaskingSession
from orchestrator.tools.executor import ToolExecutor
from orchestrator.transport.mock import MockTransport

# Параметры, с которыми каждый инструмент вызывается в тесте.
ARGS: dict[str, dict[str, object]] = {
    "get_context": {},
    "reconcile_period": {
        "organization_uuid": "org-0001",
        "period": {"date_from": "2026-04-01", "date_to": "2026-06-30"},
    },
    "list_discrepancies": {
        "organization_uuid": "org-0001",
        "period": {"date_from": "2026-04-01", "date_to": "2026-06-30"},
    },
    "get_discrepancy": {"discrepancy_id": "disc-0001"},
    "get_document": {"uuid": "doc-receipt-145", "kind": "ПоступлениеТМЗиУслуг"},
    "find_esf_candidates": {"receipt_uuid": "doc-receipt-145"},
    "find_receipt_candidates": {"esf_uuid": "doc-esf-9042"},
    "get_counterparty": {"bin": "123456789012"},
    "get_vat_turnovers": {
        "organization_uuid": "org-0001",
        "period": {"date_from": "2026-04-01", "date_to": "2026-06-30"},
    },
    "get_change_history": {"uuid": "doc-receipt-145", "kind": "ПоступлениеТМЗиУслуг"},
    "plan_set_link": {"receipt_uuid": "doc-receipt-145", "esf_uuid": "doc-esf-8891"},
    "plan_adjust_lines": {"receipt_uuid": "doc-receipt-145", "line_numbers": [3]},
    "plan_change_vat_flag": {
        "receipt_uuid": "doc-receipt-145",
        "vat_included_in_price": False,
    },
    "plan_create_adjustment": {"receipt_uuid": "doc-receipt-145", "reason": "тест"},
    "plan_create_receipt_from_esf": {"esf_uuid": "doc-esf-9042"},
    "plan_create_receipts_bulk": {"esf_uuids": ["doc-esf-9042"]},
    "mark_reviewed": {"discrepancy_id": "disc-0001", "comment": "принято"},
    "get_settings": {},
    "open_object": {"uuid": "doc-esf-8891", "kind": "ЭСФПолученный"},
}


@pytest.fixture
def executor() -> ToolExecutor:
    return ToolExecutor(MockTransport())


@pytest.mark.parametrize("tool_name", sorted(ARGS))
async def test_каждый_инструмент_проходит_валидацию_контракта(
    executor: ToolExecutor, tool_name: str
) -> None:
    outcome = await executor.execute(
        tenant_id="demo",
        tool_name=tool_name,
        arguments=ARGS[tool_name],
        masking=MaskingSession(enabled=False),
    )
    assert outcome.ok, outcome.payload


async def test_неизвестный_инструмент_возвращает_ошибку_а_не_падает(
    executor: ToolExecutor,
) -> None:
    outcome = await executor.execute(
        tenant_id="demo",
        tool_name="drop_database",
        arguments={},
        masking=MaskingSession(),
    )
    assert not outcome.ok
    assert "не существует" in outcome.payload


async def test_неверные_параметры_возвращаются_модели_для_исправления(
    executor: ToolExecutor,
) -> None:
    outcome = await executor.execute(
        tenant_id="demo",
        tool_name="get_counterparty",
        arguments={"bin": "123"},  # БИН должен быть ровно 12 знаков
        masking=MaskingSession(),
    )
    assert not outcome.ok
    assert "Неверные параметры" in outcome.payload


async def test_копеечное_расхождение_r1_доезжает_до_модели(executor: ToolExecutor) -> None:
    """Ключевой кейс продукта: 84,00 против 84,01 и код причины R1."""
    outcome = await executor.execute(
        tenant_id="demo",
        tool_name="get_discrepancy",
        arguments={"discrepancy_id": "disc-0001"},
        masking=MaskingSession(enabled=False),
    )
    card = json.loads(outcome.payload)["card"]
    assert card["rounding_code"] == "R1"
    assert card["code"] == "D14"
    vat = next(f for f in card["field_comparisons"] if f["field"] == "Сумма НДС")
    assert vat["receipt_value"] == "84.00"
    assert vat["esf_value"] == "84.01"
    assert vat["within_tolerance"] is True
