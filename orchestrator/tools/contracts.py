"""Контракты инструментов по Приложению А v0.1 (разделы A.1–A.10).

Один класс на вход и один на выход каждого инструмента. Из этих же моделей
генерируется JSON Schema для модели — контракт с 1С и контракт с LLM описаны
один раз и не могут разъехаться.

Здесь только фаза 1 (`plan_*`). Инструменты `apply_*` существуют в расширении,
но агенту не показываются: расширение проверяет, что вызов пришёл от
интерактивного пользователя 1С, а вызов по HTTP отвергает с `ACCESS_DENIED`
(A.9). Отсутствие их в реестре — второй рубеж той же защиты.

Поля `from` и `to` из Приложения объявлены как `date_from` / `date_to` с алиасом:
`from` — ключевое слово Python. В JSON уходят исходные имена.
"""

from __future__ import annotations

import datetime as dt

from pydantic import Field

from orchestrator.tools.common import (
    Bin,
    Contract,
    CounterpartyBrief,
    DocumentLine,
    EsfDocument,
    Money,
    Permissions,
    Qty,
    Rate,
    ReceiptDocument,
    Ref,
    Settings,
    Totals,
)
from orchestrator.tools.enums import (
    AdjustStrategy,
    Confidence,
    DiscrepancyCode,
    DiscrepancyStatus,
    ItemSource,
    LineMatch,
    LinkKind,
    RoundingPattern,
    Severity,
)

# ---------------------------------------------------------------------------
# A.1. get_context
# ---------------------------------------------------------------------------


class GetContextIn(Contract):
    """Параметров нет."""


class BaseInfo(Contract):
    name: str
    config: str = Field(description="Имя конфигурации")
    config_version: str
    platform: str
    currency: str = Field(default="KZT")
    extension_version: str


class OrganizationInfo(Contract):
    ref: Ref
    bin: Bin
    vat_payer: bool
    forbid_date: dt.date | None = Field(
        default=None,
        description="Дата запрета изменения; левее неё запись невозможна",
    )


class PeriodInfo(Contract):
    date_from: dt.date = Field(alias="from")
    date_to: dt.date = Field(alias="to")
    kind: str = Field(description="quarter | month | custom")


class GetContextOut(Contract):
    base: BaseInfo
    organizations: list[OrganizationInfo]
    current_period: PeriodInfo
    settings: Settings
    permissions: Permissions


# ---------------------------------------------------------------------------
# A.2. reconcile_period
# ---------------------------------------------------------------------------


class ReconcilePeriodIn(Contract):
    organization: str = Field(description="UUID организации")
    date_from: dt.date = Field(alias="from")
    date_to: dt.date = Field(alias="to")
    counterparty: str | None = Field(default=None, description="UUID контрагента")
    force_recalc: bool = Field(
        default=False,
        description="Пересчитать, не беря результат из кэша",
    )


class Scope(Contract):
    """Что попало в расчёт (A.2)."""

    receipts_total: int
    receipts_unposted: int
    esf_total: int
    pairs_linked: int
    pairs_suggested: int
    receipts_without_esf: int
    esf_without_receipt: int


class CodeSummary(Contract):
    code: DiscrepancyCode
    severity: Severity
    count: int
    amount_vat: Money = Field(description="Влияние на НДС к зачёту по этому коду")


class PatternSummary(Contract):
    pattern: RoundingPattern
    count: int
    diff_vat: Money


class RoundingSummary(Contract):
    """Накопленные округления — то, что объясняет хвост в декларации (ТЗ п.4.4)."""

    total_diff_vat: Money
    total_diff_net: Money
    by_pattern: list[PatternSummary]


class VatTotals(Contract):
    receipts_vat: Money
    esf_vat: Money
    diff: Money
    ledger_vat_1420: Money = Field(description="Оборот по счёту 1420 в учёте")


class ReconcilePeriodOut(Contract):
    calc_id: str = Field(description="Идентификатор расчёта; нужен для list_discrepancies")
    calculated_at: dt.datetime
    from_cache: bool
    scope: Scope
    summary_by_code: list[CodeSummary]
    rounding: RoundingSummary
    vat_totals: VatTotals
    top_discrepancies: list[str] = Field(
        description="До 10 id самых критичных — чтобы сразу перейти к деталям",
    )


# ---------------------------------------------------------------------------
# A.3. list_discrepancies
# ---------------------------------------------------------------------------


class ListDiscrepanciesIn(Contract):
    calc_id: str
    codes: list[DiscrepancyCode] | None = None
    severity: list[Severity] | None = None
    counterparty: str | None = None
    pattern: list[RoundingPattern] | None = None
    status: list[DiscrepancyStatus] | None = None
    sort: str | None = Field(
        default=None,
        description="amount_desc | date_asc | severity",
    )
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)


class ReceiptBrief(Contract):
    ref: Ref
    date: dt.date
    number: str
    total: Money
    vat: Money


class EsfBrief(Contract):
    ref: Ref
    date: dt.date
    reg_number: str
    status: str
    total: Money
    vat: Money


class DiffTotals(Contract):
    net: Money
    vat: Money
    total: Money


class DiscrepancyBrief(Contract):
    id: str
    code: DiscrepancyCode
    severity: Severity
    status: DiscrepancyStatus
    pattern: RoundingPattern | None = None
    counterparty: CounterpartyBrief | None = None
    receipt: ReceiptBrief | None = None
    esf: EsfBrief | None = None
    diff: DiffTotals
    short: str = Field(
        description="Резюме, сформированное движком 1С. Можно использовать как есть",
    )


class ListDiscrepanciesOut(Contract):
    items: list[DiscrepancyBrief]
    page: int
    page_size: int
    total: int
    has_more: bool


# ---------------------------------------------------------------------------
# A.4. get_discrepancy
# ---------------------------------------------------------------------------


class GetDiscrepancyIn(Contract):
    id: str


class ReceiptSide(Contract):
    ref: Ref
    date: dt.date
    number: str
    posted: bool
    vat_included_in_price: bool
    currency: str = "KZT"
    totals: Totals


class EsfSide(Contract):
    ref: Ref
    date_issue: dt.date
    date_turnover: dt.date
    reg_number: str
    status: str
    kind: str
    replaces: Ref | None = None
    totals: Totals


class Pair(Contract):
    receipt: ReceiptSide | None = None
    esf: EsfSide | None = None
    link: LinkKind


class HeaderDiff(Contract):
    """Сравнение одного реквизита пары (A.4)."""

    field: str = Field(description="net | vat | total | rate | date_turnover | …")
    receipt: str | None
    esf: str | None
    diff: str | int | None
    within_tolerance: bool


class LineDiff(Contract):
    qty: Qty | None = None
    price: Money | None = None
    net: Money | None = None
    vat: Money | None = None
    total: Money | None = None


class LineComparison(Contract):
    match: LineMatch
    match_level: int | None = Field(default=None, ge=1, le=5)
    confidence: Confidence | None = None
    receipt_line: DocumentLine | None = None
    esf_line: DocumentLine | None = None
    diff: LineDiff | None = None
    pattern: RoundingPattern | None = None


class Diagnosis(Contract):
    """Вывод движка о причине. Агент пересказывает его, а не сочиняет свой."""

    pattern: RoundingPattern | None = None
    explanation: str
    confidence: Confidence


class SuggestedAction(Contract):
    action: str = Field(description="Имя инструмента фазы 1 без префикса plan_")
    label: str = Field(description="Текст кнопки для бухгалтера")
    risk: str = Field(description="none | low | medium | high")


class GetDiscrepancyOut(Contract):
    id: str
    code: DiscrepancyCode
    severity: Severity
    status: DiscrepancyStatus
    reviewed: str | None = Field(default=None, description="Комментарий при пометке")
    pair: Pair
    header_diff: list[HeaderDiff] = Field(default_factory=list)
    lines: list[LineComparison] = Field(default_factory=list)
    diagnosis: Diagnosis
    suggested_actions: list[SuggestedAction] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# A.5. get_document
# ---------------------------------------------------------------------------


class GetDocumentIn(Contract):
    uuid: str
    type: str | None = Field(
        default=None,
        description="Тип объекта; если не указан — определится по ссылке",
    )


class DocumentLink(Contract):
    ref: Ref
    kind: str = Field(description="Тип связи: esf | receipt | correction | basis")


class GetDocumentOut(Contract):
    receipt: ReceiptDocument | None = None
    esf: EsfDocument | None = None
    links: list[DocumentLink] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# A.6. find_esf_candidates / find_receipt_candidates
# ---------------------------------------------------------------------------


class FindEsfCandidatesIn(Contract):
    receipt_uuid: str
    days: int = Field(default=10, ge=0, le=120)
    amount_tolerance: Money = Field(default="5.00")
    limit: int = Field(default=5, ge=1, le=20)


class EsfCandidate(Contract):
    esf: EsfBrief
    score: float = Field(ge=0, le=1, description="Оценка совпадения по формуле движка")
    reasons: list[str] = Field(description="same_bin | total_equal | date_diff_1 | …")
    already_linked_to: Ref | None = None


class FindEsfCandidatesOut(Contract):
    items: list[EsfCandidate]


class FindReceiptCandidatesIn(Contract):
    esf_uuid: str
    days: int = Field(default=10, ge=0, le=120)
    amount_tolerance: Money = Field(default="5.00")
    limit: int = Field(default=5, ge=1, le=20)


class ReceiptCandidate(Contract):
    receipt: ReceiptBrief
    score: float = Field(ge=0, le=1)
    reasons: list[str]
    already_linked_to: Ref | None = None


class FindReceiptCandidatesOut(Contract):
    items: list[ReceiptCandidate]


# ---------------------------------------------------------------------------
# A.7. get_counterparty
# ---------------------------------------------------------------------------


class GetCounterpartyIn(Contract):
    bin: Bin | None = None
    uuid: str | None = None
    on_date: dt.date | None = Field(
        default=None,
        description="Дата, на которую проверяется статус плательщика НДС",
    )


class VatCertificate(Contract):
    series: str | None = None
    number: str | None = None
    date_from: dt.date | None = None
    date_to: dt.date | None = Field(
        default=None,
        description="Дата снятия с учёта — основание для кода D15",
    )


class GetCounterpartyOut(Contract):
    ref: Ref
    bin: Bin | None = None
    name: str
    vat_payer: bool
    vat_certificate: VatCertificate | None = None
    vat_status_on_date: str = Field(
        description="payer | not_payer | deregistered | unknown",
    )
    main_contract: Ref | None = None
    contracts_count: int = 0
    receipts_in_period: int = 0
    esf_in_period: int = 0
    item_mapping_count: int = Field(
        default=0,
        description="Сколько соответствий номенклатуры уже накоплено по контрагенту",
    )


# ---------------------------------------------------------------------------
# A.8. get_vat_turnover
# ---------------------------------------------------------------------------


class GetVatTurnoverIn(Contract):
    organization: str
    date_from: dt.date = Field(alias="from")
    date_to: dt.date = Field(alias="to")


class VatByRate(Contract):
    rate: Rate
    net: Money
    vat: Money
    account: str | None = None


class GetVatTurnoverOut(Contract):
    by_rate: list[VatByRate]
    register_vat_offset: Money
    ledger_1420_debit: Money
    esf_sum_delivered: Money
    diff_ledger_vs_esf: Money = Field(
        description="Расхождение зачётного НДС между учётом и ЭСФ",
    )


# ---------------------------------------------------------------------------
# A.9. Двухфазные инструменты — только фаза 1
# ---------------------------------------------------------------------------


class Change(Contract):
    """Одно изменение в плане: что, где, было → станет (A.9.1, A.9.2)."""

    object: Ref | None = None
    line: int | None = Field(default=None, description="Номер строки, если правится строка")
    field: str
    value_from: str | None = Field(alias="from")
    value_to: str | None = Field(alias="to")


class Check(Contract):
    """Проверка выполнимости плана (A.9.1): период открыт, ЭСФ свободна, права есть."""

    check: str
    ok: bool
    message: str | None = None


class Posting(Contract):
    account_dt: str
    account_kt: str
    value_from: Money | None = Field(default=None, alias="from")
    value_to: Money | None = Field(default=None, alias="to")


class PlanBase(Contract):
    """Общая часть результата любого `plan_*` (A.9).

    План ничего не меняет и живёт 30 минут. Применяет его пользователь кнопкой
    в 1С; агент передаёт `plan_id` в ответ, но вызвать `apply_*` не может.
    """

    plan_id: str
    action: str
    expires_at: dt.datetime
    checks: list[Check] = Field(default_factory=list)
    can_apply: bool
    block_reason: str | None = None


class SetLinkPlanIn(Contract):
    receipt_uuid: str
    esf_uuid: str


class SetLinkPlanOut(PlanBase):
    changes: list[Change] = Field(default_factory=list)


class AdjustLineOverride(Contract):
    """Значения, которые бухгалтер задаёт вручную при стратегии custom."""

    n: int
    price: Money | None = None
    qty: Qty | None = None
    vat: Money | None = None


class AdjustLinesPlanIn(Contract):
    discrepancy_id: str
    strategy: AdjustStrategy = Field(default=AdjustStrategy.ESF_AS_MASTER)
    lines: list[AdjustLineOverride] = Field(
        default_factory=list,
        description="Обязательно только при strategy=custom",
    )


class AdjustLinesPlanOut(PlanBase):
    document: Ref
    changes: list[Change] = Field(default_factory=list)
    totals_after: Totals
    postings_affected: list[Posting] = Field(default_factory=list)
    will_repost: bool = False


class SetVatModePlanIn(Contract):
    receipt_uuid: str
    vat_included: bool


class SetVatModePlanOut(PlanBase):
    document: Ref
    changes: list[Change] = Field(default_factory=list)
    totals_after: Totals
    postings_affected: list[Posting] = Field(default_factory=list)
    will_repost: bool = False


class CreateCorrectionPlanIn(Contract):
    discrepancy_id: str
    reason: str


class CorrectionLine(Contract):
    n: int
    name: str
    before: Totals
    after: Totals


class CreateCorrectionPlanOut(PlanBase):
    basis: Ref = Field(description="Корректируемое поступление")
    lines: list[CorrectionLine] = Field(default_factory=list)
    totals_after: Totals
    will_post: bool = Field(default=False, description="Созданный документ не проводится")


class ItemOverride(Contract):
    esf_line: int
    item: str = Field(description="UUID номенклатуры, выбранной бухгалтером")


class CreateReceiptFromEsfPlanIn(Contract):
    esf_uuid: str
    warehouse: str | None = None
    contract: str | None = None
    overrides: list[ItemOverride] = Field(
        default_factory=list,
        description="Ручной выбор номенклатуры для строк, где движок не уверен",
    )


class SuggestedRef(Contract):
    """Подобранный объект с оценкой уверенности (A.9.5)."""

    ref: Ref | None = None
    confidence: Confidence
    alternatives: list[Ref] = Field(default_factory=list)


class SuggestedItem(Contract):
    ref: Ref | None = None
    confidence: Confidence
    source: ItemSource
    alternatives: list[Ref] = Field(default_factory=list)


class NewItemSuggestion(Contract):
    """Предложение завести новую номенклатуру, если ничего не подошло."""

    name: str
    unit: str | None = None
    vat_rate: Rate


class DraftAccount(Contract):
    code: str
    confidence: Confidence


class DraftLine(Contract):
    n: int
    esf_name: str = Field(description="Наименование как в ЭСФ")
    item: SuggestedItem
    suggest_new_item: NewItemSuggestion | None = None
    unit: str | None = None
    qty: Qty
    price: Money
    vat_rate: Rate
    account: DraftAccount | None = None
    needs_attention: bool = Field(
        description="Строку нужно проверить руками; блокирует применение",
    )


class ReceiptDraft(Contract):
    organization: Ref
    counterparty: SuggestedRef
    contract: SuggestedRef
    warehouse: SuggestedRef
    date: dt.date
    vat_included: bool
    lines: list[DraftLine]
    totals: Totals
    attention_count: int


class CreateReceiptFromEsfPlanOut(PlanBase):
    draft: ReceiptDraft


class CreateReceiptsBulkPlanIn(Contract):
    esf_uuids: list[str] = Field(min_length=1, max_length=200)
    warehouse: str | None = None


class BulkDraftItem(Contract):
    esf: Ref
    plan_id: str
    attention_count: int
    can_apply: bool
    block_reason: str | None = None


class CreateReceiptsBulkPlanOut(Contract):
    items: list[BulkDraftItem]
    total_attention: int


class MarkReviewedIn(Contract):
    """Однофазный инструмент: учётные данные не меняет, но пишется в журнал (A.9.7)."""

    discrepancy_id: str
    comment: str
    status: DiscrepancyStatus = Field(default=DiscrepancyStatus.REVIEWED)


class MarkReviewedOut(Contract):
    discrepancy_id: str
    status: DiscrepancyStatus


# ---------------------------------------------------------------------------
# A.10. Служебные
# ---------------------------------------------------------------------------


class GetSettingsIn(Contract):
    """Параметров нет."""


class GetSettingsOut(Contract):
    settings: Settings


class OpenObjectIn(Contract):
    uuid: str
    type: str | None = None


class OpenObjectOut(Contract):
    nav: str


class GetJournalIn(Contract):
    date_from: dt.date = Field(alias="from")
    date_to: dt.date = Field(alias="to")
    user: str | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)


class JournalEntry(Contract):
    """Применённое изменение или созданный документ (A.10)."""

    plan_id: str
    session_id: str | None = None
    action: str
    applied_at: dt.datetime
    user: str
    objects: list[Ref] = Field(default_factory=list)
    rollback_ref: Ref | None = Field(
        default=None,
        description="Чем откатить: документ сторно или предыдущая версия",
    )


class GetJournalOut(Contract):
    items: list[JournalEntry]
    page: int
    page_size: int
    total: int
    has_more: bool
