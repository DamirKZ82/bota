"""Маскирование данных перед отправкой в LLM (ТЗ п.6.4).

Отличие от ТЗ, сделанное сознательно: маскирование применяется **сразу при приёме
результата инструмента от 1С**, а не только перед вызовом модели. Тогда реальные
наименования и БИН не попадают ни в журнал вызовов, ни в трейсы, ни в дамп ошибки —
утекать из оркестратора становится нечему. Обратная подстановка делается один раз,
на выходе ответа пользователю.

Суммы и даты не маскируются — на них держится весь смысл сверки.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Поля контрактов, значение которых заменяется псевдонимом.
#: Ключ — имя поля, значение — префикс псевдонима.
SENSITIVE_FIELDS: dict[str, str] = {
    "name": "КОНТРАГЕНТ",
    "counterparty_name": "КОНТРАГЕНТ",
    "bin": "БИН",
    "item_name": "НОМЕНКЛАТУРА",
    "contract_name": "ДОГОВОР",
    "warehouse": "СКЛАД",
    "number": "НОМЕР",
    "registration_number": "РЕГНОМЕР",
    "presentation": "ДОКУМЕНТ",
    "changed_by": "ПОЛЬЗОВАТЕЛЬ",
    "vat_certificate_number": "СВИДЕТЕЛЬСТВО",
    "vat_certificate_series": "СЕРИЯ",
}

#: Поля, которые не маскируются, хотя и являются строками: это коды и служебные
#: значения, по которым модель принимает решения.
NEVER_MASK: frozenset[str] = frozenset(
    {
        "uuid",
        "kind",
        "code",
        "severity",
        "status",
        "confidence",
        "match_level",
        "rounding_code",
        "source",
        "currency",
        "accounting_currency",
        "uom",
        "field",
        "path",
        "tool",
        "plan_id",
        "id",
        "discrepancy_id",
        "navigation_link",
        "configuration_version",
        "platform_version",
    }
)

_ALIAS_RE = re.compile(r"\b(?:[А-ЯЁ]+)_\d+\b")


@dataclass
class MaskingSession:
    """Двусторонний словарь псевдонимов, стабильный в рамках одного диалога.

    Стабильность важна: если один и тот же контрагент в разных вызовах получит
    разные псевдонимы, модель решит, что это разные контрагенты, и ответ будет
    неверным.
    """

    enabled: bool = True
    _forward: dict[str, str] = field(default_factory=dict)
    _reverse: dict[str, str] = field(default_factory=dict)
    _counters: dict[str, int] = field(default_factory=dict)

    def alias_for(self, value: str, prefix: str) -> str:
        if value in self._forward:
            return self._forward[value]
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        alias = f"{prefix}_{self._counters[prefix]}"
        self._forward[value] = alias
        self._reverse[alias] = value
        return alias

    def mask(self, payload: Any) -> Any:
        """Маскирует результат инструмента (уже приведённый к JSON-совместимым типам)."""
        if not self.enabled:
            return payload
        masked = self._walk(payload)
        # Второй проход: свободный текст (вероятная причина, рекомендация, summary)
        # может содержать те же наименования — подменяем и там.
        return self._replace_in_free_text(masked)

    def unmask(self, text: str) -> str:
        """Возвращает реальные значения в текст ответа пользователю."""
        if not self.enabled or not self._reverse:
            return text
        return _ALIAS_RE.sub(lambda m: self._reverse.get(m.group(0), m.group(0)), text)

    # -- внутреннее ---------------------------------------------------------

    def _walk(self, node: Any, key: str | None = None) -> Any:
        if isinstance(node, dict):
            return {k: self._walk(v, k) for k, v in node.items()}
        if isinstance(node, list):
            return [self._walk(v, key) for v in node]
        if isinstance(node, str) and key is not None and node:
            if key in NEVER_MASK:
                return node
            prefix = SENSITIVE_FIELDS.get(key)
            if prefix:
                return self.alias_for(node, prefix)
        return node

    def _replace_in_free_text(self, node: Any) -> Any:
        if not self._forward:
            return node
        if isinstance(node, dict):
            return {k: self._replace_in_free_text(v) for k, v in node.items()}
        if isinstance(node, list):
            return [self._replace_in_free_text(v) for v in node]
        if isinstance(node, str) and node not in self._reverse:
            result = node
            # Длинные значения заменяем первыми: иначе короткое вхождение,
            # являющееся частью длинного, разорвёт его пополам.
            for real, alias in sorted(self._forward.items(), key=lambda p: -len(p[0])):
                if real and real in result:
                    result = result.replace(real, alias)
            return result
        return node
