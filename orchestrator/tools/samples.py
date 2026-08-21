"""Образцовые параметры вызова каждого инструмента.

Один набор на два потребителя: тесты гоняют его против мока, приёмочный скрипт —
против настоящего расширения 1С. Поэтому список живёт в пакете, а не в тестах.

Идентификаторы здесь — из мок-данных. Для проверки реальной базы их подменяют
через `--ids` (см. scripts/verify_extension.py): контракт от этого не меняется,
меняются только значения.
"""

from __future__ import annotations

from typing import Any

SAMPLE_ARGS: dict[str, dict[str, Any]] = {
    "get_context": {},
    "reconcile_period": {
        "organization": "org-0001",
        "from": "2026-04-01",
        "to": "2026-06-30",
    },
    "list_discrepancies": {"calc_id": "calc-2026q2-0001"},
    "get_discrepancy": {"id": "d1a2b3c4e5f6a7b8"},
    "get_document": {"uuid": "doc-receipt-145"},
    "find_esf_candidates": {"receipt_uuid": "doc-receipt-145"},
    "find_receipt_candidates": {"esf_uuid": "doc-esf-9042"},
    "get_counterparty": {"bin": "123456789012"},
    "get_vat_turnover": {
        "organization": "org-0001",
        "from": "2026-04-01",
        "to": "2026-06-30",
    },
    "get_journal": {"from": "2026-04-01", "to": "2026-06-30"},
    "plan_set_link": {"receipt_uuid": "doc-receipt-145", "esf_uuid": "doc-esf-8891"},
    "plan_adjust_lines": {"discrepancy_id": "d1a2b3c4e5f6a7b8"},
    "plan_set_vat_mode": {"receipt_uuid": "doc-receipt-208", "vat_included": False},
    "plan_create_correction": {
        "discrepancy_id": "b7c8d9e0f1a2b3c4",
        "reason": "закрытый период",
    },
    "plan_create_receipt_from_esf": {"esf_uuid": "doc-esf-9042"},
    "plan_create_receipts_bulk": {"esf_uuids": ["doc-esf-9042"]},
    "mark_reviewed": {"discrepancy_id": "d1a2b3c4e5f6a7b8", "comment": "принято"},
    "get_settings": {},
    "open_object": {"uuid": "doc-esf-8891"},
}

#: Инструменты, которые при проверке реальной базы что-то создают или помечают.
#: Планы ничего не меняют по определению (A.9), но `mark_reviewed` пишет пометку,
#: поэтому в режиме «только чтение» его пропускают.
WRITES_SOMETHING: frozenset[str] = frozenset({"mark_reviewed"})
