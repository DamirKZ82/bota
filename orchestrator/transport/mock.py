"""Мок-транспорт: правдоподобные данные вместо реальной базы 1С.

Нужен, чтобы оркестратор, цикл агента и форма ответа отлаживались до того, как
появится расширение. Данные подобраны так, чтобы воспроизводить два реальных
кейса из ТЗ:

* **R1 / D14** — построчное округление НДС против округления от итога. Три строки
  дают в сумме 84,00 ₸ построчно и 84,01 ₸ от итога: ровно тот тиын, который
  накапливается и вылезает в декларации.
* **D03** — ЭСФ без поступления, по которой агент должен предложить черновик.

Заменяется на DirectTransport / PollingTransport без изменений в цикле агента.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from orchestrator.transport.base import OneCTransport, ToolCallError

ORG = {
    "uuid": "org-0001",
    "name": "ТОО «Пилот Аутсорс»",
    "bin": "010203040506",
    "is_vat_payer": True,
    "accounting_currency": "KZT",
}

COUNTERPARTY = {
    "uuid": "cp-0001",
    "name": "ТОО «Снабженец»",
    "bin": "123456789012",
}

COUNTERPARTY_2 = {
    "uuid": "cp-0002",
    "name": "ИП Ахметов",
    "bin": "880101300123",
}

RECEIPT_REF = {
    "kind": "ПоступлениеТМЗиУслуг",
    "uuid": "doc-receipt-145",
    "number": "145",
    "date": "2026-05-14",
    "presentation": "Поступление ТМЗ и услуг № 145 от 14.05.2026",
    "navigation_link": "e1cib/data/Документ.ПоступлениеТМЗиУслуг?ref=doc-receipt-145",
}

ESF_REF = {
    "kind": "ЭСФПолученный",
    "uuid": "doc-esf-8891",
    "number": "8891",
    "date": "2026-05-15",
    "presentation": "ЭСФ (полученный) № 8891 от 15.05.2026",
    "navigation_link": "e1cib/data/Документ.ЭСФПолученный?ref=doc-esf-8891",
}

ESF_NO_RECEIPT_REF = {
    "kind": "ЭСФПолученный",
    "uuid": "doc-esf-9042",
    "number": "9042",
    "date": "2026-06-03",
    "presentation": "ЭСФ (полученный) № 9042 от 03.06.2026",
    "navigation_link": "e1cib/data/Документ.ЭСФПолученный?ref=doc-esf-9042",
}


def _line(
    number: int,
    name: str,
    uom: str,
    qty: str,
    price: str,
    net: str,
    vat: str,
    gross: str,
) -> dict[str, Any]:
    return {
        "number": number,
        "item_name": name,
        "item_uuid": f"item-{number:04d}",
        "uom": uom,
        "quantity": qty,
        "price": price,
        "amount_net": net,
        "vat_rate": "12",
        "amount_vat": vat,
        "amount_gross": gross,
    }


# Построчный НДС: 12,00 + 24,00 + 48,00 = 84,00.
# НДС от итога:  700,06 × 12 % = 84,0072 → 84,01. Разница — тот самый тиын.
RECEIPT_LINES = [
    _line(1, "Бумага А4 «Снегурочка»", "пачка", "7", "14.29", "100.03", "12.00", "112.03"),
    _line(2, "Картридж HP CF217A", "шт", "3", "66.67", "200.01", "24.00", "224.01"),
    _line(3, "Бумага для флипчарта", "рулон", "6", "66.67", "400.02", "48.00", "448.02"),
]

ESF_LINES = [
    _line(1, "Бумага А4 Снегурочка", "пачка", "7", "14.29", "100.03", "12.00", "112.03"),
    _line(2, "Картридж HP CF217A", "шт", "3", "66.67", "200.01", "24.00", "224.01"),
    _line(3, "Бумага для флипчарта", "рулон", "6", "66.67", "400.02", "48.01", "448.03"),
]


class MockTransport(OneCTransport):
    """Отдаёт фиксированные ответы по имени метода 1С."""

    async def call(
        self, tenant_id: str, onec_method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        handler = getattr(self, f"_{onec_method}", None)
        if handler is None:
            raise ToolCallError(f"Мок не умеет «{onec_method}»")
        return handler(params)  # type: ignore[no-any-return]

    # -- чтение -------------------------------------------------------------

    def _ПолучитьКонтекст(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "organizations": [ORG],
            "current_period": {"date_from": "2026-04-01", "date_to": "2026-06-30"},
            "closed_until": "2026-03-31",
            "configuration_version": "3.0.52.14",
            "platform_version": "8.3.24.1548",
            "accounting_currency": "KZT",
            "tolerances": {
                "line_per_unit": "0.01",
                "line_cap": "1.00",
                "document_per_line": "0.01",
                "document_cap": "5.00",
                "period_material": "1.00",
            },
        }

    def _СверитьПериод(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "period": params["period"],
            "organization": ORG,
            "pairs_total": 118,
            "receipts_total": 124,
            "esf_total": 121,
            "by_code": [
                {
                    "code": "D14",
                    "description": "Копеечное расхождение в допуске (см. R1–R6)",
                    "severity": "инфо",
                    "count": 37,
                    "amount_impact": "3.47",
                },
                {
                    "code": "D03",
                    "description": "ЭСФ без поступления",
                    "severity": "высокая",
                    "count": 4,
                    "amount_impact": "184320.00",
                },
                {
                    "code": "D02",
                    "description": "Поступление без ЭСФ (срок истёк)",
                    "severity": "высокая",
                    "count": 2,
                    "amount_impact": "56000.00",
                },
                {
                    "code": "D16",
                    "description": "Поступление не проведено",
                    "severity": "средняя",
                    "count": 1,
                    "amount_impact": "0.00",
                },
            ],
            "rounding_total": "3.47",
            "discrepancy_ids": ["disc-0001", "disc-0002", "disc-0003"],
            "computed_at": dt.datetime(2026, 7, 1, 9, 30).isoformat(),
            "from_cache": False,
        }

    def _ПолучитьСписокРасхождений(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "items": [
                {
                    "id": "disc-0001",
                    "code": "D14",
                    "severity": "инфо",
                    "counterparty_name": COUNTERPARTY["name"],
                    "receipt": RECEIPT_REF,
                    "esf": ESF_REF,
                    "amount_impact": "0.01",
                    "summary": "НДС в 1С 84,00 ₸, в ЭСФ 84,01 ₸ — построчное округление против округления от итога",
                },
                {
                    "id": "disc-0002",
                    "code": "D03",
                    "severity": "высокая",
                    "counterparty_name": COUNTERPARTY_2["name"],
                    "receipt": None,
                    "esf": ESF_NO_RECEIPT_REF,
                    "amount_impact": "46080.00",
                    "summary": "ЭСФ на 430 080,00 ₸ без поступления в базе",
                },
            ],
            "page": params.get("page", 1),
            "page_size": params.get("page_size", 50),
            "total": 2,
            "has_more": False,
        }

    def _ПолучитьРасхождение(self, params: dict[str, Any]) -> dict[str, Any]:
        if params["discrepancy_id"] == "disc-0002":
            return {"card": self._card_d03()}
        return {"card": self._card_d14()}

    def _card_d14(self) -> dict[str, Any]:
        return {
            "id": "disc-0001",
            "code": "D14",
            "description": "Копеечное расхождение в допуске (см. R1–R6)",
            "severity": "инфо",
            "receipt": RECEIPT_REF,
            "esf": ESF_REF,
            "counterparty": COUNTERPARTY,
            "field_comparisons": [
                {
                    "field": "Сумма без НДС",
                    "receipt_value": "700.06",
                    "esf_value": "700.06",
                    "difference": None,
                    "within_tolerance": True,
                },
                {
                    "field": "Сумма НДС",
                    "receipt_value": "84.00",
                    "esf_value": "84.01",
                    "difference": "-0.01",
                    "within_tolerance": True,
                },
            ],
            "line_comparisons": [
                {
                    "receipt_line": RECEIPT_LINES[2],
                    "esf_lines": [ESF_LINES[2]],
                    "status": "совпадает_в_допуске",
                    "match_level": "2",
                    "confidence": "высокая",
                    "difference_net": "0",
                    "difference_vat": "-0.01",
                    "within_tolerance": True,
                }
            ],
            "rounding_code": "R1",
            "probable_cause": (
                "В поступлении НДС посчитан построчно и округлён в каждой строке "
                "(12,00 + 24,00 + 48,00 = 84,00). В ЭСФ НДС рассчитан от итога: "
                "700,06 × 12 % = 84,0072, округлено до 84,01."
            ),
            "recommended_action": "Допуск; при необходимости корректировка суммы НДС в строке",
            "suggested_tool": "plan_adjust_lines",
            "reviewed": False,
            "reviewed_comment": None,
        }

    def _card_d03(self) -> dict[str, Any]:
        return {
            "id": "disc-0002",
            "code": "D03",
            "description": "ЭСФ без поступления",
            "severity": "высокая",
            "receipt": None,
            "esf": ESF_NO_RECEIPT_REF,
            "counterparty": COUNTERPARTY_2,
            "field_comparisons": [],
            "line_comparisons": [],
            "rounding_code": None,
            "probable_cause": (
                "ЭСФ выписана 03.06.2026, дата оборота 01.06.2026, поступление в базе "
                "не найдено ни по явной связи, ни по сумме и дате."
            ),
            "recommended_action": "Создать поступление по ЭСФ",
            "suggested_tool": "plan_create_receipt_from_esf",
            "reviewed": False,
            "reviewed_comment": None,
        }

    def _ПолучитьДокумент(self, params: dict[str, Any]) -> dict[str, Any]:
        if params["kind"] == "ЭСФПолученный":
            return {
                "receipt": None,
                "esf": {
                    "header": {
                        "ref": ESF_REF,
                        "organization": ORG,
                        "counterparty": COUNTERPARTY,
                        "contract_name": "Договор поставки № 12 от 09.01.2026",
                        "currency": "KZT",
                        "exchange_rate": "1",
                        "amount_net": "700.06",
                        "amount_vat": "84.01",
                        "amount_gross": "784.07",
                        "vat_included_in_price": False,
                        "is_posted": True,
                    },
                    "lines": ESF_LINES,
                    "status": "выписана",
                    "turnover_date": "2026-05-14",
                    "issue_date": "2026-05-15",
                    "registration_number": "ESF-KZ-8891-2026",
                    "linked_receipt": RECEIPT_REF,
                    "corrects": None,
                },
            }
        return {
            "receipt": {
                "header": {
                    "ref": RECEIPT_REF,
                    "organization": ORG,
                    "counterparty": COUNTERPARTY,
                    "contract_name": "Договор поставки № 12 от 09.01.2026",
                    "currency": "KZT",
                    "exchange_rate": "1",
                    "amount_net": "700.06",
                    "amount_vat": "84.00",
                    "amount_gross": "784.06",
                    "vat_included_in_price": False,
                    "is_posted": True,
                },
                "lines": RECEIPT_LINES,
                "esf_number": "8891",
                "esf_date": "2026-05-15",
                "linked_esf": ESF_REF,
            },
            "esf": None,
        }

    def _ПолучитьОборотыНДС(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "rows": [
                {
                    "vat_rate": "12",
                    "amount_net": "14206083.00",
                    "amount_vat": "1704729.96",
                    "source": "учёт",
                },
                {
                    "vat_rate": "12",
                    "amount_net": "14206083.00",
                    "amount_vat": "1704733.43",
                    "source": "ЭСФ",
                },
            ],
            "accounting_vat_total": "1704729.96",
            "esf_vat_total": "1704733.43",
            "difference": "-3.47",
        }

    def _ПолучитьКарточкуКонтрагента(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "counterparty": COUNTERPARTY,
            "is_vat_payer_on_date": True,
            "vat_certificate_series": "60001",
            "vat_certificate_number": "0012345",
            "vat_registered_from": "2019-03-12",
            "vat_deregistered_from": None,
        }

    def _НайтиКандидатовЭСФ(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "candidates": [
                {
                    "ref": ESF_REF,
                    "score": "0.94",
                    "confidence": "высокая",
                    "matched_on": ["БИН", "сумма с НДС", "дата оборота"],
                    "amount_difference": "0.01",
                    "days_difference": 1,
                }
            ]
        }

    def _НайтиКандидатовПоступления(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"candidates": []}

    def _ПолучитьИсториюИзменений(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"versioning_enabled": False, "versions": []}

    def _ПолучитьНастройки(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "settings": {
                "tolerances": {
                    "line_per_unit": "0.01",
                    "line_cap": "1.00",
                    "document_per_line": "0.01",
                    "document_cap": "5.00",
                    "period_material": "1.00",
                },
                "masking_enabled": True,
                "period_tail_days": 20,
                "include_extra_costs": False,
                "include_expense_reports": False,
            }
        }

    def _ОткрытьОбъект(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "navigation_link": f"e1cib/data/Документ.{params['kind']}?ref={params['uuid']}",
            "presentation": params["uuid"],
        }

    # -- запись, фаза 1 -----------------------------------------------------

    def _УстановитьСвязь_План(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "plan_id": "plan-link-0001",
            "tool": "plan_set_link",
            "discrepancy_id": params.get("discrepancy_id"),
            "title": "Установить связь поступления № 145 с ЭСФ № 8891",
            "changes": [
                {
                    "target": ESF_REF,
                    "path": "ДокументОснование",
                    "old_value": None,
                    "new_value": RECEIPT_REF["presentation"],
                    "comment": None,
                }
            ],
            "affected_postings": [],
            "blocked": False,
            "block_reason": None,
            "requires_reposting": False,
        }

    def _СкорректироватьСтроки_План(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "plan_id": "plan-lines-0001",
            "tool": "plan_adjust_lines",
            "discrepancy_id": params.get("discrepancy_id"),
            "title": "Привести НДС строки 3 к значению из ЭСФ",
            "changes": [
                {
                    "target": RECEIPT_REF,
                    "path": "Товары[3].СуммаНДС",
                    "old_value": "48.00",
                    "new_value": "48.01",
                    "comment": "ЭСФ принята за эталон (паттерн R1)",
                },
                {
                    "target": RECEIPT_REF,
                    "path": "СуммаДокумента",
                    "old_value": "784.06",
                    "new_value": "784.07",
                    "comment": None,
                },
            ],
            "affected_postings": ["Дт 1420 Кт 3310 — 0,01 ₸"],
            "blocked": False,
            "block_reason": None,
            "requires_reposting": True,
        }

    def _ИзменитьПризнакНДС_План(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "plan_id": "plan-vat-0001",
            "tool": "plan_change_vat_flag",
            "discrepancy_id": params.get("discrepancy_id"),
            "title": "Снять флаг «Сумма включает НДС»",
            "changes": [
                {
                    "target": RECEIPT_REF,
                    "path": "СуммаВключаетНДС",
                    "old_value": "Да",
                    "new_value": "Нет",
                    "comment": "Пересчёт сумм по всем строкам",
                }
            ],
            "affected_postings": ["Дт 1420 Кт 3310"],
            "blocked": False,
            "block_reason": None,
            "requires_reposting": True,
        }

    def _СоздатьКорректировку_План(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "plan_id": "plan-adj-0001",
            "tool": "plan_create_adjustment",
            "discrepancy_id": params.get("discrepancy_id"),
            "title": "Создать корректировку поступления № 145",
            "changes": [],
            "affected_postings": [],
            "blocked": True,
            "block_reason": "Период закрыт по 31.03.2026; документ от 14.05.2026 в открытом периоде — корректировка не требуется",
            "requires_reposting": False,
        }

    def _ПометитьПроверено(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"discrepancy_id": params["discrepancy_id"], "reviewed": True}

    def _СоздатьПоступлениеПоЭСФ_План(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "plan_id": "plan-receipt-0001",
            "draft": {
                "esf": ESF_NO_RECEIPT_REF,
                "counterparty": COUNTERPARTY_2,
                "contract_name": "Основной договор",
                "contract_confidence": "средняя",
                "warehouse": "Основной склад",
                "date": "2026-06-01",
                "lines": [
                    {
                        "esf_line_number": 1,
                        "item": {
                            "item_uuid": "item-2001",
                            "item_name": "Кабель ВВГнг 3х2.5",
                            "confidence": "высокая",
                            "source": "история сопоставлений",
                            "alternatives": [],
                        },
                        "uom": "м",
                        "quantity": "1200",
                        "price": "320.00",
                        "amount_net": "384000.00",
                        "vat_rate": "12",
                        "amount_vat": "46080.00",
                        "account": "1330",
                        "account_confidence": "высокая",
                    },
                    {
                        "esf_line_number": 2,
                        "item": {
                            "item_uuid": None,
                            "item_name": "Муфта соединительная СТп-1",
                            "confidence": "низкая",
                            "source": "не найдено — создать новую",
                            "alternatives": ["Муфта соединительная", "Муфта СТп"],
                        },
                        "uom": "шт",
                        "quantity": "40",
                        "price": "0.00",
                        "amount_net": "0.00",
                        "vat_rate": "12",
                        "amount_vat": "0.00",
                        "account": None,
                        "account_confidence": None,
                    },
                ],
                "uncertain_lines": 1,
                "amount_net": "384000.00",
                "amount_vat": "46080.00",
                "amount_gross": "430080.00",
            },
            "blocked": False,
            "block_reason": None,
        }

    def _СоздатьПоступленияМассово_План(self, params: dict[str, Any]) -> dict[str, Any]:
        single = self._СоздатьПоступлениеПоЭСФ_План({"esf_uuid": params["esf_uuids"][0]})
        return {
            "plan_id": "plan-bulk-0001",
            "drafts": [single["draft"]],
            "total_uncertain_lines": 1,
            "blocked": False,
            "block_reason": None,
        }
