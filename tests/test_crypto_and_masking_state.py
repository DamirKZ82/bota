"""Шифрование значений и сохраняемое состояние маскирования."""

from __future__ import annotations

import base64
import os

import pytest

from orchestrator.db.crypto import Cipher
from orchestrator.errors import ErrorException
from orchestrator.masking.masker import MaskingSession


def _cipher() -> Cipher:
    return Cipher.from_base64(base64.b64encode(os.urandom(32)).decode())


def test_шифрование_обратимо() -> None:
    cipher = _cipher()
    assert cipher.decrypt(cipher.encrypt("ТОО «Снабженец»")) == "ТОО «Снабженец»"


def test_один_текст_шифруется_каждый_раз_по_разному() -> None:
    """Иначе по совпадающим шифротекстам видно, что контрагент тот же самый."""
    cipher = _cipher()
    assert cipher.encrypt("ТОО «Альфа»") != cipher.encrypt("ТОО «Альфа»")


def test_чужой_ключ_не_расшифровывает() -> None:
    payload = _cipher().encrypt("секрет")
    with pytest.raises(ErrorException):
        _cipher().decrypt(payload)


def test_короткий_ключ_отвергается_при_создании() -> None:
    with pytest.raises(ErrorException):
        Cipher(b"too-short")


def test_снимок_и_восстановление_сохраняют_псевдонимы() -> None:
    """После перезапуска оркестратора диалог должен продолжаться теми же именами."""
    first = MaskingSession()
    first.mask({"name": "ТОО «Снабженец»", "item_name": "Картридж HP"})
    snapshot = first.snapshot()

    second = MaskingSession()
    second.restore(snapshot)

    assert second.unmask("КОНТРАГЕНТ_1") == "ТОО «Снабженец»"
    # Новый контрагент не должен занять уже выданный номер.
    masked = second.mask({"name": "ТОО «Бета»"})
    assert masked["name"] == "КОНТРАГЕНТ_2"


def test_аргументы_инструмента_демаскируются_перед_вызовом_1с() -> None:
    """Модель оперирует псевдонимами, 1С — реальными значениями."""
    session = MaskingSession()
    session.mask({"bin": "123456789012", "name": "ТОО «Снабженец»"})

    arguments = {"bin": "БИН_1", "on_date": "2026-05-14", "nested": ["КОНТРАГЕНТ_1"]}
    assert session.unmask_arguments(arguments) == {
        "bin": "123456789012",
        "on_date": "2026-05-14",
        "nested": ["ТОО «Снабженец»"],
    }


def test_демаскирование_аргументов_не_трогает_числа_и_флаги() -> None:
    session = MaskingSession()
    session.mask({"name": "ТОО «Альфа»"})
    arguments = {"page": 2, "page_size": 50, "blocked": False, "amount": None}
    assert session.unmask_arguments(arguments) == arguments
