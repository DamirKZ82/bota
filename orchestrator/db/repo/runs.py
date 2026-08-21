"""Метрики сверок — то, чем меряется успех пилота (ТЗ п.1.3).

Только агрегаты: количества, длительности и суммарная разница округлений.
Ни одного документа и ни одного контрагента здесь нет, поэтому таблицу можно
выгружать для анализа целиком.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from orchestrator.db.pool import execute_db, query_db


async def record(
    *,
    tenant_id: str,
    organization_uuid: str,
    period_from: dt.date,
    period_to: dt.date,
    pairs_total: int,
    receipts_total: int,
    esf_total: int,
    rounding_total: Decimal,
    by_code: list[dict[str, Any]],
    duration_ms: int,
    from_cache: bool,
) -> None:
    await execute_db(
        """
        INSERT INTO reconciliation_runs (tenant_id, organization_uuid, period_from, period_to,
                                         pairs_total, receipts_total, esf_total,
                                         rounding_total, by_code, duration_ms, from_cache)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """,
        tenant_id,
        organization_uuid,
        period_from,
        period_to,
        pairs_total,
        receipts_total,
        esf_total,
        rounding_total,
        by_code,
        duration_ms,
        from_cache,
    )


@dataclass(frozen=True, slots=True)
class RunSummary:
    runs: int
    avg_duration_ms: int
    max_duration_ms: int
    rounding_total: Decimal


async def summary(*, tenant_id: str, since: dt.date) -> RunSummary:
    """Сводка по базе: укладываемся ли в норматив 90 с на сверку периода (ТЗ п.8)."""
    rows = await query_db(
        """
        SELECT count(*)                       AS runs,
               coalesce(avg(duration_ms), 0)  AS avg_ms,
               coalesce(max(duration_ms), 0)  AS max_ms,
               coalesce(sum(rounding_total), 0) AS rounding
          FROM reconciliation_runs
         WHERE tenant_id = $1
           AND created_at >= $2
           AND NOT from_cache
        """,
        tenant_id,
        since,
    )
    row = rows[0]
    return RunSummary(
        runs=int(row["runs"]),
        avg_duration_ms=int(row["avg_ms"]),
        max_duration_ms=int(row["max_ms"]),
        rounding_total=Decimal(row["rounding"]),
    )
