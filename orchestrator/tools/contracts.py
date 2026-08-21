"""Приложение А: контракты инструментов агента (ТЗ раздел 5).

Один класс на вход и один на выход каждого инструмента. Из этих же моделей
генерируется JSON Schema, которая уходит в LLM как определение tool, — то есть
контракт с 1С и контракт с моделью описаны один раз и не могут разъехаться.

Инструменты записи представлены здесь ТОЛЬКО фазой 1 («план изменений»).
Фаза 2 (применение) агенту недоступна принципиально: её вызывает пользователь
кнопкой в 1С (ТЗ п.5.2, правило 1). См. orchestrator/tools/registry.py.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from pydantic import Field

from orchestrator.tools.common import (
    Contract,
    CounterpartyBrief,
    DocumentRef,
    EsfDocument,
    Line,
    Money,
    Organization,
    Period,
    ReceiptDocument,
    Tolerances,
)
from orchestrator.tools.enums import (
    Confidence,
    DiscrepancyCode,
    DocumentKind,
    LineMatchLevel,
    LineStatus,
    RoundingCode,
    Severity,
)

# ---------------------------------------------------------------------------
# 5.1. Чтение
# ---------------------------------------------------------------------------


class GetContextIn(Contract):
    """ПолучитьКонтекст — параметров нет."""


class GetContextOut(Contract):
    organizations: list[Organization]
    current_period: Period = Field(description="Текущий незакрытый период НДС")
    closed_until: dt.date | None = Field(
        default=None,
        description="Дата запрета изменения данных; левее неё запись запрещена",
    )
    configuration_version: str = Field(description="Релиз «Бухгалтерии для Казахстана»")
    platform_version: str
    accounting_currency: str = Field(default="KZT")
    tolerances: Tolerances


class ReconcilePeriodIn(Contract):
    """СверитьПериод — основной вход в движок сверки."""

    organization_uuid: str
    period: Period
    counterparty_bin: str | None = Field(
        default=None,
        description="Ограничить сверку одним контрагентом",
    )
    min_severity: Severity | None = Field(
        default=None,
        description="Не возвращать расхождения ниже указанной критичности",
    )


class CodeCount(Contract):
    code: DiscrepancyCode
    description: str
    severity: Severity
    count: int
    amount_impact: Money = Field(
        description="Суммарное влияние на НДС к зачёту по этому коду",
    )


class ReconcilePeriodOut(Contract):
    period: Period
    organization: Organization
    pairs_total: int = Field(description="Число сформированных пар поступление↔ЭСФ")
    receipts_total: int
    esf_total: int
    by_code: list[CodeCount]
    rounding_total: Money = Field(
        description="Накопленная разница округлений за период — «хвост» в декларации (ТЗ п.4.4)",
    )
    discrepancy_ids: list[str] = Field(
        description="ID расхождений для последующего ПолучитьРасхождение",
    )
    computed_at: dt.datetime
    from_cache: bool = Field(description="Результат взят из кэша периода (ТЗ п.8)")


class FieldComparison(Contract):
    """Сравнение одного реквизита пары (ТЗ п.4.2)."""

    field: str = Field(description="Имя реквизита, например «Сумма НДС»")
    receipt_value: str | None
    esf_value: str | None
    difference: str | None = Field(default=None, description="Разница в читаемом виде")
    within_tolerance: bool


class LineComparison(Contract):
    """Результат сопоставления пары строк (ТЗ п.4.3)."""

    receipt_line: Line | None = Field(default=None, description="None, если строка только в ЭСФ")
    esf_lines: list[Line] = Field(
        default_factory=list,
        description="Несколько строк при сопоставлении «одна против нескольких»",
    )
    status: LineStatus
    match_level: LineMatchLevel | None = None
    confidence: Confidence | None = None
    difference_net: Money = Field(default=Decimal("0"))
    difference_vat: Money = Field(default=Decimal("0"))
    within_tolerance: bool = False


class DiscrepancyCard(Contract):
    """Полная карточка расхождения — то, на чём агент строит объяснение."""

    id: str
    code: DiscrepancyCode
    description: str
    severity: Severity
    receipt: DocumentRef | None = None
    esf: DocumentRef | None = None
    counterparty: CounterpartyBrief | None = None
    field_comparisons: list[FieldComparison] = Field(default_factory=list)
    line_comparisons: list[LineComparison] = Field(default_factory=list)
    rounding_code: RoundingCode | None = Field(
        default=None,
        description="Определённый источник копеечной разницы (R1–R6)",
    )
    probable_cause: str = Field(description="Вероятная причина, сформулированная движком, не LLM")
    recommended_action: str = Field(description="Рекомендуемое действие из таблицы R/D")
    suggested_tool: str | None = Field(
        default=None,
        description="Имя инструмента фазы 1, которым это чинится",
    )
    reviewed: bool = Field(default=False, description="Помечено «рассмотрено» ранее")
    reviewed_comment: str | None = None


class GetDiscrepancyIn(Contract):
    discrepancy_id: str


class GetDiscrepancyOut(Contract):
    card: DiscrepancyCard


class ListDiscrepanciesIn(Contract):
    organization_uuid: str
    period: Period
    codes: list[DiscrepancyCode] | None = None
    counterparty_bin: str | None = None
    min_severity: Severity | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)


class DiscrepancyBrief(Contract):
    """Строка постраничного списка — без построчного сравнения, чтобы не раздувать контекст."""

    id: str
    code: DiscrepancyCode
    severity: Severity
    counterparty_name: str | None
    receipt: DocumentRef | None
    esf: DocumentRef | None
    amount_impact: Money
    summary: str


class ListDiscrepanciesOut(Contract):
    items: list[DiscrepancyBrief]
    page: int
    page_size: int
    total: int
    has_more: bool


class GetDocumentIn(Contract):
    uuid: str
    kind: DocumentKind


class GetDocumentOut(Contract):
    receipt: ReceiptDocument | None = None
    esf: EsfDocument | None = None


class FindEsfCandidatesIn(Contract):
    receipt_uuid: str
    days_tolerance: int = Field(
        default=20,
        ge=0,
        le=120,
        description="Допуск по дате оборота, ±дней",
    )
    amount_tolerance: Money = Field(default=Decimal("1.00"))


class Candidate(Contract):
    """Кандидат на связь с оценкой совпадения (ТЗ п.4.1, шаги 4–5)."""

    ref: DocumentRef
    score: Decimal = Field(ge=0, le=1, description="Оценка совпадения, 0..1")
    confidence: Confidence
    matched_on: list[str] = Field(description="По каким признакам совпало: БИН, сумма, дата")
    amount_difference: Money
    days_difference: int


class FindEsfCandidatesOut(Contract):
    candidates: list[Candidate]


class FindReceiptCandidatesIn(Contract):
    esf_uuid: str
    days_tolerance: int = Field(default=20, ge=0, le=120)
    amount_tolerance: Money = Field(default=Decimal("1.00"))


class FindReceiptCandidatesOut(Contract):
    candidates: list[Candidate]


class GetCounterpartyIn(Contract):
    bin: str = Field(min_length=12, max_length=12)
    on_date: dt.date | None = Field(
        default=None,
        description="Дата, на которую проверяется статус плательщика НДС",
    )


class GetCounterpartyOut(Contract):
    counterparty: CounterpartyBrief
    is_vat_payer_on_date: bool
    vat_certificate_series: str | None = None
    vat_certificate_number: str | None = None
    vat_registered_from: dt.date | None = None
    vat_deregistered_from: dt.date | None = Field(
        default=None,
        description="Дата снятия с учёта по НДС — основание для D15",
    )


class GetVatTurnoversIn(Contract):
    organization_uuid: str
    period: Period


class VatTurnoverRow(Contract):
    vat_rate: Decimal
    amount_net: Money
    amount_vat: Money
    source: str = Field(description="«учёт» — обороты по счетам; «ЭСФ» — сумма по ЭСФ")


class GetVatTurnoversOut(Contract):
    rows: list[VatTurnoverRow]
    accounting_vat_total: Money
    esf_vat_total: Money
    difference: Money = Field(description="Расхождение зачётного НДС между учётом и ЭСФ")


class GetChangeHistoryIn(Contract):
    uuid: str
    kind: DocumentKind


class DocumentVersion(Contract):
    version: int
    changed_at: dt.datetime
    changed_by: str
    summary: str


class GetChangeHistoryOut(Contract):
    versioning_enabled: bool
    versions: list[DocumentVersion]


# ---------------------------------------------------------------------------
# 5.2. Запись — только фаза 1 (план изменений)
# ---------------------------------------------------------------------------


class PlannedChange(Contract):
    """Одно изменение в плане: что, где, было → станет."""

    target: DocumentRef
    path: str = Field(description="Реквизит или строка, например «Товары[3].Цена»")
    old_value: str | None
    new_value: str | None
    comment: str | None = None


class ChangePlan(Contract):
    """Результат фазы 1 любого инструмента записи (ТЗ п.5.2).

    Агент возвращает план пользователю; применить его может только пользователь
    кнопкой в 1С. `plan_id` — то, что уходит в фазу 2.
    """

    plan_id: str
    tool: str = Field(description="Инструмент, породивший план")
    discrepancy_id: str | None = None
    title: str = Field(description="Заголовок для кнопки в панели «Предложенные действия»")
    changes: list[PlannedChange]
    affected_postings: list[str] = Field(
        default_factory=list,
        description="Затрагиваемые проводки — пользователь должен видеть последствия",
    )
    blocked: bool = Field(
        default=False,
        description="True, если применить нельзя (закрытый период, нет прав)",
    )
    block_reason: str | None = None
    requires_reposting: bool = Field(default=False)


class SetLinkPlanIn(Contract):
    """УстановитьСвязь, фаза 1."""

    receipt_uuid: str
    esf_uuid: str
    discrepancy_id: str | None = None


class AdjustLinesPlanIn(Contract):
    """СкорректироватьСтроки, фаза 1. ЭСФ принимается за эталон."""

    receipt_uuid: str
    line_numbers: list[int] = Field(
        default_factory=list,
        description="Номера строк поступления; пустой список — все расходящиеся строки",
    )
    discrepancy_id: str | None = None


class ChangeVatFlagPlanIn(Contract):
    """ИзменитьПризнакНДС, фаза 1 — паттерн R2."""

    receipt_uuid: str
    vat_included_in_price: bool
    discrepancy_id: str | None = None


class CreateAdjustmentPlanIn(Contract):
    """СоздатьКорректировку, фаза 1."""

    receipt_uuid: str
    reason: str
    discrepancy_id: str | None = None


class MarkReviewedIn(Contract):
    """ПометитьПроверено — единственный инструмент записи без фазы плана.

    Ничего в учётных данных не меняет, поэтому применяется сразу, но всё равно
    пишется в журнал.
    """

    discrepancy_id: str
    comment: str


class MarkReviewedOut(Contract):
    discrepancy_id: str
    reviewed: bool


class ItemSuggestion(Contract):
    """Подбор номенклатуры для строки ЭСФ (ТЗ п.5.2.1)."""

    item_uuid: str | None = Field(default=None, description="None — предлагается создать новую")
    item_name: str
    confidence: Confidence
    source: str = Field(
        description="Откуда подбор: соответствия / история / точное / нечёткое / новая",
    )
    alternatives: list[str] = Field(
        default_factory=list,
        description="До 3 альтернатив при нечётком совпадении",
    )


class DraftLine(Contract):
    """Строка черновика поступления, создаваемого по ЭСФ."""

    esf_line_number: int
    item: ItemSuggestion
    uom: str | None
    quantity: Decimal
    price: Decimal
    amount_net: Money
    vat_rate: Decimal
    amount_vat: Money
    account: str | None = Field(default=None, description="Счёт учёта по настройкам типовой")
    account_confidence: Confidence | None = None


class ReceiptDraft(Contract):
    """Черновик поступления по ЭСФ до создания в базе."""

    esf: DocumentRef
    counterparty: CounterpartyBrief
    contract_name: str | None
    contract_confidence: Confidence | None
    warehouse: str | None
    date: dt.date
    lines: list[DraftLine]
    uncertain_lines: int = Field(description="Число строк с уверенностью ниже высокой")
    amount_net: Money
    amount_vat: Money
    amount_gross: Money


class CreateReceiptFromEsfPlanIn(Contract):
    """СоздатьПоступлениеПоЭСФ, фаза 1."""

    esf_uuid: str
    discrepancy_id: str | None = None


class CreateReceiptFromEsfPlanOut(Contract):
    plan_id: str
    draft: ReceiptDraft
    blocked: bool = False
    block_reason: str | None = None


class CreateReceiptsBulkPlanIn(Contract):
    """СоздатьПоступленияМассово, фаза 1 — по списку ЭСФ без поступлений (D03)."""

    esf_uuids: list[str] = Field(min_length=1, max_length=200)


class CreateReceiptsBulkPlanOut(Contract):
    plan_id: str
    drafts: list[ReceiptDraft]
    total_uncertain_lines: int
    blocked: bool = False
    block_reason: str | None = None


# ---------------------------------------------------------------------------
# 5.3. Служебные
# ---------------------------------------------------------------------------


class GetSettingsIn(Contract):
    """ПолучитьНастройки — параметров нет."""


class AgentSettings(Contract):
    tolerances: Tolerances
    masking_enabled: bool
    period_tail_days: int = Field(
        default=20,
        description="«Хвост» периода для отбора ЭСФ — срок выписки (ТЗ п.4.1)",
    )
    include_extra_costs: bool = Field(
        default=False,
        description="Учитывать поступления доп. расходов",
    )
    include_expense_reports: bool = Field(
        default=False,
        description="Учитывать авансовые отчёты",
    )


class GetSettingsOut(Contract):
    settings: AgentSettings


class OpenObjectIn(Contract):
    uuid: str
    kind: DocumentKind


class OpenObjectOut(Contract):
    navigation_link: str
    presentation: str
