"""Живой прогон агента по типовым запросам из ТЗ п.6.2.

Работает на мок-данных: настоящая база 1С не нужна, но нужен ANTHROPIC_API_KEY.
Это не тест — тесты модель не вызывают. Скрипт нужен, чтобы увидеть, что агент
на самом деле отвечает бухгалтеру, и показать это клиенту.

    python scripts/demo_dialog.py            # все сценарии
    python scripts/demo_dialog.py 2 4        # только выбранные
    python scripts/demo_dialog.py --ask "Свой вопрос"
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Консоль Windows по умолчанию cp1251, а в ответах агента есть ₸ и тире.
# Без этого прогон падает на печати, а не на модели.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from orchestrator.agent.loop import AgentLoop  # noqa: E402
from orchestrator.llm.anthropic_provider import AnthropicProvider  # noqa: E402
from orchestrator.tools.envelope import CallContext  # noqa: E402
from orchestrator.tools.executor import ToolExecutor  # noqa: E402
from orchestrator.transport.mock import MockTransport  # noqa: E402

#: Сценарии из ТЗ п.6.2. Мок содержит данные под кейсы R1, R2 и D03,
#: остальные показывают, как агент ведёт себя, когда данных нет.
SCENARIOS: list[str] = [
    "Сверь поступления и ЭСФ за 2 квартал 2026 года.",
    "Почему НДС к зачёту по базе отличается от суммы по ЭСФ на 3,47 тенге?",
    "Что не так с поступлением № 000145 от 14.05?",
    "Покажи расхождения с высокой критичностью и объясни самое дорогое.",
    "Найди, к какой ЭСФ относится поступление doc-receipt-145.",
    "Исправь копеечные расхождения по паттерну R1, покажи что изменится.",
    "По ЭСФ без поступлений подготовь черновик и скажи, где ты не уверен.",
]


def load_env() -> None:
    """Читает .env: скрипт запускают руками, без загрузчика приложения."""
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        return
    for key, value in re.findall(r"^([A-Z_]+)=(.*)$", env_file.read_text("utf-8"), re.M):
        os.environ.setdefault(key, value.strip())


async def run_one(agent: AgentLoop, number: int, question: str) -> None:
    context = CallContext(
        user_id="demo-user", session_id=f"demo-{number}", locale="ru", masking=False
    )

    print("=" * 78)
    print(f"[{number}] {question}")
    print("=" * 78)

    started = time.monotonic()
    result, _ = await agent.run(
        tenant_id="demo", user_message=question, context=context
    )
    elapsed = time.monotonic() - started

    print(result.text)
    print()
    print("-" * 78)
    calls = ", ".join(f"{c.tool}{'' if c.ok else ' (ошибка)'}" for c in result.calls)
    print(f"вызовов: {len(result.calls)} — {calls or 'ни одного'}")
    print(
        f"время: {elapsed:.1f} с | токенов: "
        f"вход {result.usage.get('input_tokens', 0)}, "
        f"выход {result.usage.get('output_tokens', 0)}, "
        f"из кэша {result.usage.get('cache_read_input_tokens', 0)}"
    )
    if result.truncated:
        print("ответ промежуточный: достигнут лимит вызовов")
    print()


class _Tee:
    """Пишет и в консоль, и в файл: читать длинные ответы удобнее из файла."""

    def __init__(self, stream, file) -> None:
        self._stream = stream
        self._file = file

    def write(self, text: str) -> int:
        self._file.write(text)
        return self._stream.write(text)

    def flush(self) -> None:
        self._file.flush()
        self._stream.flush()


def _tee_stdout(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sys.stdout = _Tee(sys.stdout, path.open("w", encoding="utf-8"))


async def main() -> None:
    load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "Нужен ANTHROPIC_API_KEY в .env — без него модель не вызвать."
        )

    parser = argparse.ArgumentParser(description="Прогон агента на мок-данных")
    parser.add_argument("numbers", nargs="*", type=int, help="номера сценариев с 1")
    parser.add_argument("--ask", help="задать свой вопрос вместо сценариев")
    parser.add_argument("--out", help="дополнительно записать вывод в файл UTF-8")
    args = parser.parse_args()

    if args.out:
        _tee_stdout(Path(args.out))

    agent = AgentLoop(
        provider=AnthropicProvider(
            api_key=os.environ["ANTHROPIC_API_KEY"],
            model=os.environ.get("LLM_MODEL", "claude-opus-5"),
        ),
        executor=ToolExecutor(MockTransport()),
        max_tool_calls=int(os.environ.get("MAX_TOOL_CALLS", "30")),
    )

    if args.ask:
        await run_one(agent, 0, args.ask)
        return

    chosen = args.numbers or range(1, len(SCENARIOS) + 1)
    for number in chosen:
        if not 1 <= number <= len(SCENARIOS):
            print(f"нет сценария {number}, всего {len(SCENARIOS)}")
            continue
        await run_one(agent, number, SCENARIOS[number - 1])


if __name__ == "__main__":
    asyncio.run(main())
