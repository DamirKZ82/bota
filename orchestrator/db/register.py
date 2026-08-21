"""Регистрация базы 1С в оркестраторе.

    python -m orchestrator.db.register --id demo --name "Демо-база" --transport polling

Токен генерируется здесь и печатается один раз: в БД ложится только его SHA-256,
восстановить его потом неоткуда. Ключ подписи для прямого режима шифруется
ключом приложения.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets

from orchestrator.config import get_settings
from orchestrator.db.crypto import Cipher
from orchestrator.db.pool import close_pool, init_pool
from orchestrator.db.repo import tenants


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Зарегистрировать базу 1С")
    parser.add_argument("--id", required=True, help="Короткий идентификатор базы")
    parser.add_argument("--name", required=True, help="Название для журнала")
    parser.add_argument(
        "--transport",
        choices=["polling", "direct", "mock"],
        default="polling",
        help="Канал до базы; direct требует --base-url",
    )
    parser.add_argument("--base-url", help="Адрес HTTP-сервиса расширения (для direct)")
    parser.add_argument(
        "--no-masking",
        action="store_true",
        help="Отключить маскирование данных для этой базы",
    )
    return parser.parse_args()


async def _main() -> None:
    args = _args()
    settings = get_settings()

    if not settings.encryption_key:
        raise SystemExit("Не задан ENCRYPTION_KEY: openssl rand -base64 32")
    if args.transport == "direct" and not args.base_url:
        raise SystemExit("Для прямого режима нужен --base-url")

    cipher = Cipher.from_base64(settings.encryption_key)
    token = secrets.token_urlsafe(32)
    signing_key = secrets.token_urlsafe(32) if args.transport == "direct" else None

    await init_pool(settings.database_url, min_size=1, max_size=2)
    try:
        await tenants.register(
            tenant_id=args.id,
            name=args.name,
            token=token,
            transport=args.transport,
            base_url=args.base_url,
            masking_enabled=not args.no_masking,
            signing_key=signing_key,
            cipher=cipher,
        )
    finally:
        await close_pool()

    print(f"База «{args.name}» зарегистрирована как {args.id}")
    print()
    print("Впишите в настройки расширения 1С:")
    print(f"  токен базы:    {token}")
    if signing_key:
        print(f"  ключ подписи:  {signing_key}")
    print()
    print("Токен показан один раз — в базе хранится только его SHA-256.")


if __name__ == "__main__":
    asyncio.run(_main())
