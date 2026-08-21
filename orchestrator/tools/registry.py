"""Реестр инструментов: имя, контракты, признак записи.

Имя инструмента одно с обеих сторон (`reconcile_period`) — расширение публикует
их как `POST /hs/bota/v1/tools/{tool_name}` (A.0.1), поэтому переводить имена
между 1С и моделью не нужно.

Инструментов `apply_*` здесь нет и быть не может. Расширение отвергает их вызов
по HTTP с `ACCESS_DENIED` (A.9), а отсутствие в реестре означает, что модель
даже не знает об их существовании. Два независимых рубежа вместо одного.

Порядок инструментов — часть кэш-префикса запроса к модели. Менять его без
причины нельзя: это инвалидирует кэш всех баз.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from orchestrator.tools import contracts as c
from orchestrator.tools.common import Contract


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    """Описание для модели: что инструмент делает и когда его звать."""

    input_model: type[Contract]
    output_model: type[Contract]

    writes: bool = False
    """True для фазы 1 — инструмент возвращает план, но ничего не меняет."""

    def input_schema(self) -> dict[str, Any]:
        """JSON Schema параметров для Claude API.

        by_alias — чтобы модель видела имена из Приложения А (`from`, `to`),
        а не внутренние `date_from` / `date_to`.
        """
        schema = self.input_model.model_json_schema(by_alias=True)
        schema.setdefault("properties", {})
        return schema

    def to_anthropic(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema(),
        }


TOOLS: tuple[ToolSpec, ...] = (
    # --- Чтение ---
    ToolSpec(
        name="get_context",
        description=(
            "Контекст базы: организации с БИН и датой запрета изменения, текущий "
            "период НДС, релиз конфигурации и расширения, действующие допуски, "
            "права пользователя (read/apply/create). Вызывай первым в каждом "
            "диалоге — без него неизвестно, по какой организации и за какой период "
            "работать и что вообще позволено предлагать."
        ),
        input_model=c.GetContextIn,
        output_model=c.GetContextOut,
    ),
    ToolSpec(
        name="reconcile_period",
        description=(
            "Сверяет поступления и полученные ЭСФ за период. Возвращает calc_id, "
            "состав расчёта, сводку по кодам D01–D16, накопленные округления по "
            "паттернам R1–R6, сверку НДС с учётом и до 10 самых критичных "
            "расхождений. Тяжёлая операция (до 90 с), результат кэшируется — "
            "не вызывай повторно за тот же период без force_recalc. "
            "Полученный calc_id обязателен для list_discrepancies."
        ),
        input_model=c.ReconcilePeriodIn,
        output_model=c.ReconcilePeriodOut,
    ),
    ToolSpec(
        name="list_discrepancies",
        description=(
            "Постраничный список расхождений внутри расчёта calc_id, с фильтрами "
            "по кодам, критичности, контрагенту, паттерну округления и статусу. "
            "Краткие карточки с готовым резюме short — используй его как есть. "
            "Нужен для обзора; для разбора одного случая бери get_discrepancy."
        ),
        input_model=c.ListDiscrepanciesIn,
        output_model=c.ListDiscrepanciesOut,
    ),
    ToolSpec(
        name="get_discrepancy",
        description=(
            "Полная карточка расхождения: пара документов, тип связи, сравнение "
            "реквизитов, построчное сопоставление с уровнем и уверенностью, "
            "diagnosis с определённым паттерном и объяснением, а также перечень "
            "предлагаемых действий. Объяснение посчитано движком — пересказывай "
            "его, а не сочиняй своё."
        ),
        input_model=c.GetDiscrepancyIn,
        output_model=c.GetDiscrepancyOut,
    ),
    ToolSpec(
        name="get_document",
        description=(
            "Шапка, строки и связанные документы одного объекта — поступления или "
            "ЭСФ. Нужен, когда спрашивают про конкретный документ или когда "
            "карточки расхождения не хватает."
        ),
        input_model=c.GetDocumentIn,
        output_model=c.GetDocumentOut,
    ),
    ToolSpec(
        name="find_esf_candidates",
        description=(
            "Для поступления без связи ищет ЭСФ-кандидатов по БИН, сумме, дате и "
            "похожести строк, с оценкой score и перечнем причин совпадения. "
            "Это предположение: связь не устанавливается и требует подтверждения "
            "бухгалтера. Проверяй already_linked_to — кандидат может быть уже занят."
        ),
        input_model=c.FindEsfCandidatesIn,
        output_model=c.FindEsfCandidatesOut,
    ),
    ToolSpec(
        name="find_receipt_candidates",
        description="Зеркало find_esf_candidates: для ЭСФ без связи ищет поступления.",
        input_model=c.FindReceiptCandidatesIn,
        output_model=c.FindReceiptCandidatesOut,
    ),
    ToolSpec(
        name="get_counterparty",
        description=(
            "Карточка контрагента: признак плательщика НДС на указанную дату, "
            "свидетельство, даты постановки и снятия с учёта, основной договор, "
            "число накопленных соответствий номенклатуры. Обязателен для проверки "
            "кода D15 — снятие с учёта задним числом лишает права на зачёт."
        ),
        input_model=c.GetCounterpartyIn,
        output_model=c.GetCounterpartyOut,
    ),
    ToolSpec(
        name="get_vat_turnover",
        description=(
            "Обороты НДС за период в разрезе ставок, оборот по счёту 1420 и сумма "
            "по доставленным ЭСФ с готовой разницей между ними. Этим инструментом "
            "отвечают на вопрос «почему НДС к зачёту отличается от суммы по ЭСФ»."
        ),
        input_model=c.GetVatTurnoverIn,
        output_model=c.GetVatTurnoverOut,
    ),
    ToolSpec(
        name="get_journal",
        description=(
            "Журнал применённых изменений и созданных агентом документов за период: "
            "кто, когда, по какому плану и чем это можно откатить. Нужен на вопросы "
            "«что агент уже сделал» и «как отменить»."
        ),
        input_model=c.GetJournalIn,
        output_model=c.GetJournalOut,
    ),
    # --- Запись, фаза 1: только планы ---
    ToolSpec(
        name="plan_set_link",
        description=(
            "Готовит план установки связи поступление↔ЭСФ и проверяет, что период "
            "открыт, ЭСФ не связана с другим документом и контрагент тот же. "
            "Ничего не меняет: применяет план бухгалтер кнопкой в 1С."
        ),
        input_model=c.SetLinkPlanIn,
        output_model=c.SetLinkPlanOut,
        writes=True,
    ),
    ToolSpec(
        name="plan_adjust_lines",
        description=(
            "Готовит план правки строк поступления. По умолчанию эталон — ЭСФ "
            "(strategy=esf_as_master); strategy=custom требует явных значений в "
            "lines. Возвращает diff «было/станет», итоги после правки и "
            "затрагиваемые проводки. Основной инструмент для паттернов R1, R3, R4. "
            "Ничего не меняет."
        ),
        input_model=c.AdjustLinesPlanIn,
        output_model=c.AdjustLinesPlanOut,
        writes=True,
    ),
    ToolSpec(
        name="plan_set_vat_mode",
        description=(
            "Готовит план смены флага «Сумма включает НДС» с пересчётом всех строк "
            "— паттерн R2, НДС «в том числе» против «сверху». Ничего не меняет."
        ),
        input_model=c.SetVatModePlanIn,
        output_model=c.SetVatModePlanOut,
        writes=True,
    ),
    ToolSpec(
        name="plan_create_correction",
        description=(
            "Готовит проект документа «Корректировка поступления» со строками "
            "«было/стало». Нужен, когда период закрыт и править исходный документ "
            "нельзя. Документ не создаётся и не проводится."
        ),
        input_model=c.CreateCorrectionPlanIn,
        output_model=c.CreateCorrectionPlanOut,
        writes=True,
    ),
    ToolSpec(
        name="plan_create_receipt_from_esf",
        description=(
            "Готовит черновик поступления по ЭСФ: контрагент, договор, склад, "
            "строки с подобранной номенклатурой и счетами учёта. У каждой позиции "
            "указана уверенность подбора и альтернативы. Пока есть строки с "
            "needs_attention, can_apply = false — перечисли такие строки "
            "пользователю и предложи выбрать номенклатуру; повторный вызов с "
            "overrides снимает блокировку. Документ не создаётся."
        ),
        input_model=c.CreateReceiptFromEsfPlanIn,
        output_model=c.CreateReceiptFromEsfPlanOut,
        writes=True,
    ),
    ToolSpec(
        name="plan_create_receipts_bulk",
        description=(
            "Готовит пачку черновиков по списку ЭСФ без поступлений (код D03) и "
            "показывает, в каких из них есть строки, требующие внимания. "
            "Документы не создаются."
        ),
        input_model=c.CreateReceiptsBulkPlanIn,
        output_model=c.CreateReceiptsBulkPlanOut,
        writes=True,
    ),
    ToolSpec(
        name="mark_reviewed",
        description=(
            "Помечает расхождение рассмотренным с комментарием. Учётные данные не "
            "меняет, но пишется в журнал. Ставь пометку только по прямой просьбе "
            "пользователя."
        ),
        input_model=c.MarkReviewedIn,
        output_model=c.MarkReviewedOut,
        writes=True,
    ),
    # --- Служебные ---
    ToolSpec(
        name="get_settings",
        description=(
            "Действующие настройки сверки: допуски округления, хвост периода для "
            "отбора ЭСФ, допуск по дате при поиске кандидатов."
        ),
        input_model=c.GetSettingsIn,
        output_model=c.GetSettingsOut,
    ),
    ToolSpec(
        name="open_object",
        description=(
            "Навигационная ссылка на объект 1С. Как правило не нужен: ссылки уже "
            "приходят в составе документов и карточек."
        ),
        input_model=c.OpenObjectIn,
        output_model=c.OpenObjectOut,
    ),
)


BY_NAME: dict[str, ToolSpec] = {spec.name: spec for spec in TOOLS}


def anthropic_tools(specs: Sequence[ToolSpec] = TOOLS) -> list[dict[str, Any]]:
    """Определения инструментов в формате Claude API."""
    return [spec.to_anthropic() for spec in specs]


def readable_tools() -> tuple[ToolSpec, ...]:
    """Только чтение — для режима «агент не предлагает изменений»."""
    return tuple(spec for spec in TOOLS if not spec.writes)
