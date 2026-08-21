"""Базовые типы Приложения А (раздел A.0.3).

Числа передаются **строками**: `"1234.56"`, а не `1234.56`. Это требование
Приложения и оно обосновано — весь смысл продукта в копейках, а float по дороге
через JSON способен сам создать ту ошибку, которую агент должен искать.

Внутри оркестратора эти строки остаются строками: он ничего не считает
(ТЗ п.3.1), арифметика живёт в движке сверки 1С. Помощник `to_decimal`
существует только для метрик и тестов.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

DECIMAL_STRING = r"^-?\d+(\.\d+)?$"

Money = Annotated[str, Field(pattern=DECIMAL_STRING, description="Сумма, 2 знака, строкой")]
Qty = Annotated[str, Field(pattern=DECIMAL_STRING, description="Количество, до 3 знаков, строкой")]
Rate = Annotated[str, Field(description="Ставка НДС: «16», «0» или «exempt»")]
Bin = Annotated[str, Field(pattern=r"^\d{12}$", description="БИН/ИИН, 12 цифр")]


def to_decimal(value: str | None) -> Decimal:
    """Строка суммы → Decimal. Только для метрик и тестов, не для расчётов."""
    if value is None:
        return Decimal("0")
    try:
        return Decimal(value)
    except InvalidOperation:
        return Decimal("0")


class Contract(BaseModel):
    """База для всех контрактов: запрещаем лишние поля в обе стороны.

    Строгость намеренная — молча проглоченное опечатанное поле от 1С означает,
    что агент увидит неполные данные и назовёт неверную цифру.
    """

    # populate_by_name — потому что часть имён из Приложения А («from», «to»)
    # совпадает с ключевыми словами Python и объявлена через alias.
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class Ref(Contract):
    """Ссылка на объект 1С (A.0.3). Единый формат везде, где нужен переход в базу."""

    type: str = Field(description="Тип объекта: «Документ.ПоступлениеТМЗИУслуг»")
    uuid: str = Field(description="GUID ссылки")
    presentation: str = Field(
        description="Представление для показа. При маскировании — псевдоним",
    )
    nav: str | None = Field(
        default=None,
        description="Навигационная ссылка e1cib/… Не маскируется, в модель не уходит",
    )


class Page(Contract):
    """Постраничная выдача (A.0.3)."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)
    total: int
    has_more: bool


class Settings(Contract):
    """Настройки сверки (A.1). Значения по умолчанию — допуски из ТЗ п.4.4."""

    line_tolerance_per_unit: Money = Field(default="0.01")
    line_tolerance_max: Money = Field(default="1.00")
    doc_tolerance_per_line: Money = Field(default="0.01")
    doc_tolerance_max: Money = Field(default="5.00")
    period_tolerance_total: Money = Field(default="1.00")
    esf_tail_days: int = Field(
        default=20,
        description="«Хвост» периода для отбора ЭСФ — срок выписки (ТЗ п.4.1)",
    )
    candidate_days: int = Field(
        default=10,
        description="Допуск по дате при поиске кандидатов на связь",
    )


class Permissions(Contract):
    """Права текущего пользователя 1С (A.1).

    Определяют, какие действия агент вправе предлагать: без `create` нет смысла
    готовить черновики поступлений.
    """

    read: bool
    apply: bool
    create: bool


class Totals(Contract):
    """Итоги документа: без НДС, НДС, с НДС."""

    net: Money
    vat: Money
    total: Money


class DocumentLine(Contract):
    """Строка табличной части — одинаковая форма для поступления и ЭСФ (A.4)."""

    n: int = Field(description="Номер строки, с 1")
    item: Ref | None = Field(default=None, description="Номенклатура; в ЭСФ может отсутствовать")
    name: str = Field(description="Наименование как в документе")
    unit: str | None = None
    qty: Qty
    price: Money = Field(description="Цена за единицу; больше 2 знаков — источник R3")
    net: Money
    vat_rate: Rate
    vat: Money
    total: Money


class ReceiptHead(Contract):
    """Шапка поступления (A.4)."""

    ref: Ref
    date: dt.date
    number: str
    posted: bool = Field(description="Непроведённый документ даёт код D16")
    vat_included_in_price: bool = Field(
        description="Флаг «Сумма включает НДС». Расхождение флага — паттерн R2",
    )
    currency: str = Field(default="KZT")
    exchange_rate: str | None = Field(default=None, description="Курс, если валюта не KZT")
    totals: Totals


class EsfHead(Contract):
    """Шапка ЭСФ (A.4)."""

    ref: Ref
    date_issue: dt.date = Field(description="Дата выписки в ИС ЭСФ")
    date_turnover: dt.date = Field(
        description="Дата совершения оборота — определяет период НДС (источник D08)",
    )
    reg_number: str
    status: str
    kind: str = Field(description="original | fixed | additional")
    replaces: Ref | None = Field(
        default=None,
        description="Для исправленной или дополнительной ЭСФ — исправляемая",
    )
    totals: Totals


class ReceiptDocument(Contract):
    head: ReceiptHead
    lines: list[DocumentLine]


class EsfDocument(Contract):
    head: EsfHead
    lines: list[DocumentLine]


class CounterpartyBrief(Contract):
    ref: Ref
    bin: Bin | None = Field(default=None, description="None для нерезидентов")
