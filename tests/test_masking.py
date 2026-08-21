"""Маскирование: псевдонимы стабильны, обратная подстановка возвращает исходное."""

from __future__ import annotations

from orchestrator.masking.masker import MaskingSession


def test_один_контрагент_получает_один_псевдоним() -> None:
    session = MaskingSession()
    first = session.mask({"counterparty": {"name": "ТОО «Снабженец»", "bin": "123456789012"}})
    second = session.mask({"counterparty": {"name": "ТОО «Снабженец»", "bin": "123456789012"}})
    assert first == second
    assert first["counterparty"]["name"] == "КОНТРАГЕНТ_1"
    assert first["counterparty"]["bin"] == "БИН_1"


def test_разные_контрагенты_получают_разные_псевдонимы() -> None:
    session = MaskingSession()
    masked = session.mask(
        {
            "items": [
                {"counterparty_name": "ТОО «Альфа»"},
                {"counterparty_name": "ТОО «Бета»"},
            ]
        }
    )
    names = [i["counterparty_name"] for i in masked["items"]]
    assert names == ["КОНТРАГЕНТ_1", "КОНТРАГЕНТ_2"]


def test_суммы_и_даты_не_маскируются() -> None:
    session = MaskingSession()
    masked = session.mask({"amount_vat": "84.01", "date": "2026-05-14", "uuid": "doc-1"})
    assert masked == {"amount_vat": "84.01", "date": "2026-05-14", "uuid": "doc-1"}


def test_наименование_заменяется_и_в_свободном_тексте() -> None:
    """Иначе контрагент утечёт через поле «вероятная причина»."""
    session = MaskingSession()
    masked = session.mask(
        {
            "counterparty": {"name": "ТОО «Снабженец»"},
            "probable_cause": "ЭСФ от ТОО «Снабженец» выписана позже поступления",
        }
    )
    assert "Снабженец" not in masked["probable_cause"]
    assert "КОНТРАГЕНТ_1" in masked["probable_cause"]


def test_обратная_подстановка_возвращает_реальные_наименования() -> None:
    session = MaskingSession()
    session.mask({"name": "ТОО «Снабженец»", "item_name": "Картридж HP CF217A"})
    answer = "По КОНТРАГЕНТ_1 расходится строка «НОМЕНКЛАТУРА_1» на 0,01 ₸"
    assert session.unmask(answer) == (
        "По ТОО «Снабженец» расходится строка «Картридж HP CF217A» на 0,01 ₸"
    )


def test_выключенное_маскирование_ничего_не_трогает() -> None:
    session = MaskingSession(enabled=False)
    payload = {"name": "ТОО «Снабженец»"}
    assert session.mask(payload) == payload
    assert session.unmask("КОНТРАГЕНТ_1") == "КОНТРАГЕНТ_1"
