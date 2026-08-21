"""Реестр инструментов: одно место, где связаны имя для LLM, метод 1С и контракты.

Три вещи, которые здесь важны:

1. **Имена латиницей.** Имя tool в Claude API ограничено `^[a-zA-Z0-9_-]{1,64}$`,
   поэтому русские имена экспортных функций из ТЗ (`ПолучитьКонтекст`) в модель
   уйти не могут. Реестр держит пару «имя для LLM ↔ имя метода в 1С»; наружу в
   HTTP-сервис расширения уходит русское имя, в LLM — латинское.

2. **Фаза 2 отсутствует в реестре.** Модель физически не может вызвать применение
   изменений: таких инструментов ей не показывают (ТЗ п.5.2, правило 1). Применение
   идёт отдельным маршрутом, инициированным пользователем из 1С.

3. **JSON Schema генерируется из Pydantic**, а не пишется руками, — расхождение
   между тем, что принимает 1С, и тем, что модель считает допустимым, невозможно.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from orchestrator.tools import contracts as c
from orchestrator.tools.common import Contract


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Описание одного инструмента."""

    name: str
    """Имя для LLM, латиницей."""

    onec_method: str
    """Имя экспортной функции расширения 1С (как в ТЗ раздел 5)."""

    description: str
    """Описание для модели. Пишется от лица «что этот инструмент делает и когда его звать»."""

    input_model: type[Contract]
    output_model: type[Contract]

    writes: bool = False
    """True для инструментов фазы 1 — они возвращают план, но ничего не меняют."""

    def input_schema(self) -> dict[str, Any]:
        """JSON Schema параметров для Claude API."""
        schema = self.input_model.model_json_schema()
        # Claude ожидает объектную схему; у моделей без полей Pydantic всё равно
        # отдаёт type: object, но properties может отсутствовать.
        schema.setdefault("properties", {})
        return schema

    def to_anthropic(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema(),
        }


# ---------------------------------------------------------------------------
# Порядок в этом кортеже — часть кэш-префикса запроса к LLM (см. shared/prompt-caching).
# Менять порядок без причины нельзя: это инвалидирует кэш всех тенантов.
# ---------------------------------------------------------------------------

TOOLS: tuple[ToolSpec, ...] = (
    # --- 5.1. Чтение ---
    ToolSpec(
        name="get_context",
        onec_method="ПолучитьКонтекст",
        description=(
            "Контекст базы 1С: список организаций, текущий период НДС, дата запрета "
            "изменения, релиз конфигурации, валюта учёта, действующие допуски. "
            "Вызывай первым в каждом диалоге — без него неизвестно, по какой "
            "организации и за какой период работать."
        ),
        input_model=c.GetContextIn,
        output_model=c.GetContextOut,
    ),
    ToolSpec(
        name="reconcile_period",
        onec_method="СверитьПериод",
        description=(
            "Запускает сверку поступлений и полученных ЭСФ за период и возвращает "
            "сводку: сколько пар, сколько расхождений по каждому коду D01–D16, "
            "накопленная разница округлений и список ID расхождений. "
            "Тяжёлая операция (до 90 с), результат кэшируется — не вызывай повторно "
            "за тот же период с теми же параметрами."
        ),
        input_model=c.ReconcilePeriodIn,
        output_model=c.ReconcilePeriodOut,
    ),
    ToolSpec(
        name="list_discrepancies",
        onec_method="ПолучитьСписокРасхождений",
        description=(
            "Постраничный список расхождений с фильтрами по кодам, контрагенту и "
            "критичности. Краткие карточки без построчного сравнения. "
            "Используй, когда нужен обзор, а не разбор конкретного случая."
        ),
        input_model=c.ListDiscrepanciesIn,
        output_model=c.ListDiscrepanciesOut,
    ),
    ToolSpec(
        name="get_discrepancy",
        onec_method="ПолучитьРасхождение",
        description=(
            "Полная карточка одного расхождения: пара документов, сравнение реквизитов, "
            "построчное сопоставление, определённый движком источник копеечной разницы "
            "(R1–R6), вероятная причина и рекомендуемое действие. "
            "Причина и рекомендация посчитаны движком — передавай их пользователю, "
            "не придумывай свои."
        ),
        input_model=c.GetDiscrepancyIn,
        output_model=c.GetDiscrepancyOut,
    ),
    ToolSpec(
        name="get_document",
        onec_method="ПолучитьДокумент",
        description=(
            "Шапка и строки одного документа — поступления или ЭСФ. "
            "Нужен, когда пользователь спрашивает про конкретный документ или когда "
            "карточки расхождения не хватает."
        ),
        input_model=c.GetDocumentIn,
        output_model=c.GetDocumentOut,
    ),
    ToolSpec(
        name="find_esf_candidates",
        onec_method="НайтиКандидатовЭСФ",
        description=(
            "Для поступления без связи ищет ЭСФ-кандидатов по БИН, сумме и дате "
            "с оценкой совпадения. Ответ — предположение, требующее подтверждения "
            "бухгалтера; связь этим инструментом не устанавливается."
        ),
        input_model=c.FindEsfCandidatesIn,
        output_model=c.FindEsfCandidatesOut,
    ),
    ToolSpec(
        name="find_receipt_candidates",
        onec_method="НайтиКандидатовПоступления",
        description=(
            "Для ЭСФ без связи ищет поступления-кандидаты. Зеркало find_esf_candidates."
        ),
        input_model=c.FindReceiptCandidatesIn,
        output_model=c.FindReceiptCandidatesOut,
    ),
    ToolSpec(
        name="get_counterparty",
        onec_method="ПолучитьКарточкуКонтрагента",
        description=(
            "Карточка контрагента по БИН: реквизиты, признак плательщика НДС на "
            "указанную дату, даты постановки и снятия с учёта, свидетельство. "
            "Обязателен для проверки кода D15 — снятие с учёта задним числом "
            "лишает права на зачёт."
        ),
        input_model=c.GetCounterpartyIn,
        output_model=c.GetCounterpartyOut,
    ),
    ToolSpec(
        name="get_vat_turnovers",
        onec_method="ПолучитьОборотыНДС",
        description=(
            "Обороты по зачётному НДС в разрезе ставок и итог по ЭСФ за период, "
            "с готовой разницей между ними. Это тот инструмент, которым отвечают на "
            "вопрос «почему НДС к зачёту отличается от суммы по ЭСФ»."
        ),
        input_model=c.GetVatTurnoversIn,
        output_model=c.GetVatTurnoversOut,
    ),
    ToolSpec(
        name="get_change_history",
        onec_method="ПолучитьИсториюИзменений",
        description=(
            "Версии документа, если в базе включено версионирование. "
            "Помогает понять, кто и когда изменил сумму. Если версионирование "
            "выключено, вернётся пустой список — так и скажи пользователю."
        ),
        input_model=c.GetChangeHistoryIn,
        output_model=c.GetChangeHistoryOut,
    ),
    # --- 5.2. Запись, фаза 1: только планы ---
    ToolSpec(
        name="plan_set_link",
        onec_method="УстановитьСвязь_План",
        description=(
            "Готовит план установки связи поступление↔ЭСФ. Ничего не меняет: "
            "возвращает план, который пользователь применяет кнопкой в 1С."
        ),
        input_model=c.SetLinkPlanIn,
        output_model=c.ChangePlan,
        writes=True,
    ),
    ToolSpec(
        name="plan_adjust_lines",
        onec_method="СкорректироватьСтроки_План",
        description=(
            "Готовит план правки строк поступления по эталону ЭСФ: показывает diff "
            "«было / станет» и затрагиваемые проводки. Основной инструмент для "
            "копеечных расхождений R1, R3, R4. Ничего не меняет."
        ),
        input_model=c.AdjustLinesPlanIn,
        output_model=c.ChangePlan,
        writes=True,
    ),
    ToolSpec(
        name="plan_change_vat_flag",
        onec_method="ИзменитьПризнакНДС_План",
        description=(
            "Готовит план смены флага «Сумма включает НДС» с пересчётом сумм — "
            "паттерн R2 (НДС «в том числе» против «сверху»). Ничего не меняет."
        ),
        input_model=c.ChangeVatFlagPlanIn,
        output_model=c.ChangePlan,
        writes=True,
    ),
    ToolSpec(
        name="plan_create_adjustment",
        onec_method="СоздатьКорректировку_План",
        description=(
            "Готовит проект документа «Корректировка поступления». "
            "Применяется, когда период уже закрыт и править исходный документ нельзя. "
            "Ничего не меняет."
        ),
        input_model=c.CreateAdjustmentPlanIn,
        output_model=c.ChangePlan,
        writes=True,
    ),
    ToolSpec(
        name="plan_create_receipt_from_esf",
        onec_method="СоздатьПоступлениеПоЭСФ_План",
        description=(
            "Готовит черновик поступления по ЭСФ: контрагент, договор, склад, строки "
            "с подобранной номенклатурой и счетами учёта. По каждой строке указана "
            "уверенность подбора и альтернативы. Обязательно перечисли пользователю "
            "строки с уверенностью ниже высокой — именно их придётся проверить руками. "
            "Документ не создаётся."
        ),
        input_model=c.CreateReceiptFromEsfPlanIn,
        output_model=c.CreateReceiptFromEsfPlanOut,
        writes=True,
    ),
    ToolSpec(
        name="plan_create_receipts_bulk",
        onec_method="СоздатьПоступленияМассово_План",
        description=(
            "Готовит пачку черновиков поступлений по списку ЭСФ без поступлений (код D03). "
            "Возвращает сводную таблицу с числом неуверенных строк в каждом черновике. "
            "Документы не создаются."
        ),
        input_model=c.CreateReceiptsBulkPlanIn,
        output_model=c.CreateReceiptsBulkPlanOut,
        writes=True,
    ),
    ToolSpec(
        name="mark_reviewed",
        onec_method="ПометитьПроверено",
        description=(
            "Помечает расхождение как рассмотренное и принятое, с комментарием. "
            "Учётные данные не меняет, но пишется в журнал. Ставь пометку только "
            "по прямой просьбе пользователя."
        ),
        input_model=c.MarkReviewedIn,
        output_model=c.MarkReviewedOut,
        writes=True,
    ),
    # --- 5.3. Служебные ---
    ToolSpec(
        name="get_settings",
        onec_method="ПолучитьНастройки",
        description=(
            "Текущие настройки агента в базе: допуски округления, режим маскирования, "
            "хвост периода, какие виды документов участвуют в сверке."
        ),
        input_model=c.GetSettingsIn,
        output_model=c.GetSettingsOut,
    ),
    ToolSpec(
        name="open_object",
        onec_method="ОткрытьОбъект",
        description=(
            "Навигационная ссылка на объект 1С для кликабельной ссылки в ответе. "
            "Как правило не нужен: ссылки уже приходят в составе документов и карточек."
        ),
        input_model=c.OpenObjectIn,
        output_model=c.OpenObjectOut,
    ),
)


BY_NAME: dict[str, ToolSpec] = {spec.name: spec for spec in TOOLS}
BY_ONEC_METHOD: dict[str, ToolSpec] = {spec.onec_method: spec for spec in TOOLS}


def anthropic_tools(specs: Sequence[ToolSpec] = TOOLS) -> list[dict[str, Any]]:
    """Определения инструментов в формате Claude API."""
    return [spec.to_anthropic() for spec in specs]


def readable_tools() -> tuple[ToolSpec, ...]:
    """Только инструменты чтения — для режима «агент не предлагает изменений»."""
    return tuple(spec for spec in TOOLS if not spec.writes)
