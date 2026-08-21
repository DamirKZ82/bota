"""Общая настройка тестов.

Читает `.env`, чтобы тесты БД запускались обычным `pytest`, без ручного
экспорта переменной. Если `BOTA_TEST_DSN` не задан, тесты, которым нужна живая
база, пропускаются — прогон не должен зависеть от того, поднят ли Postgres.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def pytest_configure() -> None:
    if not ENV_FILE.exists():
        return
    values = dict(
        re.findall(r"^([A-Z_]+)=(.*)$", ENV_FILE.read_text(encoding="utf-8"), re.M)
    )
    # Переменная окружения важнее: ею можно указать другую базу на один прогон.
    for key in ("BOTA_TEST_DSN",):
        if key in values and key not in os.environ:
            os.environ[key] = values[key].strip()
