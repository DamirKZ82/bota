"""Справочники кодов (ТЗ разделы 4.3–4.5, Приложение А).

Единственный источник правды. И движок в 1С, и оркестратор используют одни и те
же строковые значения — они уходят в LLM, в журнал и в отчёт на СКД.

Значения латиницей, как в Приложении А: `high`, а не «высокая». Русские подписи
хранятся отдельно (`*_LABEL`) и нужны только для показа пользователю — модель
работает с кодами, а не с текстом.
"""

from enum import StrEnum


class Severity(StrEnum):
    """Критичность расхождения (ТЗ п.4.5, A.2)."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


SEVERITY_LABEL: dict[Severity, str] = {
    Severity.HIGH: "высокая",
    Severity.MEDIUM: "средняя",
    Severity.LOW: "низкая",
    Severity.INFO: "инфо",
}


class Confidence(StrEnum):
    """Уверенность сопоставления строк (п.4.3) и подбора номенклатуры (A.9.5)."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


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


#: Описание и критичность по умолчанию (ТЗ п.4.5).
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


class RoundingPattern(StrEnum):
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


ROUNDING_META: dict[RoundingPattern, tuple[str, str]] = {
    RoundingPattern.R1_LINE_VS_TOTAL_VAT: (
        "Построчное округление НДС vs от итога",
        "Допуск; при необходимости корректировка суммы НДС в строке",
    ),
    RoundingPattern.R2_VAT_INCLUDED_FLAG: (
        "НДС «в том числе» vs «сверху»",
        "Изменить флаг «Сумма включает НДС» в поступлении",
    ),
    RoundingPattern.R3_PRICE_PRECISION: (
        "Округление цены (в ЭСФ 4+ знака, в 1С 2 знака)",
        "Привести цену к значению из ЭСФ / уточнить точность цены",
    ),
    RoundingPattern.R4_REMAINDER_ON_LAST_LINE: (
        "Перенос остатка округления на последнюю строку",
        "Принять ЭСФ как эталон, скорректировать строку",
    ),
    RoundingPattern.R5_CURRENCY_RATE: ("Пересчёт валюты", "Проверить дату курса"),
    RoundingPattern.R6_UNEXPLAINED: ("Необъяснимая разница", "Ручной разбор"),
}


class LineMatch(StrEnum):
    """Результат сопоставления строк (A.4)."""

    MATCHED = "matched"
    MATCHED_WITHIN_TOLERANCE = "matched_within_tolerance"
    MATCHED_WITH_DIFF = "matched_with_diff"
    ONLY_IN_RECEIPT = "only_in_receipt"
    ONLY_IN_ESF = "only_in_esf"
    ONE_TO_MANY = "one_to_many"
    MANY_TO_ONE = "many_to_one"


#: Уровень сопоставления 1–5 (ТЗ п.4.3) и соответствующая ему уверенность.
#: В Приложении А `match_level` — число, а не строка.
LEVEL_CONFIDENCE: dict[int, Confidence] = {
    1: Confidence.HIGH,  # справочник соответствий номенклатуры контрагента
    2: Confidence.HIGH,  # нормализованное наименование + количество + цена
    3: Confidence.MEDIUM,  # количество + цена + ставка
    4: Confidence.LOW,  # сумма строки + ставка
    5: Confidence.LOW,  # нечёткое совпадение наименования
}


class DiscrepancyStatus(StrEnum):
    """Состояние расхождения в работе бухгалтера (A.3)."""

    OPEN = "open"
    REVIEWED = "reviewed"
    FIXED = "fixed"


class EsfStatus(StrEnum):
    """Статус ЭСФ в ИС ЭСФ (A.3, A.4)."""

    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REVOKED = "revoked"
    DRAFT = "draft"


class EsfKind(StrEnum):
    """Вид ЭСФ (A.4): основная, исправленная, дополнительная."""

    ORIGINAL = "original"
    FIXED = "fixed"
    ADDITIONAL = "additional"


class LinkKind(StrEnum):
    """Как связаны поступление и ЭСФ (A.4)."""

    EXPLICIT = "explicit"
    SUGGESTED = "suggested"
    NONE = "none"


class ItemSource(StrEnum):
    """Откуда взялся подбор номенклатуры (ТЗ п.5.2.1, A.9.5)."""

    MAPPING = "mapping"
    HISTORY = "history"
    EXACT = "exact"
    FUZZY = "fuzzy"
    NONE = "none"


class AdjustStrategy(StrEnum):
    """Чей документ считается эталоном при правке строк (A.9.2)."""

    ESF_AS_MASTER = "esf_as_master"
    RECEIPT_AS_MASTER = "receipt_as_master"
    CUSTOM = "custom"


class ErrorCode(StrEnum):
    """Коды ошибок расширения (A.0.2).

    Модель видит код и решает, что делать: `BAD_ARGS` — исправить параметры,
    `PERIOD_CLOSED` — предложить корректировку, `TIMEOUT` — повторить позже.
    """

    BAD_ARGS = "BAD_ARGS"
    NOT_FOUND = "NOT_FOUND"
    ACCESS_DENIED = "ACCESS_DENIED"
    PERIOD_CLOSED = "PERIOD_CLOSED"
    LOCKED = "LOCKED"
    TIMEOUT = "TIMEOUT"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    INTERNAL = "INTERNAL"
    NOT_SUPPORTED_RELEASE = "NOT_SUPPORTED_RELEASE"


#: Ошибки, при которых имеет смысл повторить вызов.
RETRYABLE_ERRORS: frozenset[ErrorCode] = frozenset(
    {ErrorCode.TIMEOUT, ErrorCode.LOCKED, ErrorCode.INTERNAL}
)
