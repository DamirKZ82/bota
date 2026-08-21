"""Базовые типы, общие для всех контрактов инструментов.

Денежные суммы — только `Decimal`. Весь смысл продукта в копеечных расхождениях
(ТЗ п.4.4), поэтому float здесь недопустим: он сам является источником ошибки,
которую агент должен искать. В JSON Decimal сериализуется строкой.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.tools.enums import DocumentKind, EsfStatus


class Contract(BaseModel):
    """База для всех контрактов: запрещаем лишние поля в обе стороны.

    Строгость намеренная — молча проглоченное опечатанное поле от 1С означает,
    что агент увидит неполные данные и назовёт неверную цифру.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


Money = Decimal
"""Сумма в валюте учёта. Точность — 2 знака, кроме цены (см. `Line.price`)."""


class DocumentRef(Contract):
    """Ссылка на объект 1С. Возвращается всеми инструментами вместо «голого» GUID."""

    kind: DocumentKind
    uuid: str = Field(description="GUID ссылки объекта в базе 1С")
    number: str = Field(description="Номер документа")
    date: dt.date
    presentation: str = Field(description="Представление для показа пользователю")
    navigation_link: str | None = Field(
        default=None,
        description="Навигационная ссылка e1cib/... для клика в ответе агента (ТЗ п.5.3)",
    )


class Organization(Contract):
    """Организация базы (ТЗ п.5.1, ПолучитьКонтекст)."""

    uuid: str
    name: str
    bin: str = Field(description="БИН организации", min_length=12, max_length=12)
    is_vat_payer: bool
    accounting_currency: str = Field(default="KZT", description="Код валюты учёта, ISO 4217")


class CounterpartyBrief(Contract):
    """Контрагент в составе документа."""

    uuid: str
    name: str
    bin: str | None = Field(default=None, description="БИН/ИИН; None для нерезидентов")


class Period(Contract):
    """Период сверки. Границы включаются обе."""

    date_from: dt.date
    date_to: dt.date

    def __str__(self) -> str:
        return f"{self.date_from:%d.%m.%Y}–{self.date_to:%d.%m.%Y}"


class Tolerances(Contract):
    """Допуски округления (ТЗ п.4.4). Значения по умолчанию — из ТЗ.

    Допуск на строку и на документ считается по формуле «база × множитель,
    но не более потолка»: например на документ 0,01 × число строк, максимум 5,00 ₸.
    """

    line_per_unit: Money = Field(
        default=Decimal("0.01"),
        description="Допуск на единицу количества в строке",
    )
    line_cap: Money = Field(default=Decimal("1.00"), description="Потолок допуска на строку")
    document_per_line: Money = Field(
        default=Decimal("0.01"),
        description="Допуск на одну строку документа",
    )
    document_cap: Money = Field(default=Decimal("5.00"), description="Потолок допуска на документ")
    period_material: Money = Field(
        default=Decimal("1.00"),
        description="Порог существенности для итогов периода",
    )


class Line(Contract):
    """Строка табличной части поступления или ЭСФ.

    Одинаковая форма для обеих сторон — движок сверки сравнивает однотипные объекты.
    """

    number: int = Field(description="Номер строки в документе, с 1")
    item_name: str = Field(description="Наименование номенклатуры как в документе")
    item_uuid: str | None = Field(
        default=None,
        description="GUID номенклатуры; None для строк ЭСФ, где номенклатура не сопоставлена",
    )
    uom: str | None = Field(default=None, description="Единица измерения")
    quantity: Decimal
    price: Decimal = Field(
        description="Цена за единицу. Может иметь больше 2 знаков — источник расхождений R3",
    )
    amount_net: Money = Field(description="Сумма без НДС")
    vat_rate: Decimal = Field(description="Ставка НДС в процентах, например 12")
    amount_vat: Money
    amount_gross: Money = Field(description="Сумма с НДС")


class DocumentHeader(Contract):
    """Шапка документа, общая часть поступления и ЭСФ."""

    ref: DocumentRef
    organization: Organization
    counterparty: CounterpartyBrief
    contract_name: str | None = Field(default=None, description="Договор контрагента")
    currency: str = Field(default="KZT")
    exchange_rate: Decimal = Field(default=Decimal("1"))
    amount_net: Money
    amount_vat: Money
    amount_gross: Money
    vat_included_in_price: bool = Field(
        description="Флаг «Сумма включает НДС». Расхождение флага — паттерн R2",
    )
    is_posted: bool = Field(description="Проведён ли документ; непроведённые дают D16")


class ReceiptDocument(Contract):
    """Поступление ТМЗ и услуг (или доп. расходов / авансовый отчёт)."""

    header: DocumentHeader
    lines: list[Line]
    esf_number: str | None = Field(
        default=None,
        description="Номер ЭСФ из реквизитов поступления, если заполнен (ТЗ п.4.2)",
    )
    esf_date: dt.date | None = None
    linked_esf: DocumentRef | None = Field(
        default=None,
        description="Явная связь с ЭСФ (документ-основание)",
    )


class EsfDocument(Contract):
    """Электронный счёт-фактура (полученный)."""

    header: DocumentHeader
    lines: list[Line]
    status: EsfStatus
    turnover_date: dt.date = Field(
        description="Дата совершения оборота — определяет период НДС (источник D08)",
    )
    issue_date: dt.date = Field(description="Дата выписки в ИС ЭСФ")
    registration_number: str = Field(description="Регистрационный номер ЭСФ в ИС ЭСФ")
    linked_receipt: DocumentRef | None = Field(
        default=None,
        description="Явная связь с поступлением (документ-основание)",
    )
    corrects: DocumentRef | None = Field(
        default=None,
        description="Для исправленной/дополнительной ЭСФ — исправляемая ЭСФ",
    )
