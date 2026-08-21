"""Проверка модулей 1С без конфигуратора.

Код расширения нельзя откомпилировать вне 1С, поэтому здесь ловится то, что
ловится статически:

* **смешанные алфавиты в идентификаторе** — латинская «e» внутри русского
  слова. Код при этом работает (объявление и вызов совпадают), но найти такую
  функцию по имени невозможно, а глазами разница не видна;
* **баланс блоков** — незакрытый `Если` или лишний `КонецЦикла` в длинном
  модуле ищется в конфигураторе долго и обидно;
* **вызовы несуществующих экспортных методов** между нашими модулями.

Разбор учитывает две особенности языка: многострочные строковые литералы
(текст запроса продолжается символом `|`) и условия, перенесённые на несколько
строк. Без этого проверки дают ложные срабатывания.

    python scripts/lint_bsl.py
    python scripts/lint_bsl.py extension/modules/БотаДвижокСверки.bsl
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MODULES_DIR = Path(__file__).resolve().parent.parent / "extension" / "modules"

CYRILLIC = re.compile(r"[а-яёА-ЯЁ]")
LATIN = re.compile(r"[a-zA-Z]")
IDENTIFIER = re.compile(r"[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*")

#: Открывающая конструкция → закрывающая.
BLOCKS: dict[str, str] = {
    "процедура": "конецпроцедуры",
    "функция": "конецфункции",
    "если": "конецесли",
    "цикл": "конеццикла",
    "попытка": "конецпопытки",
    "#область": "#конецобласти",
}
CLOSERS = {value: key for key, value in BLOCKS.items()}

KEYWORD = re.compile(
    r"#?\b(процедура|функция|конецпроцедуры|конецфункции|если|иначеесли|конецесли"
    r"|цикл|конеццикла|попытка|конецпопытки|область|конецобласти)\b",
    re.I,
)

#: Слова языка запросов, где смешение алфавитов допустимо.
ALLOWED_MIXED = {"естьnull"}

#: Русское слово с приклеенным кодом классификатора: ПроверитьR1, КарточкаD06.
#: Коды в ТЗ записаны латиницей, поэтому такое смешение осмысленно.
CODE_SUFFIX = re.compile(r"^[А-Яа-яЁё_]+(?:[A-Z]\d+)+$")

#: Аббревиатуры, которые в именах платформы пишутся латиницей:
#: ПолучитьHexСтрокуИзДвоичныхДанных, ЗначениеВСтрокуXML, HTTPСоединение.
ABBREVIATIONS = (
    "xml", "json", "html", "http", "https", "hex", "url", "uuid", "guid",
    "odata", "soap", "dom", "zip", "csv", "pdf", "sha", "md5", "base64", "id",
    "uri", "url", "openssl", "ssl", "tls", "ftp", "smtp", "imap", "pop3", "dbf",
)


def known_abbreviation_only(word: str) -> bool:
    """True, если латиница в слове — только известные аббревиатуры."""
    rest = word.lower()
    for abbreviation in sorted(ABBREVIATIONS, key=len, reverse=True):
        rest = rest.replace(abbreviation, "")
    return not LATIN.search(rest)


def logical_lines(lines: list[str]) -> list[tuple[int, str]]:
    """Склеивает физические строки в логические, выбрасывая литералы и комментарии.

    Внутри многострочного текста запроса встречаются слова вроде «ГДЕ» и «И»,
    а также имена реквизитов — считать их кодом нельзя.
    """
    result: list[tuple[int, str]] = []
    buffer = ""
    buffer_start = 0
    inside_string = False

    for number, raw in enumerate(lines, start=1):
        line = raw

        if inside_string:
            # Ищем закрывающую кавычку; всё до неё — часть литерала.
            closing = line.find('"')
            if closing == -1:
                continue
            line = line[closing + 1 :]
            inside_string = False

        # Убираем парные литералы, затем смотрим, не осталась ли открытая кавычка.
        line = re.sub(r'"[^"]*"', '""', line)
        if line.count('"') % 2 == 1:
            inside_string = True
            line = line[: line.rfind('"')]

        code = line.split("//", 1)[0].strip()
        if not code:
            continue

        if not buffer:
            buffer_start = number
        buffer = f"{buffer} {code}".strip()

        # Логическая строка завершена, когда закончилось выражение или открылся блок.
        lowered = buffer.lower()
        if (
            code.endswith(";")
            or lowered.endswith("тогда")
            or lowered.endswith("цикл")
            or lowered.endswith("иначе")
            or lowered.endswith("попытка")
            or lowered.endswith("исключение")
            or lowered.startswith("#")
            or re.match(r"^(процедура|функция)\b", lowered)
            or lowered in CLOSERS
        ):
            result.append((buffer_start, buffer))
            buffer = ""

    if buffer:
        result.append((buffer_start, buffer))
    return result


def check_mixed_alphabet(path: Path, lines: list[tuple[int, str]]) -> list[str]:
    problems: list[str] = []
    for number, code in lines:
        for word in IDENTIFIER.findall(code):
            if (
                word.lower() in ALLOWED_MIXED
                or CODE_SUFFIX.match(word)
                or known_abbreviation_only(word)
            ):
                continue
            if CYRILLIC.search(word) and LATIN.search(word):
                problems.append(
                    f"{path.name}:{number}: идентификатор «{word}» смешивает "
                    f"кириллицу и латиницу"
                )
    return problems


def check_blocks(path: Path, lines: list[tuple[int, str]]) -> list[str]:
    problems: list[str] = []
    stack: list[tuple[str, int]] = []

    for number, code in lines:
        for match in KEYWORD.finditer(code):
            word = match.group(1).lower()
            if match.group(0).startswith("#"):
                word = "#" + word

            if word == "иначеесли":
                continue

            if word in CLOSERS:
                opener = CLOSERS[word]
                if not stack:
                    problems.append(f"{path.name}:{number}: лишний «{word}»")
                elif stack[-1][0] != opener:
                    problems.append(
                        f"{path.name}:{number}: встретился «{word}», а ожидался "
                        f"«{BLOCKS[stack[-1][0]]}» (открыт в строке {stack[-1][1]})"
                    )
                    stack.pop()
                else:
                    stack.pop()
                continue

            if word in BLOCKS:
                stack.append((word, number))

    for opener, number in stack:
        problems.append(
            f"{path.name}:{number}: не закрыт «{opener}» (ожидается «{BLOCKS[opener]}»)"
        )
    return problems


def check_exports(paths: list[Path]) -> list[str]:
    """Вызовы вида `БотаМодуль.Функция(...)` должны существовать в наших модулях."""
    declared: dict[str, set[str]] = {}
    for path in paths:
        text = path.read_text(encoding="utf-8")
        declared[path.stem] = set(
            re.findall(
                r"^\s*(?:Функция|Процедура)\s+([A-Za-zА-Яа-яЁё0-9_]+)\s*\([^)]*\)\s*Экспорт",
                text,
                re.M | re.I,
            )
        )

    problems: list[str] = []
    for path in paths:
        for number, code in logical_lines(path.read_text(encoding="utf-8").splitlines()):
            for module, method in re.findall(
                # Просмотр назад: «РегистрыСведений.БотаПланы.…» — обращение
                # к регистру, а не вызов общего модуля.
                r"(?<![\w.])(Бота[A-Za-zА-Яа-яЁё0-9_]*)"
                r"\.([A-Za-zА-Яа-яЁё0-9_]+)\s*\(",
                code,
            ):
                if module == path.stem or module not in declared:
                    continue  # модуль ещё не написан — это нормально по ходу работы
                if method not in declared[module]:
                    problems.append(
                        f"{path.name}:{number}: {module}.{method}() не объявлена "
                        f"как экспортная в {module}.bsl"
                    )
    return problems


def main() -> None:
    targets = (
        [Path(a) for a in sys.argv[1:]]
        if len(sys.argv) > 1
        else sorted(MODULES_DIR.glob("*.bsl"))
    )
    if not targets:
        print("Модули не найдены")
        sys.exit(0)

    problems: list[str] = []
    for path in targets:
        parsed = logical_lines(path.read_text(encoding="utf-8").splitlines())
        problems += check_mixed_alphabet(path, parsed)
        problems += check_blocks(path, parsed)
    problems += check_exports(targets)

    for problem in problems:
        print(problem)

    print()
    print(f"Проверено модулей: {len(targets)}, замечаний: {len(problems)}")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
