"""Пул asyncpg и единственный способ ходить в БД.

Правила, которые здесь закреплены кодом, а не дисциплиной:

* **Только параметризованные запросы.** Значения передаются через `$1`, не
  подставляются в текст. В multi-tenant базе с данными нескольких клиентов
  ручное экранирование — вопрос времени до утечки между тенантами.
* **Ошибка SQL сама превращается в ErrorException** с текстом запроса, поэтому
  в репозиториях ничего не оборачивается вручную.
* **Текст запроса пишется в журнал, значения — нет.** В `$1` едут БИН и
  наименования; попади они в `errors_back`, маскирование потеряло бы смысл.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg
import structlog

from orchestrator.errors import ErrorException

log = structlog.get_logger(__name__)

_pool: asyncpg.Pool | None = None


def normalize_dsn(url: str) -> str:
    """Убирает драйвер из схемы: asyncpg понимает только postgresql://.

    DATABASE_URL часто записывают в форме SQLAlchemy — `postgresql+psycopg://`,
    `postgresql+asyncpg://`. Разбираться с этим один раз здесь дешевле, чем
    ловить «invalid DSN» на старте у каждого, кто скопировал строку из другого
    проекта.
    """
    scheme, separator, rest = url.partition("://")
    if not separator:
        return url
    return f"{scheme.split('+', 1)[0]}{separator}{rest}"


async def init_pool(dsn: str, *, min_size: int = 2, max_size: int = 10) -> asyncpg.Pool:
    """Создать пул. Вызывается один раз в lifespan приложения."""
    global _pool
    if _pool is not None:
        return _pool
    _pool = await asyncpg.create_pool(
        dsn=normalize_dsn(dsn),
        min_size=min_size,
        max_size=max_size,
        init=_register_codecs,
    )
    log.info("db_pool_created", min_size=min_size, max_size=max_size)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise ErrorException(RuntimeError("Пул подключений к БД не инициализирован"))
    return _pool


async def _register_codecs(connection: asyncpg.Connection) -> None:
    """jsonb приходит и уходит как объект Python, а не как строка."""
    await connection.set_type_codec(
        "jsonb",
        encoder=lambda value: json.dumps(value, ensure_ascii=False, default=str),
        decoder=json.loads,
        schema="pg_catalog",
    )


async def query_db(sql: str, *args: Any) -> list[dict[str, Any]]:
    """SELECT (или INSERT ... RETURNING). Возвращает список словарей."""
    try:
        async with get_pool().acquire() as connection:
            rows = await connection.fetch(sql, *args)
            return [dict(row) for row in rows]
    except ErrorException:
        raise
    except Exception as err:
        raise ErrorException(err=err, sql=sql) from err


async def fetch_one(sql: str, *args: Any) -> dict[str, Any] | None:
    """Первая строка или None."""
    rows = await query_db(sql, *args)
    return rows[0] if rows else None


async def execute_db(sql: str, *args: Any) -> str:
    """INSERT / UPDATE / DELETE без возврата строк. Отдаёт статус asyncpg."""
    try:
        async with get_pool().acquire() as connection:
            return await connection.execute(sql, *args)
    except ErrorException:
        raise
    except Exception as err:
        raise ErrorException(err=err, sql=sql) from err
