"""Арифметическая состоятельность мок-данных.

Тест существует потому, что в исходном Приложении А пример карточки D06 не
сходился: сумма НДС не соответствовала ставке 16 %. Такую опечатку глазами не
видно, а разработчик расширения реализует ровно то, что написано в примере.
Здесь мок-данные проверяются теми же правилами, которые движок сверки применяет
к настоящим документам.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import pytest

from orchestrator.transport.mock import (
    ESF_LINES,
    ESF_R2_LINES,
    RECEIPT_LINES,
    RECEIPT_R2_LINES,
    VAT_RATE,
)

#: Допуск на документ из ТЗ п.4.4: 0,01 ₸ на строку.
TOLERANCE_PER_LINE = Decimal("0.01")

#: набор строк, итоги (без НДС, НДС, с НДС) и признак «цена включает НДС».
#: Последний важен: при включённом флаге количество × цена даёт итог с НДС,
#: а не базу — на этом и построен паттерн R2.
DOCUMENTS: dict[str, tuple[list[dict], str, str, str, bool]] = {
    "поступление R1": (RECEIPT_LINES, "700.06", "112.00", "812.06", False),
    "ЭСФ R1": (ESF_LINES, "700.06", "112.01", "812.07", False),
    "поступление R2": (RECEIPT_R2_LINES, "103448.28", "16551.72", "120000.00", True),
    "ЭСФ R2": (ESF_R2_LINES, "120000.00", "19200.00", "139200.00", False),
}


def _d(value: str) -> Decimal:
    return Decimal(value)


@pytest.mark.parametrize("name", sorted(DOCUMENTS))
def test_в_строке_сумма_без_ндс_плюс_ндс_равна_итогу(name: str) -> None:
    lines = DOCUMENTS[name][0]
    for line in lines:
        assert _d(line["net"]) + _d(line["vat"]) == _d(line["total"]), (
            f"{name}, строка {line['n']}: {line['net']} + {line['vat']} ≠ {line['total']}"
        )


@pytest.mark.parametrize("name", sorted(DOCUMENTS))
def test_количество_на_цену_сходится_с_суммой_строки(name: str) -> None:
    """С каким итогом сходится произведение — зависит от флага «включает НДС»."""
    lines, _, _, _, vat_included = DOCUMENTS[name]
    field = "total" if vat_included else "net"
    for line in lines:
        expected = (_d(line["qty"]) * _d(line["price"])).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        assert abs(expected - _d(line[field])) <= TOLERANCE_PER_LINE, (
            f"{name}, строка {line['n']}: "
            f"{line['qty']} × {line['price']} ≠ {line[field]} ({field})"
        )


@pytest.mark.parametrize("name", sorted(DOCUMENTS))
def test_итоги_документа_равны_сумме_строк(name: str) -> None:
    lines, net, vat, total, _ = DOCUMENTS[name]
    assert sum(_d(line["net"]) for line in lines) == _d(net)
    assert sum(_d(line["vat"]) for line in lines) == _d(vat)
    assert sum(_d(line["total"]) for line in lines) == _d(total)


@pytest.mark.parametrize("name", sorted(DOCUMENTS))
def test_ндс_соответствует_ставке_в_пределах_допуска(name: str) -> None:
    """Итоговый НДС обязан сходиться со ставкой с точностью до округлений.

    Именно это правило нарушал пример из Приложения А: 103 440,00 × 16 % — это
    16 550,40 ₸, а в примере стояло 16 560,00 ₸.
    """
    lines, net, vat, _, _ = DOCUMENTS[name]
    rate = _d(VAT_RATE) / 100
    expected = (_d(net) * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    allowed = TOLERANCE_PER_LINE * len(lines)
    assert abs(expected - _d(vat)) <= allowed, (
        f"{name}: {net} × {VAT_RATE} % = {expected}, в данных {vat}"
    )


def test_кейс_r1_воспроизводит_расхождение_ровно_в_один_тиын() -> None:
    """Смысл кейса: построчный НДС 112,00 ₸ против 112,01 ₸ от итога."""
    line_by_line = sum(_d(line["vat"]) for line in RECEIPT_LINES)
    net_total = sum(_d(line["net"]) for line in RECEIPT_LINES)
    from_total = (net_total * _d(VAT_RATE) / 100).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    assert line_by_line == Decimal("112.00")
    assert from_total == Decimal("112.01")
    assert from_total - line_by_line == Decimal("0.01")


def test_кейс_r2_воспроизводит_разную_трактовку_одной_цены() -> None:
    """Цена одна и та же, расходится только способ учёта НДС."""
    receipt, esf = RECEIPT_R2_LINES[0], ESF_R2_LINES[0]
    assert receipt["price"] == esf["price"] == "1200.00"

    # В поступлении цена включает НДС: 120 000 / 1,16 = 103 448,28.
    gross = _d(receipt["total"])
    rate = _d(VAT_RATE) / 100
    assert (gross / (1 + rate)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    ) == _d(receipt["net"])

    # В ЭСФ та же цена взята без НДС: 120 000 × 16 % = 19 200,00.
    assert (_d(esf["net"]) * rate).quantize(Decimal("0.01")) == _d(esf["vat"])

    # Разница по НДС — то, что увидит бухгалтер в карточке D06.
    assert _d(esf["vat"]) - _d(receipt["vat"]) == Decimal("2648.28")
