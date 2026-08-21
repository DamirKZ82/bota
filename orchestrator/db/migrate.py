"""Раннер миграций: нумерованные .sql-файлы, применяемые по одному в транзакции.

Alembic здесь не нужен — он инструмент для SQLAlchemy, а проект работает на чистом
asyncpg. Плоские SQL-файлы читаются глазами, ложатся в код-ревью и не требуют
модели данных в Python, которой у нас нет.

Запуск:  python -m orchestrator.db.migrate
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import asyncpg
import structlog

from orchestrator.config import get_settings

log = structlog.get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

#: Миграции, которые не применяются автоматически: требуют расширений или
#: решений, ещё не принятых (см. комментарий в самом файле).
OPTIONAL: frozenset[str] = frozenset({"0002_knowledge"})


def discover() -> list[tuple[str, Path]]:
    """Все миграции по возрастанию номера."""
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    return [(path.stem, path) for path in files]


async def applied_versions(connection: asyncpg.Connection) -> set[str]:
    exists = await connection.fetchval("SELECT to_regclass('public.schema_migrations')")
    if exists is None:
        return set()
    rows = await connection.fetch("SELECT version FROM schema_migrations")
    return {row["version"] for row in rows}


async def migrate(dsn: str, *, include_optional: bool = False) -> list[str]:
    """Применить недостающие миграции. Возвращает применённые версии."""
    connection = await asyncpg.connect(dsn)
    try:
        done = await applied_versions(connection)
        newly_applied: list[str] = []

        for version, path in discover():
            if version in done:
                continue
            if version in OPTIONAL and not include_optional:
                log.info("migration_skipped", version=version, reason="optional")
                continue

            sql = path.read_text(encoding="utf-8")
            # Каждая миграция — одна транзакция: либо применилась целиком,
            # либо база осталась в прежнем состоянии.
            async with connection.transaction():
                await connection.execute(sql)
                await connection.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1)", version
                )
            newly_applied.append(version)
            log.info("migration_applied", version=version)

        if not newly_applied:
            log.info("migrations_up_to_date", applied=len(done))
        return newly_applied
    finally:
        await connection.close()


def _dsn_for_asyncpg(url: str) -> str:
    """DATABASE_URL хранится в форме SQLAlchemy; asyncpg ждёт postgresql://."""
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _main() -> None:
    settings = get_settings()
    include_optional = "--with-optional" in sys.argv
    applied = await migrate(
        _dsn_for_asyncpg(settings.database_url), include_optional=include_optional
    )
    for version in applied:
        print(f"применена: {version}")


if __name__ == "__main__":
    asyncio.run(_main())
