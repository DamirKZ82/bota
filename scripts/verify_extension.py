"""Приёмочная проверка расширения 1С (спецификация, раздел 10).

Гоняет по HTTP-сервису расширения те же вызовы, которыми тесты проверяют мок, и
валидирует ответы контрактами Приложения А. Красная строка означает расхождение
с Приложением — и правым считается Приложение.

    python scripts/verify_extension.py --base-url https://server/base --token … --key …
    python scripts/verify_extension.py --only get_context reconcile_period
    python scripts/verify_extension.py --ids ids.json      # свои идентификаторы базы

`ids.json` — плоский словарь подмен, например:

    {"organization": "1c-uuid…", "receipt_uuid": "1c-uuid…", "bin": "123456789012"}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from orchestrator.tools.envelope import CallContext  # noqa: E402
from orchestrator.tools.executor import ToolExecutor  # noqa: E402
from orchestrator.tools.registry import TOOLS  # noqa: E402
from orchestrator.tools.samples import SAMPLE_ARGS, WRITES_SOMETHING  # noqa: E402
from orchestrator.transport.direct import DirectTransport, TenantEndpoint  # noqa: E402

OK = "  ок  "
FAIL = " ошибка "
SKIP = "пропуск"


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Проверка расширения 1С по Приложению А")
    parser.add_argument("--base-url", required=True, help="Адрес базы, без /hs/bota/v1")
    parser.add_argument("--token", required=True, help="Токен базы")
    parser.add_argument("--key", required=True, help="Ключ подписи запросов")
    parser.add_argument("--only", nargs="*", help="Проверить только эти инструменты")
    parser.add_argument("--ids", help="JSON с идентификаторами реальной базы")
    parser.add_argument(
        "--allow-writes",
        action="store_true",
        help="Разрешить инструменты, которые что-то пишут (mark_reviewed)",
    )
    parser.add_argument("--timeout", type=int, default=180)
    return parser.parse_args()


def _substitute(args: dict[str, Any], ids: dict[str, Any]) -> dict[str, Any]:
    """Подменяет значения из образца на идентификаторы реальной базы."""
    return {key: ids.get(key, value) for key, value in args.items()}


async def main() -> None:
    options = _args()
    ids: dict[str, Any] = {}
    if options.ids:
        ids = json.loads(Path(options.ids).read_text(encoding="utf-8"))

    endpoint = TenantEndpoint(
        tenant_id="verify",
        base_url=options.base_url,
        token=options.token,
        signing_key=options.key,
    )

    async def resolve(_: str) -> TenantEndpoint:
        return endpoint

    transport = DirectTransport(resolve, timeout_seconds=options.timeout)
    executor = ToolExecutor(transport)
    context = CallContext(user_id="verify", session_id="verify-session", masking=False)

    names = options.only or [spec.name for spec in TOOLS]
    passed, failed, skipped = 0, 0, 0
    transport_errors = 0

    print(f"Проверяю {len(names)} инструментов на {options.base_url}\n")

    for name in names:
        if name in WRITES_SOMETHING and not options.allow_writes:
            print(f"[{SKIP}] {name} — пишет в базу, нужен --allow-writes")
            skipped += 1
            continue

        arguments = _substitute(SAMPLE_ARGS.get(name, {}), ids)
        outcome = await executor.execute(
            tenant_id="verify", tool_name=name, arguments=arguments, context=context
        )

        if outcome.ok:
            print(f"[{OK}] {name} ({outcome.duration_ms} мс)")
            passed += 1
        else:
            code = outcome.error_code.value if outcome.error_code else "?"
            detail = json.loads(outcome.payload).get("error", {}).get("message", "")
            print(f"[{FAIL}] {name} — {code}: {detail}")
            failed += 1
            if code in {"INTERNAL", "TIMEOUT"} and "недоступна" in detail:
                transport_errors += 1

    await transport.aclose()

    print()
    print(f"Прошло: {passed}, ошибок: {failed}, пропущено: {skipped}")
    if failed and transport_errors == failed:
        # Не путать разработчика: до базы просто не достучались.
        print(
            "\nВсе ошибки сетевые: база не отвечает по указанному адресу. "
            "Проверьте, опубликован ли HTTP-сервис и доступен ли он снаружи."
        )
    elif failed:
        print(
            "\nОшибка означает расхождение с Приложением А. Сверьте формат ответа "
            "инструмента с разделом Приложения, где он описан."
        )
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
