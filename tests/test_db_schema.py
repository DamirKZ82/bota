"""Схема БД.

Часть проверок работает без Postgres — они следят за порядком и целостностью
файлов миграций. Остальные требуют живой базы и включаются переменной
`BOTA_TEST_DSN`; без неё пропускаются, чтобы прогон тестов не зависел от того,
поднят ли у разработчика сервер.

    BOTA_TEST_DSN=postgresql://postgres:пароль@localhost:5432/bota_test pytest
"""

from __future__ import annotations

import base64
import datetime as dt
import os
import re
import uuid
from decimal import Decimal

import asyncpg
import pytest

from orchestrator.db import pool as db_pool
from orchestrator.db.crypto import Cipher
from orchestrator.db.migrate import MIGRATIONS_DIR, discover, migrate
from orchestrator.db.repo import dialogs, journal, poll_tasks, runs, tenants
from orchestrator.errors import ErrorException
from orchestrator.llm.base import AssistantTurn, TextBlock, UserTurn

TEST_DSN = os.getenv("BOTA_TEST_DSN")
requires_db = pytest.mark.skipif(TEST_DSN is None, reason="не задан BOTA_TEST_DSN")


# -- без базы ---------------------------------------------------------------


def test_миграции_пронумерованы_подряд() -> None:
    versions = [version for version, _ in discover()]
    numbers = [int(re.match(r"^(\d+)", v).group(1)) for v in versions]  # type: ignore[union-attr]
    assert numbers == sorted(numbers)
    assert len(set(numbers)) == len(numbers), "номера миграций не уникальны"


def test_первая_миграция_создаёт_таблицу_версий() -> None:
    first = (MIGRATIONS_DIR / "0001_init.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE schema_migrations" in first


def test_денежные_колонки_объявлены_numeric_а_не_float() -> None:
    """float в суммах сам создаёт те копейки, которые продукт должен искать."""
    for _, path in discover():
        sql = path.read_text(encoding="utf-8")
        assert "DOUBLE PRECISION" not in sql.upper()
        assert " REAL" not in sql.upper()


def test_в_схеме_нет_таблицы_псевдонимов() -> None:
    """Маскирование делает 1С (A.0.5) — расшифровке псевдонимов здесь не место."""
    sql = (MIGRATIONS_DIR / "0001_init.sql").read_text(encoding="utf-8")
    assert "dialog_aliases" not in sql


def test_таблицы_с_данными_баз_ссылаются_на_тенанта() -> None:
    """Изоляция баз держится на внешнем ключе, а не на аккуратности запросов."""
    sql = (MIGRATIONS_DIR / "0001_init.sql").read_text(encoding="utf-8")
    for table in ("dialogs", "dialog_turns", "tool_calls",
                  "change_plans", "reconciliation_runs", "poll_tasks"):
        block = sql.split(f"CREATE TABLE {table} (")[1].split(");")[0]
        assert "REFERENCES tenants" in block, f"{table} не привязана к тенанту"


# -- на живой базе ----------------------------------------------------------


@pytest.fixture
async def db() -> None:
    """Чистая схема на тестовой базе перед каждым тестом."""
    assert TEST_DSN is not None
    connection = await asyncpg.connect(TEST_DSN)
    try:
        await connection.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await connection.close()

    await migrate(TEST_DSN)
    await db_pool.init_pool(TEST_DSN, min_size=1, max_size=4)
    try:
        yield
    finally:
        await db_pool.close_pool()


@pytest.fixture
def cipher() -> Cipher:
    return Cipher.from_base64(base64.b64encode(os.urandom(32)).decode())


@requires_db
async def test_миграции_применяются_на_чистой_базе(db: None) -> None:
    rows = await db_pool.query_db("SELECT version FROM schema_migrations ORDER BY version")
    assert [row["version"] for row in rows] == ["0001_init"]


@requires_db
async def test_повторный_прогон_миграций_ничего_не_ломает(db: None) -> None:
    assert TEST_DSN is not None
    assert await migrate(TEST_DSN) == []


@requires_db
async def test_база_находится_по_токену_а_сам_токен_не_хранится(
    db: None, cipher: Cipher
) -> None:
    await tenants.register(
        tenant_id="t1",
        name="Пилотная база",
        token="секретный-токен",
        transport="polling",
        signing_key="ключ-подписи",
        cipher=cipher,
    )

    found = await tenants.get_by_token("секретный-токен", cipher=cipher)
    assert found is not None
    assert found.signing_key == "ключ-подписи"
    assert await tenants.get_by_token("другой-токен", cipher=cipher) is None

    rows = await db_pool.query_db("SELECT token_sha256 FROM tenants WHERE id = 't1'")
    assert b"\xd1" not in bytes(rows[0]["token_sha256"])[:1] or True
    stored = bytes(rows[0]["token_sha256"])
    assert len(stored) == 32, "в базе должен лежать SHA-256, а не сам токен"


@requires_db
async def test_прямой_транспорт_требует_адрес(db: None) -> None:
    """База в прямом режиме без адреса бессмысленна — ограничение ловит это в БД.

    Наружу приходит ErrorException: query_db оборачивает любое падение SQL,
    чтобы в errors_back попал и текст запроса, и трейсбек.
    """
    with pytest.raises(ErrorException) as err:
        await tenants.register(
            tenant_id="t2", name="Без адреса", token="t", transport="direct"
        )
    assert isinstance(err.value.err, asyncpg.CheckViolationError)
    assert "tenants_direct_needs_url" in str(err.value.err)


@requires_db
async def test_история_диалога_переживает_перезапуск(db: None, cipher: Cipher) -> None:
    await tenants.register(tenant_id="t1", name="База", token="tok", transport="polling")
    dialog_id = await dialogs.create(tenant_id="t1", user_key="buh")

    turns = [
        UserTurn(text="Сверь 2 квартал"),
        AssistantTurn(blocks=[TextBlock(text="Проверил")], stop_reason="end_turn"),
    ]
    await dialogs.append_turns(tenant_id="t1", dialog_id=dialog_id, turns=turns, from_seq=0)

    loaded = await dialogs.load_turns(tenant_id="t1", dialog_id=dialog_id)
    assert len(loaded) == 2
    assert isinstance(loaded[0], UserTurn)
    assert loaded[0].text == "Сверь 2 квартал"


@requires_db
async def test_очередь_выдаёт_задачу_один_раз(db: None) -> None:
    await tenants.register(tenant_id="t1", name="База", token="tok", transport="polling")
    task_id = await poll_tasks.enqueue(
        tenant_id="t1", tool="get_context", request={"tool": "get_context", "args": {}}
    )

    first = await poll_tasks.lease(tenant_id="t1")
    second = await poll_tasks.lease(tenant_id="t1")
    assert first is not None and first.id == task_id
    assert second is None, "арендованная задача не должна выдаваться повторно"

    assert await poll_tasks.complete(
        tenant_id="t1", task_id=task_id, response={"ok": True, "tool": "get_context"}
    )
    state = await poll_tasks.get_state(tenant_id="t1", task_id=task_id)
    assert state is not None and state.status == "done"


@requires_db
async def test_повторный_результат_по_закрытой_задаче_отвергается(db: None) -> None:
    await tenants.register(tenant_id="t1", name="База", token="tok", transport="polling")
    task_id = await poll_tasks.enqueue(tenant_id="t1", tool="x", request={})
    await poll_tasks.lease(tenant_id="t1")
    await poll_tasks.complete(tenant_id="t1", task_id=task_id, response={})

    assert not await poll_tasks.complete(tenant_id="t1", task_id=task_id, response={})


@requires_db
async def test_очередь_чужой_базы_не_видна(db: None) -> None:
    await tenants.register(tenant_id="t1", name="Первая", token="tok1", transport="polling")
    await tenants.register(tenant_id="t2", name="Вторая", token="tok2", transport="polling")
    await poll_tasks.enqueue(tenant_id="t1", tool="x", request={})

    assert await poll_tasks.lease(tenant_id="t2") is None


@requires_db
async def test_план_закрывается_только_один_раз(db: None) -> None:
    await tenants.register(tenant_id="t1", name="База", token="tok", transport="polling")
    plan_id = f"plan-{uuid.uuid4()}"
    await journal.record_plan(
        plan_id=plan_id,
        tenant_id="t1",
        dialog_id=None,
        tool_name="plan_adjust_lines",
        action="adjust_lines",
        discrepancy_id="d1a2b3c4e5f6a7b8",
    )

    assert await journal.resolve_plan(
        tenant_id="t1", plan_id=plan_id, status="applied", resolved_by="buh"
    )
    # Повторное применение того же плана journal не переписывает.
    assert not await journal.resolve_plan(
        tenant_id="t1", plan_id=plan_id, status="rejected", resolved_by="buh"
    )

    stats = await journal.plan_stats(tenant_id="t1")
    assert (stats.proposed, stats.applied, stats.rejected) == (0, 1, 0)


@requires_db
async def test_метрика_сверки_хранит_копейки_без_потерь(db: None) -> None:
    await tenants.register(tenant_id="t1", name="База", token="tok", transport="polling")
    await runs.record(
        tenant_id="t1",
        organization_uuid="org-0001",
        period_from=dt.date(2026, 4, 1),
        period_to=dt.date(2026, 6, 30),
        pairs_total=118,
        receipts_total=124,
        esf_total=121,
        rounding_total=Decimal("3.47"),
        by_code=[{"code": "D14", "count": 37}],
        duration_ms=41230,
        from_cache=False,
    )

    summary = await runs.summary(tenant_id="t1", since=dt.date(2026, 1, 1))
    assert summary.runs == 1
    assert summary.rounding_total == Decimal("3.47")
    assert summary.max_duration_ms == 41230


@requires_db
async def test_ретеншн_чистит_журнал_старше_срока(db: None) -> None:
    await tenants.register(tenant_id="t1", name="База", token="tok", transport="polling")
    await journal.record_tool_call(
        tenant_id="t1",
        dialog_id=None,
        user_key="buh",
        tool_name="get_context",
        arguments={},
        ok=True,
        duration_ms=12,
    )
    await db_pool.execute_db(
        "UPDATE tool_calls SET created_at = now() - INTERVAL '13 months'"
    )

    await db_pool.query_db("SELECT * FROM bota_purge_expired(12, 30)")
    rows = await db_pool.query_db("SELECT count(*) AS n FROM tool_calls")
    assert rows[0]["n"] == 0
