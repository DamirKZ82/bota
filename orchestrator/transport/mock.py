"""Мок-транспорт: правдоподобные данные вместо реальной базы 1С.

Нужен, чтобы оркестратор, цикл агента и формат ответа отлаживались до появления
расширения. Данные подобраны так, чтобы воспроизводить три кейса из ТЗ:

* **D14 / R1** — построчное округление НДС против округления от итога. Три строки
  дают 112,00 ₸ построчно и 112,01 ₸ от итога: ровно тот тиын, который
  накапливается и вылезает в декларации.
* **D06 / R2** — в поступлении стоит «сумма включает НДС», а поставщик выписал
  ЭСФ с НДС сверху. Итоги расходятся на 2 648,28 ₸.
* **D03** — ЭСФ без поступления, по которой агент готовит черновик, где одна
  строка требует ручного выбора номенклатуры.

Ставка НДС — 16 %, как в примерах Приложения А. Все суммы пересчитаны и сходятся.
"""

from __future__ import annotations

from typing import Any

from orchestrator.tools.enums import ErrorCode
from orchestrator.tools.envelope import ToolRequest, ToolResponse
from orchestrator.transport.base import OneCTransport

VAT_RATE = "16"

ORG_REF = {
    "type": "Справочник.Организации",
    "uuid": "org-0001",
    "presentation": "ТОО «Пилот Аутсорс»",
    "nav": "e1cib/data/Справочник.Организации?ref=org-0001",
}

CP_REF = {
    "type": "Справочник.Контрагенты",
    "uuid": "cp-0001",
    "presentation": "ТОО «Снабженец»",
    "nav": "e1cib/data/Справочник.Контрагенты?ref=cp-0001",
}

CP2_REF = {
    "type": "Справочник.Контрагенты",
    "uuid": "cp-0002",
    "presentation": "ИП Ахметов",
    "nav": "e1cib/data/Справочник.Контрагенты?ref=cp-0002",
}

RECEIPT_REF = {
    "type": "Документ.ПоступлениеТМЗИУслуг",
    "uuid": "doc-receipt-145",
    "presentation": "Поступление ТМЗ и услуг 000145 от 14.05.2026",
    "nav": "e1cib/data/Документ.ПоступлениеТМЗИУслуг?ref=doc-receipt-145",
}

RECEIPT_R2_REF = {
    "type": "Документ.ПоступлениеТМЗИУслуг",
    "uuid": "doc-receipt-208",
    "presentation": "Поступление ТМЗ и услуг 000208 от 22.05.2026",
    "nav": "e1cib/data/Документ.ПоступлениеТМЗИУслуг?ref=doc-receipt-208",
}

ESF_REF = {
    "type": "Документ.СчетФактураПолученный",
    "uuid": "doc-esf-8891",
    "presentation": "ЭСФ (полученный) 8891 от 15.05.2026",
    "nav": "e1cib/data/Документ.СчетФактураПолученный?ref=doc-esf-8891",
}

ESF_R2_REF = {
    "type": "Документ.СчетФактураПолученный",
    "uuid": "doc-esf-8934",
    "presentation": "ЭСФ (полученный) 8934 от 23.05.2026",
    "nav": "e1cib/data/Документ.СчетФактураПолученный?ref=doc-esf-8934",
}

ESF_ORPHAN_REF = {
    "type": "Документ.СчетФактураПолученный",
    "uuid": "doc-esf-9042",
    "presentation": "ЭСФ (полученный) 9042 от 03.06.2026",
    "nav": "e1cib/data/Документ.СчетФактураПолученный?ref=doc-esf-9042",
}


def _line(
    n: int,
    name: str,
    unit: str,
    qty: str,
    price: str,
    net: str,
    vat: str,
    total: str,
    item_uuid: str | None = None,
) -> dict[str, Any]:
    line: dict[str, Any] = {
        "n": n,
        "name": name,
        "unit": unit,
        "qty": qty,
        "price": price,
        "net": net,
        "vat_rate": VAT_RATE,
        "vat": vat,
        "total": total,
    }
    if item_uuid:
        line["item"] = {
            "type": "Справочник.Номенклатура",
            "uuid": item_uuid,
            "presentation": name,
            "nav": f"e1cib/data/Справочник.Номенклатура?ref={item_uuid}",
        }
    return line


# Построчный НДС: 16,00 + 32,00 + 64,00 = 112,00.
# НДС от итога:  700,06 × 16 % = 112,0096 → 112,01. Разница — тот самый тиын.
RECEIPT_LINES = [
    _line(1, "Бумага А4 «Снегурочка»", "пач", "7.000", "14.29",
          "100.03", "16.00", "116.03", "item-0001"),
    _line(2, "Картридж HP CF217A", "шт", "3.000", "66.67",
          "200.01", "32.00", "232.01", "item-0002"),
    _line(3, "Бумага для флипчарта", "рул", "6.000", "66.67",
          "400.02", "64.00", "464.02", "item-0003"),
]

ESF_LINES = [
    _line(1, "Бумага офисная А4 «Снегурочка»", "пач", "7.000", "14.29",
          "100.03", "16.00", "116.03"),
    _line(2, "Картридж HP CF217A", "шт", "3.000", "66.67",
          "200.01", "32.00", "232.01"),
    _line(3, "Бумага для флипчарта 80 г/м2", "рул", "6.000", "66.67",
          "400.02", "64.01", "464.03"),
]

# R2: в поступлении цена 1 200,00 включает НДС (120 000 / 1,16 = 103 448,28),
# в ЭСФ та же цена взята как цена без НДС (120 000 × 16 % = 19 200,00).
RECEIPT_R2_LINES = [
    _line(1, "Кабель ВВГнг 3х2.5", "м", "100.000", "1200.00",
          "103448.28", "16551.72", "120000.00", "item-2001"),
]

ESF_R2_LINES = [
    _line(1, "Кабель ВВГнг 3х2.5", "м", "100.000", "1200.00",
          "120000.00", "19200.00", "139200.00"),
]


class MockTransport(OneCTransport):
    """Отдаёт фиксированные ответы по имени инструмента, в конверте A.0.2."""

    async def call(self, tenant_id: str, request: ToolRequest) -> ToolResponse:
        handler = getattr(self, f"_{request.tool}", None)
        if handler is None:
            return ToolResponse.failure(
                request.tool,
                ErrorCode.NOT_SUPPORTED_RELEASE,
                f"Мок не умеет «{request.tool}»",
            )
        return ToolResponse(
            ok=True,
            tool=request.tool,
            duration_ms=12,
            result=handler(request.args),
        )

    # -- чтение -------------------------------------------------------------

    def _get_context(self, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "base": {
                "name": "ТОО «Пилот Аутсорс» (основная)",
                "config": "Бухгалтерия для Казахстана",
                "config_version": "3.0.52.14",
                "platform": "8.3.24.1548",
                "currency": "KZT",
                "extension_version": "0.1.0",
            },
            "organizations": [
                {
                    "ref": ORG_REF,
                    "bin": "010203040506",
                    "vat_payer": True,
                    "forbid_date": "2026-03-31",
                }
            ],
            "current_period": {"from": "2026-04-01", "to": "2026-06-30", "kind": "quarter"},
            "settings": self._settings(),
            "permissions": {"read": True, "apply": True, "create": True},
        }

    @staticmethod
    def _settings() -> dict[str, Any]:
        return {
            "line_tolerance_per_unit": "0.01",
            "line_tolerance_max": "1.00",
            "doc_tolerance_per_line": "0.01",
            "doc_tolerance_max": "5.00",
            "period_tolerance_total": "1.00",
            "esf_tail_days": 20,
            "candidate_days": 10,
        }

    def _reconcile_period(self, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "calc_id": "calc-2026q2-0001",
            "calculated_at": "2026-07-01T09:30:00",
            "from_cache": False,
            "scope": {
                "receipts_total": 124,
                "receipts_unposted": 1,
                "esf_total": 121,
                "pairs_linked": 104,
                "pairs_suggested": 14,
                "receipts_without_esf": 6,
                "esf_without_receipt": 4,
            },
            "summary_by_code": [
                {"code": "D14", "severity": "info", "count": 37, "amount_vat": "3.47"},
                {"code": "D03", "severity": "high", "count": 4, "amount_vat": "184320.00"},
                {"code": "D06", "severity": "high", "count": 3, "amount_vat": "2648.28"},
                {"code": "D02", "severity": "high", "count": 2, "amount_vat": "56000.00"},
                {"code": "D16", "severity": "medium", "count": 1, "amount_vat": "0.00"},
            ],
            "rounding": {
                "total_diff_vat": "3.47",
                "total_diff_net": "-1.12",
                "by_pattern": [
                    {"pattern": "R1", "count": 29, "diff_vat": "2.90"},
                    {"pattern": "R2", "count": 3, "diff_vat": "0.44"},
                    {"pattern": "R6", "count": 5, "diff_vat": "0.13"},
                ],
            },
            "vat_totals": {
                "receipts_vat": "5241877.12",
                "esf_vat": "5241880.59",
                "diff": "3.47",
                "ledger_vat_1420": "5241877.12",
            },
            "top_discrepancies": ["d1a2b3c4e5f6a7b8", "b7c8d9e0f1a2b3c4", "c4d5e6f7a8b9c0d1"],
        }

    def _list_discrepancies(self, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "items": [
                {
                    "id": "b7c8d9e0f1a2b3c4",
                    "code": "D06",
                    "severity": "high",
                    "status": "open",
                    "pattern": "R2",
                    "counterparty": {"ref": CP_REF, "bin": "123456789012"},
                    "receipt": {
                        "ref": RECEIPT_R2_REF,
                        "date": "2026-05-22",
                        "number": "000208",
                        "total": "120000.00",
                        "vat": "16551.72",
                    },
                    "esf": {
                        "ref": ESF_R2_REF,
                        "date": "2026-05-23",
                        "reg_number": "ESF-KZ-8934-2026",
                        "status": "delivered",
                        "total": "139200.00",
                        "vat": "19200.00",
                    },
                    "diff": {"net": "-16551.72", "vat": "-2648.28", "total": "-19200.00"},
                    "short": (
                        "В поступлении цена 1 200,00 ₸ включает НДС, в ЭСФ — без НДС. "
                        "Расхождение НДС 2 648,28 ₸ (паттерн R2)"
                    ),
                },
                {
                    "id": "d1a2b3c4e5f6a7b8",
                    "code": "D14",
                    "severity": "info",
                    "status": "open",
                    "pattern": "R1",
                    "counterparty": {"ref": CP_REF, "bin": "123456789012"},
                    "receipt": {
                        "ref": RECEIPT_REF,
                        "date": "2026-05-14",
                        "number": "000145",
                        "total": "812.06",
                        "vat": "112.00",
                    },
                    "esf": {
                        "ref": ESF_REF,
                        "date": "2026-05-15",
                        "reg_number": "ESF-KZ-8891-2026",
                        "status": "delivered",
                        "total": "812.07",
                        "vat": "112.01",
                    },
                    "diff": {"net": "0.00", "vat": "-0.01", "total": "-0.01"},
                    "short": (
                        "НДС в 1С 112,00 ₸, в ЭСФ 112,01 ₸ — построчное округление "
                        "против округления от итога (паттерн R1)"
                    ),
                },
                {
                    "id": "c4d5e6f7a8b9c0d1",
                    "code": "D03",
                    "severity": "high",
                    "status": "open",
                    "pattern": None,
                    "counterparty": {"ref": CP2_REF, "bin": "880101300123"},
                    "receipt": None,
                    "esf": {
                        "ref": ESF_ORPHAN_REF,
                        "date": "2026-06-03",
                        "reg_number": "ESF-KZ-9042-2026",
                        "status": "delivered",
                        "total": "445440.00",
                        "vat": "61440.00",
                    },
                    "diff": {"net": "-384000.00", "vat": "-61440.00", "total": "-445440.00"},
                    "short": "ЭСФ на 445 440,00 ₸ без поступления в базе",
                },
            ],
            "page": args.get("page", 1),
            "page_size": args.get("page_size", 50),
            "total": 3,
            "has_more": False,
        }

    def _get_discrepancy(self, args: dict[str, Any]) -> dict[str, Any]:
        card_id = args.get("id")
        if card_id == "b7c8d9e0f1a2b3c4":
            return self._card_r2()
        if card_id == "c4d5e6f7a8b9c0d1":
            return self._card_d03()
        return self._card_r1()

    def _card_r1(self) -> dict[str, Any]:
        return {
            "id": "d1a2b3c4e5f6a7b8",
            "code": "D14",
            "severity": "info",
            "status": "open",
            "reviewed": None,
            "pair": {
                "receipt": {
                    "ref": RECEIPT_REF,
                    "date": "2026-05-14",
                    "number": "000145",
                    "posted": True,
                    "vat_included_in_price": False,
                    "currency": "KZT",
                    "totals": {"net": "700.06", "vat": "112.00", "total": "812.06"},
                },
                "esf": {
                    "ref": ESF_REF,
                    "date_issue": "2026-05-15",
                    "date_turnover": "2026-05-14",
                    "reg_number": "ESF-KZ-8891-2026",
                    "status": "delivered",
                    "kind": "original",
                    "replaces": None,
                    "totals": {"net": "700.06", "vat": "112.01", "total": "812.07"},
                },
                "link": "explicit",
            },
            "header_diff": [
                {
                    "field": "net",
                    "receipt": "700.06",
                    "esf": "700.06",
                    "diff": "0.00",
                    "within_tolerance": True,
                },
                {
                    "field": "vat",
                    "receipt": "112.00",
                    "esf": "112.01",
                    "diff": "-0.01",
                    "within_tolerance": True,
                },
                {
                    "field": "date_turnover",
                    "receipt": "2026-05-14",
                    "esf": "2026-05-14",
                    "diff": 0,
                    "within_tolerance": True,
                },
            ],
            "lines": [
                {
                    "match": "matched",
                    "match_level": 2,
                    "confidence": "high",
                    "receipt_line": RECEIPT_LINES[0],
                    "esf_line": ESF_LINES[0],
                    "diff": None,
                    "pattern": None,
                },
                {
                    "match": "matched_within_tolerance",
                    "match_level": 2,
                    "confidence": "high",
                    "receipt_line": RECEIPT_LINES[2],
                    "esf_line": ESF_LINES[2],
                    "diff": {"vat": "-0.01", "total": "-0.01"},
                    "pattern": "R1",
                },
            ],
            "diagnosis": {
                "pattern": "R1",
                "explanation": (
                    "В поступлении НДС посчитан построчно и округлён в каждой строке "
                    "(16,00 + 32,00 + 64,00 = 112,00). В ЭСФ НДС рассчитан от итога: "
                    "700,06 × 16 % = 112,0096, округлено до 112,01. Разница 0,01 ₸ "
                    "в пределах допуска."
                ),
                "confidence": "high",
            },
            "suggested_actions": [
                {
                    "action": "adjust_lines",
                    "label": "Привести НДС строки 3 к значению ЭСФ",
                    "risk": "low",
                },
                {"action": "mark_reviewed", "label": "Принять как есть", "risk": "none"},
            ],
        }

    def _card_r2(self) -> dict[str, Any]:
        return {
            "id": "b7c8d9e0f1a2b3c4",
            "code": "D06",
            "severity": "high",
            "status": "open",
            "reviewed": None,
            "pair": {
                "receipt": {
                    "ref": RECEIPT_R2_REF,
                    "date": "2026-05-22",
                    "number": "000208",
                    "posted": True,
                    "vat_included_in_price": True,
                    "currency": "KZT",
                    "totals": {"net": "103448.28", "vat": "16551.72", "total": "120000.00"},
                },
                "esf": {
                    "ref": ESF_R2_REF,
                    "date_issue": "2026-05-23",
                    "date_turnover": "2026-05-22",
                    "reg_number": "ESF-KZ-8934-2026",
                    "status": "delivered",
                    "kind": "original",
                    "replaces": None,
                    "totals": {"net": "120000.00", "vat": "19200.00", "total": "139200.00"},
                },
                "link": "explicit",
            },
            "header_diff": [
                {
                    "field": "net",
                    "receipt": "103448.28",
                    "esf": "120000.00",
                    "diff": "-16551.72",
                    "within_tolerance": False,
                },
                {
                    "field": "vat",
                    "receipt": "16551.72",
                    "esf": "19200.00",
                    "diff": "-2648.28",
                    "within_tolerance": False,
                },
                {
                    "field": "rate",
                    "receipt": "16",
                    "esf": "16",
                    "diff": None,
                    "within_tolerance": True,
                },
            ],
            "lines": [
                {
                    "match": "matched_with_diff",
                    "match_level": 2,
                    "confidence": "high",
                    "receipt_line": RECEIPT_R2_LINES[0],
                    "esf_line": ESF_R2_LINES[0],
                    "diff": {
                        "qty": "0.000",
                        "price": "0.00",
                        "net": "-16551.72",
                        "vat": "-2648.28",
                        "total": "-19200.00",
                    },
                    "pattern": "R2",
                },
            ],
            "diagnosis": {
                "pattern": "R2",
                "explanation": (
                    "Цена 1 200,00 ₸ одна и та же, но в поступлении включён флаг "
                    "«Сумма включает НДС», а поставщик выписал ЭСФ с НДС сверху. "
                    "Из-за этого расходятся и сумма без НДС, и сумма НДС."
                ),
                "confidence": "high",
            },
            "suggested_actions": [
                {
                    "action": "set_vat_mode",
                    "label": "Снять «Сумма включает НДС» и пересчитать",
                    "risk": "low",
                },
                {
                    "action": "adjust_lines",
                    "label": "Привести суммы к значениям ЭСФ",
                    "risk": "low",
                },
            ],
        }

    def _card_d03(self) -> dict[str, Any]:
        return {
            "id": "c4d5e6f7a8b9c0d1",
            "code": "D03",
            "severity": "high",
            "status": "open",
            "reviewed": None,
            "pair": {
                "receipt": None,
                "esf": {
                    "ref": ESF_ORPHAN_REF,
                    "date_issue": "2026-06-03",
                    "date_turnover": "2026-06-01",
                    "reg_number": "ESF-KZ-9042-2026",
                    "status": "delivered",
                    "kind": "original",
                    "replaces": None,
                    "totals": {"net": "384000.00", "vat": "61440.00", "total": "445440.00"},
                },
                "link": "none",
            },
            "header_diff": [],
            "lines": [],
            "diagnosis": {
                "pattern": None,
                "explanation": (
                    "ЭСФ выписана 03.06.2026, дата оборота 01.06.2026. Поступление "
                    "в базе не найдено ни по явной связи, ни по контрагенту, сумме и дате."
                ),
                "confidence": "high",
            },
            "suggested_actions": [
                {
                    "action": "create_receipt_from_esf",
                    "label": "Создать поступление по ЭСФ",
                    "risk": "medium",
                },
            ],
        }

    def _get_document(self, args: dict[str, Any]) -> dict[str, Any]:
        if str(args.get("uuid", "")).startswith("doc-esf"):
            return {
                "receipt": None,
                "esf": {
                    "head": {
                        "ref": ESF_REF,
                        "date_issue": "2026-05-15",
                        "date_turnover": "2026-05-14",
                        "reg_number": "ESF-KZ-8891-2026",
                        "status": "delivered",
                        "kind": "original",
                        "replaces": None,
                        "totals": {"net": "700.06", "vat": "112.01", "total": "812.07"},
                    },
                    "lines": ESF_LINES,
                },
                "links": [{"ref": RECEIPT_REF, "kind": "receipt"}],
            }
        return {
            "receipt": {
                "head": {
                    "ref": RECEIPT_REF,
                    "date": "2026-05-14",
                    "number": "000145",
                    "posted": True,
                    "vat_included_in_price": False,
                    "currency": "KZT",
                    "exchange_rate": None,
                    "totals": {"net": "700.06", "vat": "112.00", "total": "812.06"},
                },
                "lines": RECEIPT_LINES,
            },
            "esf": None,
            "links": [{"ref": ESF_REF, "kind": "esf"}],
        }

    def _find_esf_candidates(self, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "items": [
                {
                    "esf": {
                        "ref": ESF_REF,
                        "date": "2026-05-15",
                        "reg_number": "ESF-KZ-8891-2026",
                        "status": "delivered",
                        "total": "812.07",
                        "vat": "112.01",
                    },
                    "score": 0.94,
                    "reasons": ["same_bin", "total_equal", "date_diff_1", "lines_similar_0.87"],
                    "already_linked_to": None,
                }
            ]
        }

    def _find_receipt_candidates(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"items": []}

    def _get_counterparty(self, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "ref": CP_REF,
            "bin": "123456789012",
            "name": "ТОО «Снабженец»",
            "vat_payer": True,
            "vat_certificate": {
                "series": "60001",
                "number": "0012345",
                "date_from": "2019-03-12",
                "date_to": None,
            },
            "vat_status_on_date": "payer",
            "main_contract": {
                "type": "Справочник.ДоговорыКонтрагентов",
                "uuid": "contract-0001",
                "presentation": "Договор поставки № 12 от 09.01.2026",
                "nav": "e1cib/data/Справочник.ДоговорыКонтрагентов?ref=contract-0001",
            },
            "contracts_count": 2,
            "receipts_in_period": 14,
            "esf_in_period": 13,
            "item_mapping_count": 37,
        }

    def _get_vat_turnover(self, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "by_rate": [
                {"rate": "16", "net": "32761732.00", "vat": "5241877.12", "account": "1420"},
                {"rate": "0", "net": "1840000.00", "vat": "0.00", "account": None},
                {"rate": "exempt", "net": "412000.00", "vat": "0.00", "account": None},
            ],
            "register_vat_offset": "5241877.12",
            "ledger_1420_debit": "5241877.12",
            "esf_sum_delivered": "5241880.59",
            "diff_ledger_vs_esf": "-3.47",
        }

    def _get_journal(self, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "items": [
                {
                    "plan_id": "plan-lines-0001",
                    "session_id": "session-0001",
                    "action": "adjust_lines",
                    "applied_at": "2026-07-01T10:12:00",
                    "user": "Бухгалтер Иванова",
                    "objects": [RECEIPT_REF],
                    "rollback_ref": None,
                }
            ],
            "page": args.get("page", 1),
            "page_size": args.get("page_size", 50),
            "total": 1,
            "has_more": False,
        }

    def _get_settings(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"settings": self._settings()}

    def _open_object(self, args: dict[str, Any]) -> dict[str, Any]:
        return {"nav": f"e1cib/data/Документ?ref={args['uuid']}"}

    # -- запись, фаза 1 -----------------------------------------------------

    @staticmethod
    def _plan_base(plan_id: str, action: str) -> dict[str, Any]:
        return {
            "plan_id": plan_id,
            "action": action,
            "expires_at": "2026-07-01T10:00:00",
            "checks": [{"check": "period_open", "ok": True, "message": None}],
            "can_apply": True,
            "block_reason": None,
        }

    def _plan_set_link(self, args: dict[str, Any]) -> dict[str, Any]:
        plan = self._plan_base("plan-link-0001", "set_link")
        plan["checks"] += [
            {"check": "esf_not_linked_elsewhere", "ok": True, "message": None},
            {"check": "same_counterparty", "ok": True, "message": None},
        ]
        plan["changes"] = [
            {
                "object": ESF_REF,
                "line": None,
                "field": "ДокументОснование",
                "from": None,
                "to": RECEIPT_REF["presentation"],
            }
        ]
        return plan

    def _plan_adjust_lines(self, args: dict[str, Any]) -> dict[str, Any]:
        plan = self._plan_base("plan-lines-0001", "adjust_lines")
        plan["document"] = RECEIPT_REF
        plan["changes"] = [
            {"object": None, "line": 3, "field": "СуммаНДС", "from": "64.00", "to": "64.01"},
        ]
        plan["totals_after"] = {"net": "700.06", "vat": "112.01", "total": "812.07"}
        plan["postings_affected"] = [
            {"account_dt": "1420", "account_kt": "3310", "from": "112.00", "to": "112.01"}
        ]
        plan["will_repost"] = True
        return plan

    def _plan_set_vat_mode(self, args: dict[str, Any]) -> dict[str, Any]:
        plan = self._plan_base("plan-vat-0001", "set_vat_mode")
        plan["document"] = RECEIPT_R2_REF
        plan["changes"] = [
            {
                "object": None,
                "line": None,
                "field": "СуммаВключаетНДС",
                "from": "Да",
                "to": "Нет",
            },
            {"object": None, "line": 1, "field": "Сумма", "from": "103448.28", "to": "120000.00"},
            {"object": None, "line": 1, "field": "СуммаНДС", "from": "16551.72", "to": "19200.00"},
        ]
        plan["totals_after"] = {"net": "120000.00", "vat": "19200.00", "total": "139200.00"}
        plan["postings_affected"] = [
            {"account_dt": "1330", "account_kt": "3310", "from": "103448.28", "to": "120000.00"},
            {"account_dt": "1420", "account_kt": "3310", "from": "16551.72", "to": "19200.00"},
        ]
        plan["will_repost"] = True
        return plan

    def _plan_create_correction(self, args: dict[str, Any]) -> dict[str, Any]:
        plan = self._plan_base("plan-corr-0001", "create_correction")
        plan["can_apply"] = False
        plan["block_reason"] = (
            "Период закрыт по 31.03.2026, документ от 22.05.2026 в открытом периоде — "
            "корректировка не требуется, правьте исходный документ"
        )
        plan["checks"] = [
            {"check": "period_open", "ok": True, "message": "Документ в открытом периоде"}
        ]
        plan["basis"] = RECEIPT_R2_REF
        plan["lines"] = []
        plan["totals_after"] = {"net": "120000.00", "vat": "19200.00", "total": "139200.00"}
        plan["will_post"] = False
        return plan

    def _plan_create_receipt_from_esf(self, args: dict[str, Any]) -> dict[str, Any]:
        overrides = {o["esf_line"]: o["item"] for o in args.get("overrides", [])}
        second_resolved = 2 in overrides

        plan = self._plan_base("plan-receipt-0001", "create_receipt_from_esf")
        plan["checks"] = [
            {"check": "period_open", "ok": True, "message": None},
            {"check": "esf_not_linked", "ok": True, "message": None},
        ]
        plan["can_apply"] = second_resolved
        plan["block_reason"] = (
            None if second_resolved else "1 строка требует выбора номенклатуры"
        )
        plan["draft"] = {
            "organization": ORG_REF,
            "counterparty": {"ref": CP2_REF, "confidence": "high", "alternatives": []},
            "contract": {
                "ref": {
                    "type": "Справочник.ДоговорыКонтрагентов",
                    "uuid": "contract-0002",
                    "presentation": "Основной договор",
                    "nav": None,
                },
                "confidence": "medium",
                "alternatives": [],
            },
            "warehouse": {
                "ref": {
                    "type": "Справочник.Склады",
                    "uuid": "wh-0001",
                    "presentation": "Основной склад",
                    "nav": None,
                },
                "confidence": "medium",
                "alternatives": [],
            },
            "date": "2026-06-01",
            "vat_included": False,
            "lines": [
                {
                    "n": 1,
                    "esf_name": "Кабель ВВГнг 3х2.5",
                    "item": {
                        "ref": {
                            "type": "Справочник.Номенклатура",
                            "uuid": "item-2001",
                            "presentation": "Кабель ВВГнг 3х2.5",
                            "nav": None,
                        },
                        "confidence": "high",
                        "source": "history",
                        "alternatives": [],
                    },
                    "suggest_new_item": None,
                    "unit": "м",
                    "qty": "1200.000",
                    "price": "320.00",
                    "vat_rate": VAT_RATE,
                    "account": {"code": "1330", "confidence": "high"},
                    "needs_attention": False,
                },
                {
                    "n": 2,
                    "esf_name": "Муфта соединительная СТп-1",
                    "item": {
                        "ref": (
                            {
                                "type": "Справочник.Номенклатура",
                                "uuid": overrides.get(2, ""),
                                "presentation": "Муфта соединительная СТп-1",
                                "nav": None,
                            }
                            if second_resolved
                            else None
                        ),
                        "confidence": "high" if second_resolved else "none",
                        "source": "mapping" if second_resolved else "none",
                        "alternatives": [],
                    },
                    "suggest_new_item": (
                        None
                        if second_resolved
                        else {
                            "name": "Муфта соединительная СТп-1",
                            "unit": "шт",
                            "vat_rate": VAT_RATE,
                        }
                    ),
                    "unit": "шт",
                    "qty": "40.000",
                    "price": "0.00",
                    "vat_rate": VAT_RATE,
                    "account": {"code": "1330", "confidence": "medium"}
                    if second_resolved
                    else None,
                    "needs_attention": not second_resolved,
                },
            ],
            "totals": {"net": "384000.00", "vat": "61440.00", "total": "445440.00"},
            "attention_count": 0 if second_resolved else 1,
        }
        return plan

    def _plan_create_receipts_bulk(self, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "items": [
                {
                    "esf": ESF_ORPHAN_REF,
                    "plan_id": "plan-receipt-0001",
                    "attention_count": 1,
                    "can_apply": False,
                    "block_reason": "1 строка требует выбора номенклатуры",
                }
            ],
            "total_attention": 1,
        }

    def _mark_reviewed(self, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "discrepancy_id": args["discrepancy_id"],
            "status": args.get("status", "reviewed"),
        }
