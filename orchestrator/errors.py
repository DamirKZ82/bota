"""Два типа ошибок: виноват запрос или виноват сервер.

Разделение взято из backend-guidelines: `WarnException` не требует вмешательства
инженера и показывается пользователю как есть, `ErrorException` требует разбора
и сохраняется с трейсбеком и текстом упавшего SQL.
"""

from __future__ import annotations


class WarnException(Exception):
    """Некорректный запрос. Сообщение уходит пользователю дословно."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class ErrorException(Exception):
    """Сбой на сервере. Пользователь видит нейтральный текст, инженер — детали."""

    def __init__(self, err: BaseException | None = None, sql: str | None = None) -> None:
        super().__init__(str(err) if err else "Внутренняя ошибка")
        self.err = err
        self.sql = sql


USER_FACING_ERROR = (
    "Произошла ошибка на сервере. Попробуйте позже или обратитесь в техподдержку."
)
