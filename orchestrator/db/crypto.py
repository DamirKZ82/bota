"""Шифрование значений, которые обязаны лежать в БД, но не должны читаться из дампа.

Таких значений два: словарь псевдонимов маскирования и ключ подписи запросов к
базе 1С. Оба шифруются AES-GCM ключом приложения (`ENCRYPTION_KEY` в .env).

Шифрование делается здесь, в Python, а не средствами Postgres намеренно: вызов
`pgp_sym_encrypt` тащит ключ в текст SQL-запроса, а тексты упавших запросов
сохраняются в `errors_back`. Ключ в журнале ошибок обесценил бы всю затею.
"""

from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from orchestrator.errors import ErrorException

_NONCE_BYTES = 12


class Cipher:
    """AES-GCM поверх ключа приложения."""

    def __init__(self, key: bytes) -> None:
        if len(key) not in (16, 24, 32):
            raise ErrorException(
                ValueError(
                    "ENCRYPTION_KEY должен быть 16, 24 или 32 байта "
                    "(сгенерировать: openssl rand -base64 32)"
                )
            )
        self._aes = AESGCM(key)

    @classmethod
    def from_base64(cls, encoded: str) -> Cipher:
        try:
            return cls(base64.b64decode(encoded, validate=True))
        except (ValueError, TypeError) as err:
            raise ErrorException(
                ValueError("ENCRYPTION_KEY не является корректным base64")
            ) from err

    def encrypt(self, plaintext: str) -> bytes:
        nonce = os.urandom(_NONCE_BYTES)
        return nonce + self._aes.encrypt(nonce, plaintext.encode("utf-8"), None)

    def decrypt(self, payload: bytes) -> str:
        if len(payload) <= _NONCE_BYTES:
            raise ErrorException(ValueError("Зашифрованное значение повреждено"))
        nonce, ciphertext = payload[:_NONCE_BYTES], payload[_NONCE_BYTES:]
        try:
            return self._aes.decrypt(nonce, ciphertext, None).decode("utf-8")
        except InvalidTag as err:
            # Чаще всего это значит, что ENCRYPTION_KEY сменили, а данные остались.
            raise ErrorException(
                ValueError("Не удалось расшифровать значение: ключ не подходит")
            ) from err
