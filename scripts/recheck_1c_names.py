"""Сверка имён, на которые опирается расширение, с реальной базой 1С.

Расширение знает имена объектов типовой в одном месте — в
`БотаМетаданныеТиповой.Имена()`. Этот скрипт проверяет их по стандартному
интерфейсу OData, не открывая конфигуратор. Запускать при смене базы, релиза
конфигурации и перед развёртыванием на новой площадке.

Имена здесь дублируют модуль намеренно: смысл проверки в том, чтобы сравнить
ожидание с фактом. Разошлись — правится модуль, а следом этот список.

    python scripts/recheck_1c_names.py --base-url http://localhost/WK --user odata.user

Пароль запрашивается интерактивно либо берётся из BOTA_1C_PASSWORD.

Состав стандартного интерфейса OData настраивается в конфигураторе
(Администрирование → Настройка состава стандартного интерфейса OData).
Объекты, не включённые в состав, скрипт не увидит — это не значит, что их нет
в конфигурации.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import os
import re
import sys

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: Что расширение ожидает найти. Ключ — сущность OData, значение — поля.
EXPECTED: dict[str, list[str]] = {
    "Document_ПоступлениеТоваровУслуг": [
        "Организация_Key",
        "Контрагент_Key",
        "СуммаВключаетНДС",
        "Товары",
    ],
    "Document_ПоступлениеТоваровУслуг_Товары_RowType": [
        "Номенклатура_Key",
        "Количество",
        "Цена",
        "Сумма",
        "СтавкаНДС_Key",
        "СуммаНДС",
    ],
    "Document_СчетФактураПолученный": [
        "ДокументОснование",
        "ДокументыОснования",
        "Товары",
    ],
    "Document_ЭСФ": [
        "ДатаОборота",
        "РегистрационныйНомер",
        "Статус",
        "Вид",
        "Направление",
        # Среднее звено связи: ЭСФ → счёт-фактура → поступление.
        "СчетФактура",
        "Контрагент_Key",
        "Организация_Key",
        "Товары",
    ],
    "Document_ЭСФ_Товары_RowType": [
        # Имена полей строк ЭСФ отличаются от поступления — это и проверяем.
        "ТоварНаименование",
        "СуммаБезНалогов",
        "СтавкаНДСЧисло",
        "СуммаНДС",
        "Сумма",
    ],
}


def fetch_metadata(base_url: str, user: str, password: str) -> str:
    auth = base64.b64encode(f"{user}:{password}".encode()).decode()
    url = f"{base_url.rstrip('/')}/odata/standard.odata/$metadata"
    response = httpx.get(
        url, headers={"Authorization": f"Basic {auth}"}, timeout=300
    )
    if response.status_code == 401:
        raise SystemExit("База отвергла учётные данные (401).")
    if response.status_code != 200:
        raise SystemExit(f"База вернула {response.status_code} на {url}")
    return response.text


def check(xml: str) -> list[str]:
    problems: list[str] = []
    for entity, fields in EXPECTED.items():
        # Сущности и составные типы объявляются разными тегами, ищем оба.
        pattern = (
            r'<(?:EntityType|ComplexType) Name="%s"'
            r".*?</(?:EntityType|ComplexType)>" % re.escape(entity)
        )
        block = re.search(pattern, xml, re.S)
        if not block:
            problems.append(
                f"{entity}: не найден. Либо объект не включён в состав "
                f"OData, либо называется иначе"
            )
            print(f"[нет] {entity}")
            continue

        present = set(re.findall(r'<Property Name="([^"]+)"', block.group(0)))
        missing = [field for field in fields if field not in present]
        if missing:
            problems.append(f"{entity}: не найдены поля {', '.join(missing)}")
            print(f"[!]   {entity}: нет {', '.join(missing)}")
        else:
            print(f"[ок]  {entity}")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description="Сверка имён 1С с расширением")
    parser.add_argument("--base-url", required=True, help="Например http://localhost/WK")
    parser.add_argument("--user", required=True, help="Пользователь 1С с правом на OData")
    args = parser.parse_args()

    # Пароль не передаётся аргументом: он остался бы в истории команд.
    password = os.environ.get("BOTA_1C_PASSWORD")
    if password is None:
        password = getpass.getpass(f"Пароль для {args.user}: ")

    print(f"Сверяю имена по {args.base_url}\n")
    problems = check(fetch_metadata(args.base_url, args.user, password))

    print()
    if problems:
        print(f"Расхождений: {len(problems)}")
        print(
            "Поправьте БотаМетаданныеТиповой.Имена() под фактические имена, "
            "а затем этот список — он должен отражать то, на что опирается код."
        )
        sys.exit(1)

    print("Все ожидаемые имена на месте.")


if __name__ == "__main__":
    main()
