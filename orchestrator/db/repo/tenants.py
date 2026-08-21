"""Базы 1С: поиск по токену и регистрация."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from orchestrator.db.crypto import Cipher
from orchestrator.db.pool import execute_db, fetch_one

Transport = Literal["direct", "polling", "mock"]


@dataclass(frozen=True, slots=True)
class Tenant:
    id: str
    name: str
    transport: Transport
    base_url: str | None
    masking_enabled: bool
    signing_key: str | None


def token_digest(token: str) -> bytes:
    """Токен в базе не хранится — только его SHA-256."""
    return hashlib.sha256(token.encode("utf-8")).digest()


async def get_by_token(token: str, cipher: Cipher | None = None) -> Tenant | None:
    row = await fetch_one(
        """
        SELECT id, name, transport, base_url, masking_enabled, signing_key_enc
          FROM tenants
         WHERE token_sha256 = $1
           AND is_active
        """,
        token_digest(token),
    )
    if row is None:
        return None

    signing_key: str | None = None
    if row["signing_key_enc"] is not None and cipher is not None:
        signing_key = cipher.decrypt(bytes(row["signing_key_enc"]))

    return Tenant(
        id=row["id"],
        name=row["name"],
        transport=row["transport"],
        base_url=row["base_url"],
        masking_enabled=row["masking_enabled"],
        signing_key=signing_key,
    )


async def register(
    *,
    tenant_id: str,
    name: str,
    token: str,
    transport: Transport,
    base_url: str | None = None,
    masking_enabled: bool = True,
    signing_key: str | None = None,
    cipher: Cipher | None = None,
) -> None:
    """Зарегистрировать базу или обновить её параметры."""
    signing_key_enc: bytes | None = None
    if signing_key is not None:
        if cipher is None:
            raise ValueError("Для сохранения ключа подписи нужен Cipher")
        signing_key_enc = cipher.encrypt(signing_key)

    await execute_db(
        """
        INSERT INTO tenants (id, name, token_sha256, signing_key_enc,
                             transport, base_url, masking_enabled)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (id) DO UPDATE
           SET name            = EXCLUDED.name,
               token_sha256    = EXCLUDED.token_sha256,
               signing_key_enc = EXCLUDED.signing_key_enc,
               transport       = EXCLUDED.transport,
               base_url        = EXCLUDED.base_url,
               masking_enabled = EXCLUDED.masking_enabled,
               updated_at      = now()
        """,
        tenant_id,
        name,
        token_digest(token),
        signing_key_enc,
        transport,
        base_url,
        masking_enabled,
    )


async def deactivate(tenant_id: str) -> None:
    """Отключить базу, не удаляя журнал по ней."""
    await execute_db(
        "UPDATE tenants SET is_active = FALSE, updated_at = now() WHERE id = $1",
        tenant_id,
    )
