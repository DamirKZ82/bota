"""Шифрование секретов, которые обязаны лежать в БД (ключи подписи баз)."""

from __future__ import annotations

import base64
import os

import pytest

from orchestrator.db.crypto import Cipher
from orchestrator.errors import ErrorException


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
