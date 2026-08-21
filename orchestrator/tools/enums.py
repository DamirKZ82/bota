"""Справочники кодов из ТЗ (разделы 4.3–4.5).

Единственный источник правды для кодов расхождений. И движок в 1С, и оркестратор
обязаны использовать одни и те же строковые значения — они уходят в LLM, в журнал
и в отчёт на СКД.
"""

from enum import StrEnum


class Severity(StrEnum):
    """Критичность расхождения (ТЗ п.4.5)."""

    HIGH = "высокая"
    MEDIUM = "средняя"
    LOW = "низкая"
    INFO = "инфо"


class Confidence(StrEnum):
    """Уверенность сопоставления — строк (п.4.3) и подбора номенклатуры (п.5.2.1)."""

    HIGH = "высокая"
    MEDIUM = "средняя"
    LOW = "низкая"


class DiscrepancyCode(StrEnum):
    """Итоговый классификатор расхождений (ТЗ п.4.5, таблица D01–D16)."""

    D01_NO_ESF_IN_TERM = "D01"
    D02_NO_ESF_OVERDUE = "D02"
    D03_ESF_WITHOUT_RECEIPT = "D03"
    D04_LINK_SUGGESTED = "D04"
    D05_VAT_RATE_MISMATCH = "D05"
    D06_VAT_AMOUNT_MISMATCH = "D06"
    D07_NET_AMOUNT_MISMATCH = "D07"
    D08_DIFFERENT_VAT_PERIOD = "D08"
    D09_ESF_CANCELLED = "D09"
    D10_ESF_CORRECTED = "D10"
    D11_LINES_MISMATCH_TOTALS_OK = "D11"
    D12_LINE_ONLY_ON_ONE_SIDE = "D12"
    D13_UOM_MISMATCH = "D13"
    D14_ROUNDING_WITHIN_TOLERANCE = "D14"
    D15_COUNTERPARTY_NOT_VAT_PAYER = "D15"
    D16_RECEIPT_NOT_POSTED = "D16"


#: Человекочитаемые описания и критичность по умолчанию (ТЗ п.4.5).
#: Критичность может быть переопределена настройками базы.
DISCREPANCY_META: dict[DiscrepancyCode, tuple[str, Severity]] = {
    DiscrepancyCode.D01_NO_ESF_IN_TERM: (
        "Поступление без ЭСФ (срок выписки не истёк)",
        Severity.INFO,
    ),
    DiscrepancyCode.D02_NO_ESF_OVERDUE: ("Поступление без ЭСФ (срок истёк)", Severity.HIGH),
    DiscrepancyCode.D03_ESF_WITHOUT_RECEIPT: ("ЭСФ без поступления", Severity.HIGH),
    DiscrepancyCode.D04_LINK_SUGGESTED: (
        "Найдена предполагаемая связь, не установлена",
        Severity.MEDIUM,
    ),
    DiscrepancyCode.D05_VAT_RATE_MISMATCH: ("Разная ставка НДС", Severity.HIGH),
    DiscrepancyCode.D06_VAT_AMOUNT_MISMATCH: (
        "Расхождение суммы НДС выше допуска",
        Severity.HIGH,
    ),
    DiscrepancyCode.D07_NET_AMOUNT_MISMATCH: (
        "Расхождение суммы без НДС выше допуска",
        Severity.HIGH,
    ),
    DiscrepancyCode.D08_DIFFERENT_VAT_PERIOD: (
        "Разные периоды НДС (дата оборота ЭСФ в другом квартале)",
        Severity.HIGH,
    ),
    DiscrepancyCode.D09_ESF_CANCELLED: (
        "ЭСФ аннулирована/отозвана, поступление активно",
        Severity.HIGH,
    ),
    DiscrepancyCode.D10_ESF_CORRECTED: (
        "Исправленная ЭСФ — поступление по старой",
        Severity.MEDIUM,
    ),
    DiscrepancyCode.D11_LINES_MISMATCH_TOTALS_OK: (
        "Расхождение по строкам при совпадении итогов",
        Severity.MEDIUM,
    ),
    DiscrepancyCode.D12_LINE_ONLY_ON_ONE_SIDE: (
        "Строка только в поступлении / только в ЭСФ",
        Severity.MEDIUM,
    ),
    DiscrepancyCode.D13_UOM_MISMATCH: ("Расхождение единиц измерения", Severity.LOW),
    DiscrepancyCode.D14_ROUNDING_WITHIN_TOLERANCE: (
        "Копеечное расхождение в допуске (см. R1–R6)",
        Severity.INFO,
    ),
    DiscrepancyCode.D15_COUNTERPARTY_NOT_VAT_PAYER: (
        "Контрагент не плательщик НДС / снят с учёта на дату оборота",
        Severity.HIGH,
    ),
    DiscrepancyCode.D16_RECEIPT_NOT_POSTED: ("Поступление не проведено", Severity.MEDIUM),
}


class RoundingCode(StrEnum):
    """Источник копеечной разницы (ТЗ п.4.4, таблица R1–R6).

    Определяет, какое исправление предлагать: R1–R4 автоматизируемы,
    R5 требует проверки курса, R6 — ручной разбор.
    """

    R1_LINE_VS_TOTAL_VAT = "R1"
    R2_VAT_INCLUDED_FLAG = "R2"
    R3_PRICE_PRECISION = "R3"
    R4_REMAINDER_ON_LAST_LINE = "R4"
    R5_CURRENCY_RATE = "R5"
    R6_UNEXPLAINED = "R6"


ROUNDING_META: dict[RoundingCode, tuple[str, str]] = {
    RoundingCode.R1_LINE_VS_TOTAL_VAT: (
        "Построчное округление НДС vs от итога",
        "Допуск; при необходимости корректировка суммы НДС в строке",
    ),
    RoundingCode.R2_VAT_INCLUDED_FLAG: (
        "НДС «в том числе» vs «сверху»",
        "Изменить флаг «Сумма включает НДС» в поступлении",
    ),
    RoundingCode.R3_PRICE_PRECISION: (
        "Округление цены (в ЭСФ 4+ знака, в 1С 2 знака)",
        "Привести цену к значению из ЭСФ / уточнить точность цены",
    ),
    RoundingCode.R4_REMAINDER_ON_LAST_LINE: (
        "Перенос остатка округления на последнюю строку",
        "Принять ЭСФ как эталон, скорректировать строку",
    ),
    RoundingCode.R5_CURRENCY_RATE: ("Пересчёт валюты", "Проверить дату курса"),
    RoundingCode.R6_UNEXPLAINED: ("Необъяснимая разница", "Ручной разбор"),
}


class LineMatchLevel(StrEnum):
    """Уровень, на котором сопоставились строки (ТЗ п.4.3, таблица уровней 1–5)."""

    L1_EXPLICIT_MAPPING = "1"  # справочник соответствий номенклатуры контрагента
    L2_NORMALIZED_NAME = "2"  # нормализованное наименование + количество + цена
    L3_QTY_PRICE_RATE = "3"  # количество + цена + ставка
    L4_AMOUNT_RATE = "4"  # сумма строки + ставка
    L5_FUZZY_NAME = "5"  # нечёткое совпадение наименования


#: Уверенность, соответствующая уровню сопоставления (ТЗ п.4.3).
LEVEL_CONFIDENCE: dict[LineMatchLevel, Confidence] = {
    LineMatchLevel.L1_EXPLICIT_MAPPING: Confidence.HIGH,
    LineMatchLevel.L2_NORMALIZED_NAME: Confidence.HIGH,
    LineMatchLevel.L3_QTY_PRICE_RATE: Confidence.MEDIUM,
    LineMatchLevel.L4_AMOUNT_RATE: Confidence.LOW,
    LineMatchLevel.L5_FUZZY_NAME: Confidence.LOW,
}


class LineStatus(StrEnum):
    """Результат сверки строки (ТЗ п.4.3)."""

    MATCH = "совпадает"
    MATCH_WITHIN_TOLERANCE = "совпадает_в_допуске"
    PRICE_MISMATCH = "расхождение_цены"
    QTY_MISMATCH = "расхождение_количества"
    AMOUNT_MISMATCH = "расхождение_суммы"
    RATE_MISMATCH = "расхождение_ставки"
    UOM_MISMATCH = "расхождение_единицы"
    ONLY_IN_RECEIPT = "только_в_поступлении"
    ONLY_IN_ESF = "только_в_ЭСФ"
    ONE_TO_MANY = "одна_против_нескольких"


class EsfStatus(StrEnum):
    """Статус ЭСФ (ТЗ п.4.2)."""

    ACTIVE = "выписана"
    CANCELLED = "аннулирована"
    REVOKED = "отозвана"
    CORRECTED = "исправленная"
    ADDITIONAL = "дополнительная"


class DocumentKind(StrEnum):
    """Виды документов, участвующих в сверке (ТЗ п.2)."""

    RECEIPT_GOODS = "ПоступлениеТМЗиУслуг"
    RECEIPT_EXTRA_COSTS = "ПоступлениеДопРасходов"
    EXPENSE_REPORT = "АвансовыйОтчет"
    ESF_RECEIVED = "ЭСФПолученный"
    ADJUSTMENT = "КорректировкаПоступления"
