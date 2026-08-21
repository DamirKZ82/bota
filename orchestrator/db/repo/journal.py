"""Журнал: вызовы инструментов, предложенные планы и их судьба (ТЗ п.8).

Всё, что сюда пишется, уже замаскировано: аргументы приходят от модели, то есть
в псевдонимах, а заголовок плана — тот же текст, что видел пользователь.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from orchestrator.db.pool import execute_db, fetch_one, query_db


async def record_tool_call(
    *,
    tenant_id: str,
    dialog_id: str | None,
    user_key: str,
    tool_name: str,
    onec_method: str,
    arguments: dict[str, Any],
    ok: bool,
    duration_ms: int,
    error_message: str | None = None,
) -> None:
    await execute_db(
        """
        INSERT INTO tool_calls (tenant_id, dialog_id, user_key, tool_name, onec_method,
                                arguments, ok, error_message, duration_ms)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """,
        tenant_id,
        dialog_id,
        user_key,
        tool_name,
        onec_method,
        arguments,
        ok,
        error_message,
        duration_ms,
    )


async def record_plan(
    *,
    plan_id: str,
    tenant_id: str,
    dialog_id: str | None,
    tool_name: str,
    title: str,
    discrepancy_id: str | None,
    changes_count: int,
    blocked: bool,
    block_reason: str | None,
) -> None:
    """Запомнить предложенный план. Тело плана остаётся в 1С — здесь только след."""
    await execute_db(
        """
        INSERT INTO change_plans (plan_id, tenant_id, dialog_id, tool_name, discrepancy_id,
                                  title, changes_count, blocked, block_reason)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (plan_id) DO NOTHING
        """,
        plan_id,
        tenant_id,
        dialog_id,
        tool_name,
        discrepancy_id,
        title,
        changes_count,
        blocked,
        block_reason,
    )


async def resolve_plan(
    *,
    tenant_id: str,
    plan_id: str,
    status: str,
    resolved_by: str,
    created_documents: list[dict[str, Any]] | None = None,
    failure_message: str | None = None,
) -> bool:
    """Отметить исход плана: применён, отклонён или упал при применении.

    Вызывается из 1С после действия пользователя. Возвращает False, если план
    уже был закрыт раньше, — повторное применение журнал не переписывает.
    """
    if status not in {"applied", "rejected", "failed", "expired"}:
        raise ValueError(f"Недопустимый статус плана: {status}")

    rows = await query_db(
        """
        UPDATE change_plans
           SET status            = $3,
               resolved_at       = now(),
               resolved_by       = $4,
               created_documents = $5,
               failure_message   = $6
         WHERE tenant_id = $1
           AND plan_id   = $2
           AND status    = 'proposed'
        RETURNING plan_id
        """,
        tenant_id,
        plan_id,
        status,
        resolved_by,
        created_documents or [],
        failure_message,
    )
    return bool(rows)


@dataclass(frozen=True, slots=True)
class PlanStats:
    proposed: int
    applied: int
    rejected: int


async def plan_stats(*, tenant_id: str) -> PlanStats:
    """Сколько предложено и сколько принято — метрика доверия к агенту."""
    row = await fetch_one(
        """
        SELECT count(*) FILTER (WHERE status = 'proposed') AS proposed,
               count(*) FILTER (WHERE status = 'applied')  AS applied,
               count(*) FILTER (WHERE status = 'rejected') AS rejected
          FROM change_plans
         WHERE tenant_id = $1
        """,
        tenant_id,
    )
    if row is None:
        return PlanStats(0, 0, 0)
    return PlanStats(int(row["proposed"]), int(row["applied"]), int(row["rejected"]))


# -- ошибки и предупреждения ------------------------------------------------


async def record_error(
    *,
    tenant_id: str | None,
    message: str,
    traceback: str | None = None,
    sql: str | None = None,
    method: str | None = None,
    path: str | None = None,
    user_key: str | None = None,
) -> int | None:
    row = await fetch_one(
        """
        INSERT INTO errors_back (tenant_id, message, traceback, sql, method, path, user_key)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id
        """,
        tenant_id,
        message,
        traceback,
        sql,
        method,
        path,
        user_key,
    )
    return int(row["id"]) if row else None


async def record_warn(
    *,
    tenant_id: str | None,
    status_code: int,
    message: str,
    method: str | None = None,
    path: str | None = None,
    user_key: str | None = None,
) -> None:
    await execute_db(
        """
        INSERT INTO warns (tenant_id, status_code, message, method, path, user_key)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        tenant_id,
        status_code,
        message,
        method,
        path,
        user_key,
    )
