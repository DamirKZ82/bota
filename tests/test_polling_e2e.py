"""Обратный канал целиком: оркестратор ставит задачу, «1С» её забирает и отвечает.

Проверяется то, что не видно на уровне репозитория: маршруты A.0.1, длинный
опрос, конверт задачи, изоляция очередей между базами и защита от повторного
ответа. Модель здесь не участвует — только транспорт.

Требует живую базу (`BOTA_TEST_DSN`), иначе пропускается.
"""

from __future__ import annotations

import asyncio
import os
import secrets

import httpx
import pytest

from orchestrator.db import pool as db_pool
from orchestrator.db.migrate import migrate
from orchestrator.db.repo import tenants
from orchestrator.tools.envelope import CallContext, ToolRequest
from orchestrator.transport.mock import MockTransport
from orchestrator.transport.pg_polling import PgPollingTransport

TEST_DSN = os.getenv("BOTA_TEST_DSN")
requires_db = pytest.mark.skipif(TEST_DSN is None, reason="не задан BOTA_TEST_DSN")

TOKEN = secrets.token_urlsafe(16)
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

REQUEST = ToolRequest(
    tool="reconcile_period",
    args={"organization": "org-0001", "from": "2026-04-01", "to": "2026-06-30"},
    context=CallContext(user_id="u-1", session_id="s-1", masking=True),
)


@pytest.fixture
async def client() -> httpx.AsyncClient:
    """Приложение в режиме поллинга на чистой тестовой базе.

    Всё в одном event loop: пул asyncpg привязан к циклу, в котором создан,
    поэтому синхронный TestClient со своим циклом здесь не подходит.
    """
    assert TEST_DSN is not None
    os.environ["TRANSPORT"] = "polling"

    from orchestrator.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    object.__setattr__(settings, "database_url", TEST_DSN)

    connection_dsn = db_pool.normalize_dsn(TEST_DSN)
    import asyncpg

    connection = await asyncpg.connect(connection_dsn)
    try:
        await connection.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await connection.close()
    await migrate(TEST_DSN)

    from orchestrator.main import app

    async with app.router.lifespan_context(app):
        await tenants.register(
            tenant_id="demo", name="Демо-база", token=TOKEN, transport="polling"
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as http_client:
            yield http_client

    get_settings.cache_clear()
    os.environ.pop("TRANSPORT", None)


async def _act_as_1c(client: httpx.AsyncClient) -> tuple[dict, int]:
    """Фоновое задание 1С: забрать задачу, выполнить на моке, отдать ответ."""
    for _ in range(50):
        taken = await client.get(
            "/api/v1/bases/demo/tasks", params={"wait": 0}, headers=HEADERS
        )
        if taken.status_code == 200 and taken.json():
            task = taken.json()
            response = await MockTransport().call(
                "demo", ToolRequest.model_validate(task["request"])
            )
            posted = await client.post(
                f"/api/v1/bases/demo/tasks/{task['task_id']}/result",
                json={"response": response.model_dump(mode="json", by_alias=True)},
                headers=HEADERS,
            )
            return task, posted.status_code
        await asyncio.sleep(0.1)
    raise AssertionError("задача так и не появилась в очереди")


@requires_db
async def test_задача_проходит_очередь_и_возвращается_вызывающему(
    client: httpx.AsyncClient,
) -> None:
    transport = PgPollingTransport(timeout_seconds=20, poll_interval=0.1)

    caller = asyncio.create_task(transport.call("demo", REQUEST))
    task, posted_status = await _act_as_1c(client)

    assert task["request"]["tool"] == "reconcile_period"
    # Имена полей из Приложения А доезжают до 1С неизменными.
    assert task["request"]["args"]["from"] == "2026-04-01"
    assert task["request"]["context"]["session_id"] == "s-1"
    assert posted_status == 202

    response = await caller
    assert response.ok
    assert response.result is not None
    assert response.result["calc_id"] == "calc-2026q2-0001"


@requires_db
async def test_очередь_чужой_базы_недоступна_по_валидному_токену(
    client: httpx.AsyncClient,
) -> None:
    """Токен одной базы не должен читать очередь другой, подставив base_id в URL."""
    alien = await client.get(
        "/api/v1/bases/other/tasks", params={"wait": 0}, headers=HEADERS
    )
    assert alien.status_code == 403


@requires_db
async def test_повторный_ответ_по_закрытой_задаче_отвергается(
    client: httpx.AsyncClient,
) -> None:
    """Иначе опоздавший ответ 1С может быть засчитан дважды."""
    transport = PgPollingTransport(timeout_seconds=20, poll_interval=0.1)
    caller = asyncio.create_task(transport.call("demo", REQUEST))
    task, _ = await _act_as_1c(client)
    await caller

    again = await client.post(
        f"/api/v1/bases/demo/tasks/{task['task_id']}/result",
        json={"response": {"ok": True, "tool": "reconcile_period"}},
        headers=HEADERS,
    )
    assert again.status_code == 409


@requires_db
async def test_без_токена_очередь_не_отдаётся(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/bases/demo/tasks")).status_code == 401


@requires_db
async def test_пустая_очередь_отвечает_204(client: httpx.AsyncClient) -> None:
    empty = await client.get(
        "/api/v1/bases/demo/tasks", params={"wait": 0}, headers=HEADERS
    )
    assert empty.status_code == 204
