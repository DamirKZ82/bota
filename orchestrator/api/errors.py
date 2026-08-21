"""Глобальная обработка ошибок.

Правило из гайдлайнов: 2xx только при успехе, пользователю — понятный текст,
инженеру — трейсбек и упавший SQL в `errors_back`. Роутеры ничего не оборачивают.

Сохранение в БД делается «мягко»: если БД недоступна (а это частая причина самой
ошибки), запись падать не должна — иначе пользователь вместо сообщения получит
пустой ответ.
"""

from __future__ import annotations

import traceback

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from orchestrator.config import get_settings
from orchestrator.db.repo import journal
from orchestrator.errors import USER_FACING_ERROR, ErrorException, WarnException

log = structlog.get_logger(__name__)


def install(app: FastAPI) -> None:
    app.add_exception_handler(WarnException, _handle_warn)  # type: ignore[arg-type]
    app.add_exception_handler(ErrorException, _handle_error)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _handle_unexpected)


async def _handle_warn(request: Request, exc: WarnException) -> JSONResponse:
    log.info("warn", status=exc.status_code, message=exc.message, path=request.url.path)
    await _save_warn(request, exc)
    return JSONResponse(status_code=exc.status_code, content={"message": exc.message})


async def _handle_error(request: Request, exc: ErrorException) -> JSONResponse:
    await _save_error(request, str(exc), traceback.format_exc(), exc.sql)
    return JSONResponse(status_code=500, content={"message": USER_FACING_ERROR})


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    await _save_error(request, str(exc), traceback.format_exc(), None)
    return JSONResponse(status_code=500, content={"message": USER_FACING_ERROR})


def _tenant_id(request: Request) -> str | None:
    tenant = getattr(request.state, "tenant", None)
    return getattr(tenant, "id", None)


async def _save_warn(request: Request, exc: WarnException) -> None:
    if get_settings().storage != "postgres":
        return
    try:
        await journal.record_warn(
            tenant_id=_tenant_id(request),
            status_code=exc.status_code,
            message=exc.message,
            method=request.method,
            path=request.url.path,
        )
    except Exception as err:
        log.warning("warn_not_saved", error=str(err))


async def _save_error(
    request: Request, message: str, tb: str, sql: str | None
) -> None:
    # В лог — без значений параметров запроса: в них едут БИН и наименования.
    log.error("error", message=message, path=request.url.path, has_sql=sql is not None)
    if get_settings().storage != "postgres":
        return
    try:
        await journal.record_error(
            tenant_id=_tenant_id(request),
            message=message,
            traceback=tb,
            sql=sql,
            method=request.method,
            path=request.url.path,
        )
    except Exception as err:
        log.warning("error_not_saved", error=str(err))
