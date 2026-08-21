# Bota — Приложение А. Контракты инструментов (1С ↔ оркестратор)

Версия 0.1 — черновик. Дополняет ТЗ v0.2 (раздел 5).

---

## A.0. Общие соглашения

### A.0.1. Транспорт

**Прямой режим.** HTTP-сервис расширения, корень `/hs/bota/v1`.
Единственный метод для вызова инструментов:

```
POST /hs/bota/v1/tools/{tool_name}
Content-Type: application/json; charset=utf-8
Authorization: Bearer <base_token>
X-Bota-Request-Id: <uuid>
X-Bota-Session-Id: <uuid>
X-Bota-Timestamp: <unix_ms>
X-Bota-Signature: <HMAC-SHA256(secret, method + path + timestamp + body)>
```

**Обратный режим (поллинг).** Фоновое задание 1С вызывает оркестратор:

```
GET  /api/v1/bases/{base_id}/tasks?wait=25          → очередь задач (long-poll)
POST /api/v1/bases/{base_id}/tasks/{task_id}/result  → результат
```
Тело задачи и результата — те же объекты `ToolRequest` / `ToolResponse`, что и в прямом режиме. Инструменты об этом не знают.

### A.0.2. Конверт запроса / ответа

```json
// ToolRequest
{
  "tool": "reconcile_period",
  "args": { },
  "context": {
    "user_id": "uuid-пользователя-1С",
    "session_id": "uuid",
    "locale": "ru",
    "masking": true
  }
}
```

```json
// ToolResponse
{
  "ok": true,
  "tool": "reconcile_period",
  "request_id": "uuid",
  "duration_ms": 2140,
  "result": { },
  "warnings": [ { "code": "PARTIAL", "message": "…" } ]
}
```

```json
// ToolResponse при ошибке
{
  "ok": false,
  "tool": "reconcile_period",
  "request_id": "uuid",
  "error": {
    "code": "PERIOD_CLOSED",
    "message": "Период закрыт датой запрета 30.06.2026",
    "details": { "forbid_date": "2026-06-30" },
    "retryable": false
  }
}
```

Коды ошибок (общие): `BAD_ARGS`, `NOT_FOUND`, `ACCESS_DENIED`, `PERIOD_CLOSED`, `LOCKED`, `TIMEOUT`, `LIMIT_EXCEEDED`, `INTERNAL`, `NOT_SUPPORTED_RELEASE`.

### A.0.3. Типы данных

| Тип | Формат | Пример |
|---|---|---|
| `date` | ISO 8601, без времени | `"2026-06-30"` |
| `datetime` | ISO 8601, локальное время базы | `"2026-06-30T14:05:00"` |
| `money` | число, 2 знака, **строкой** (избегаем float) | `"1234.56"` |
| `qty` | число, до 3 знаков, строкой | `"12.500"` |
| `rate` | число, строкой | `"16"` или `"0"` |
| `ref` | ссылка на объект 1С | см. ниже |
| `uuid` | GUID | `"6f1c…"` |
| `bin` | 12 цифр строкой | `"123456789012"` |

**Ссылка на объект (`ref`)** — единый объект везде, где нужен переход в 1С:

```json
{
  "type": "Документ.ПоступлениеТМЗИУслуг",
  "uuid": "6f1c1b4e-…",
  "presentation": "Поступление ТМЗ и услуг 000145 от 14.05.2026",
  "nav": "e1cib/data/Документ.ПоступлениеТМЗИУслуг?ref=…"
}
```

При `masking: true` поле `presentation` заменяется на псевдоним (см. A.0.5), `nav` не меняется — он не уходит в LLM.

**Пагинация.** Списки принимают `page` (с 1) и `page_size` (≤ 200, по умолчанию 50) и возвращают:

```json
{ "items": [ ], "page": 1, "page_size": 50, "total": 137, "has_more": true }
```

### A.0.4. Идентификаторы расхождений

`discrepancy_id` — стабильный в рамках одного расчёта: `sha1(base_id + period + receipt_uuid + esf_uuid + code + line_key)`, первые 16 hex. Повторный расчёт того же периода без изменений даёт те же id — это позволяет помечать «проверено» и не терять пометки.

### A.0.5. Маскирование

Если `context.masking = true`, расширение **само** подменяет перед ответом:

| Поле | Замена |
|---|---|
| Наименование контрагента | `Контрагент-A1`, `Контрагент-A2`… (стабильно в сессии) |
| БИН | `БИН-A1` (соответствие хранится в 1С) |
| Наименование номенклатуры | `Номенклатура-N17` |
| Номер ЭСФ / регистрационный номер | `ЭСФ-E3` |
| `presentation` ссылок | по тем же правилам |

Суммы, даты, ставки, коды — не маскируются. Таблица соответствий живёт в 1С (регистр сведений, ключ — `session_id`) и используется для обратной подстановки в форме. Оркестратор псевдонимов не знает.

---

## A.1. `get_context` — контекст базы

Вызывается агентом первым в каждой сессии.

**Args:** `{}`

**Result:**
```json
{
  "base": {
    "name": "ТОО Ромашка (основная)",
    "config": "Бухгалтерия для Казахстана",
    "config_version": "3.0.50.1",
    "platform": "8.3.24.1548",
    "currency": "KZT",
    "extension_version": "0.3.1"
  },
  "organizations": [
    { "ref": { "type": "Справочник.Организации", "uuid": "…", "presentation": "ТОО Ромашка" },
      "bin": "123456789012",
      "vat_payer": true,
      "forbid_date": "2026-03-31" }
  ],
  "current_period": { "from": "2026-07-01", "to": "2026-09-30", "kind": "quarter" },
  "settings": {
    "line_tolerance_per_unit": "0.01",
    "line_tolerance_max": "1.00",
    "doc_tolerance_per_line": "0.01",
    "doc_tolerance_max": "5.00",
    "period_tolerance_total": "1.00",
    "esf_tail_days": 20,
    "candidate_days": 10
  },
  "permissions": { "read": true, "apply": true, "create": false }
}
```

---

## A.2. `reconcile_period` — сверка периода

Запускает (или берёт из кэша) расчёт по периоду. Тяжёлая операция.

**Args:**
```json
{
  "organization": "uuid",
  "from": "2026-04-01",
  "to": "2026-06-30",
  "counterparty": "uuid | null",
  "force_recalc": false
}
```

**Result:**
```json
{
  "calc_id": "uuid",
  "calculated_at": "2026-08-21T10:14:03",
  "from_cache": false,
  "scope": {
    "receipts_total": 412,
    "receipts_unposted": 3,
    "esf_total": 398,
    "pairs_linked": 371,
    "pairs_suggested": 14,
    "receipts_without_esf": 27,
    "esf_without_receipt": 13
  },
  "summary_by_code": [
    { "code": "D02", "severity": "high", "count": 9,  "amount_vat": "184320.00" },
    { "code": "D03", "severity": "high", "count": 13, "amount_vat": "96010.40" },
    { "code": "D06", "severity": "high", "count": 4,  "amount_vat": "1240.00" },
    { "code": "D11", "severity": "medium", "count": 22, "amount_vat": "0.00" },
    { "code": "D14", "severity": "info", "count": 57, "amount_vat": "3.47" }
  ],
  "rounding": {
    "total_diff_vat": "3.47",
    "total_diff_net": "-1.12",
    "by_pattern": [
      { "pattern": "R1", "count": 41, "diff_vat": "2.90" },
      { "pattern": "R2", "count": 9,  "diff_vat": "0.44" },
      { "pattern": "R6", "count": 7,  "diff_vat": "0.13" }
    ]
  },
  "vat_totals": {
    "receipts_vat": "5241877.12",
    "esf_vat": "5241880.59",
    "diff": "3.47",
    "ledger_vat_1420": "5241877.12"
  },
  "top_discrepancies": [ "d1a2…", "e4f5…" ]
}
```

`top_discrepancies` — до 10 id с наибольшей критичностью/суммой, чтобы агент мог сразу перейти к деталям без запроса списка.

---

## A.3. `list_discrepancies` — список расхождений

**Args:**
```json
{
  "calc_id": "uuid",
  "codes": ["D02", "D06"],
  "severity": ["high", "medium"],
  "counterparty": "uuid | null",
  "pattern": ["R1"],
  "status": ["open", "reviewed", "fixed"],
  "sort": "amount_desc | date_asc | severity",
  "page": 1,
  "page_size": 50
}
```

**Result:** пагинация, `items[]`:
```json
{
  "id": "d1a2b3c4e5f6a7b8",
  "code": "D06",
  "severity": "high",
  "status": "open",
  "pattern": null,
  "counterparty": { "ref": { }, "bin": "…" },
  "receipt": { "ref": { }, "date": "2026-05-14", "number": "000145", "total": "120000.00", "vat": "16551.72" },
  "esf": { "ref": { }, "date": "2026-05-15", "reg_number": "ESF-…", "status": "delivered", "total": "139200.00", "vat": "19200.00" },
  "diff": { "net": "-16551.72", "vat": "-2648.28", "total": "-19200.00" },
  "short": "Цена 1 200,00 ₸ одна и та же, но в поступлении включён флаг «Сумма включает НДС», а в ЭСФ цена без НДС — НДС расходится на 2 648,28 ₸"
}
```

Поле `short` — человекочитаемое резюме, сформированное кодом 1С по шаблону кода расхождения. Агент может использовать его как есть.

---

## A.4. `get_discrepancy` — карточка расхождения

**Args:** `{ "id": "d1a2b3c4e5f6a7b8" }`

**Result:**
```json
{
  "id": "d1a2b3c4e5f6a7b8",
  "code": "D06",
  "severity": "high",
  "status": "open",
  "reviewed": null,
  "pair": {
    "receipt": {
      "ref": { },
      "date": "2026-05-14",
      "number": "000145",
      "posted": true,
      "vat_included_in_price": true,
      "currency": "KZT",
      "totals": { "net": "103448.28", "vat": "16551.72", "total": "120000.00" }
    },
    "esf": {
      "ref": { },
      "date_issue": "2026-05-15",
      "date_turnover": "2026-05-14",
      "reg_number": "ESF-…",
      "status": "delivered",
      "kind": "original | fixed | additional",
      "replaces": null,
      "totals": { "net": "120000.00", "vat": "19200.00", "total": "139200.00" }
    },
    "link": "explicit | suggested | none"
  },
  "header_diff": [
    { "field": "net",   "receipt": "103448.28", "esf": "120000.00", "diff": "-16551.72", "within_tolerance": false },
    { "field": "vat",   "receipt": "16551.72",  "esf": "19200.00",  "diff": "-2648.28",  "within_tolerance": false },
    { "field": "total", "receipt": "120000.00", "esf": "139200.00", "diff": "-19200.00", "within_tolerance": false },
    { "field": "rate",  "receipt": "16", "esf": "16", "diff": null, "within_tolerance": true },
    { "field": "date_turnover", "receipt": "2026-05-14", "esf": "2026-05-14", "diff": 0, "within_tolerance": true }
  ],
  "lines": [
    {
      "match": "matched_with_diff",
      "match_level": 2,
      "confidence": "high",
      "receipt_line": { "n": 1, "item": { "ref": { } }, "name": "Бумага А4 80г", "unit": "пач", "qty": "100.000", "price": "1200.00", "net": "103448.28", "vat_rate": "16", "vat": "16551.72", "total": "120000.00" },
      "esf_line":     { "n": 1, "name": "Бумага офисная А4 80 г/м2", "unit": "пач", "qty": "100.000", "price": "1200.00", "net": "120000.00", "vat_rate": "16", "vat": "19200.00", "total": "139200.00" },
      "diff": { "qty": "0.000", "price": "0.00", "net": "-16551.72", "vat": "-2648.28", "total": "-19200.00" },
      "pattern": "R2"
    }
  ],
  "diagnosis": {
    "pattern": "R2",
    "explanation": "Цена 1 200,00 ₸ в обоих документах одна и та же, но в поступлении включён флаг «Сумма включает НДС» (120 000 / 1,16 = 103 448,28 без НДС), а поставщик выписал ЭСФ с НДС сверху (120 000 × 16 % = 19 200,00). Расходятся и база, и сумма НДС.",
    "confidence": "high"
  },
  "suggested_actions": [
    { "action": "set_vat_mode", "label": "Переключить «Сумма включает НДС» и пересчитать", "risk": "low" },
    { "action": "adjust_lines", "label": "Привести цену и НДС к значениям ЭСФ", "risk": "low" },
    { "action": "mark_reviewed", "label": "Принять как есть", "risk": "none" }
  ]
}
```

`match` ∈ `matched | matched_within_tolerance | matched_with_diff | only_in_receipt | only_in_esf | one_to_many | many_to_one`.

---

## A.5. `get_document` — документ целиком

**Args:** `{ "ref": { "type": "…", "uuid": "…" } }` или `{ "uuid": "…" }` (тип определится)

**Result:** шапка и строки в том же формате, что в A.4 (`receipt` / `esf`), плюс `links[]` — связанные документы с их типом связи.

---

## A.6. `find_esf_candidates` / `find_receipt_candidates`

**Args:**
```json
{ "receipt_uuid": "uuid", "days": 10, "amount_tolerance": "5.00", "limit": 5 }
```

**Result:**
```json
{
  "items": [
    {
      "esf": { "ref": { }, "date_turnover": "2026-05-14", "total": "120000.00", "vat": "16551.72", "counterparty_bin": "…" },
      "score": 0.94,
      "reasons": ["same_bin", "total_equal", "date_diff_1", "lines_similar_0.87"],
      "already_linked_to": null
    }
  ]
}
```
`score` 0–1, детерминированная формула (веса: БИН 0.4, сумма 0.3, дата 0.15, строки 0.15). Зеркальный инструмент принимает `esf_uuid`.

---

## A.7. `get_counterparty` — карточка контрагента

**Args:** `{ "bin": "…" }` или `{ "uuid": "…" }`, опционально `"on_date": "2026-05-14"`

**Result:**
```json
{
  "ref": { },
  "bin": "…",
  "name": "…",
  "vat_payer": true,
  "vat_certificate": { "series": "…", "number": "…", "date_from": "2019-02-01", "date_to": null },
  "vat_status_on_date": "payer | not_payer | deregistered | unknown",
  "main_contract": { "ref": { } },
  "contracts_count": 2,
  "receipts_in_period": 14,
  "esf_in_period": 13,
  "item_mapping_count": 37
}
```

---

## A.8. `get_vat_turnover` — обороты НДС

**Args:** `{ "organization": "uuid", "from": "…", "to": "…" }`

**Result:**
```json
{
  "by_rate": [
    { "rate": "16", "net": "…", "vat": "…", "account": "1420" },
    { "rate": "0",  "net": "…", "vat": "0.00" },
    { "rate": "exempt", "net": "…", "vat": "0.00" }
  ],
  "register_vat_offset": "…",
  "ledger_1420_debit": "…",
  "esf_sum_delivered": "…",
  "diff_ledger_vs_esf": "…"
}
```

---

## A.9. Двухфазные инструменты (запись)

Общая схема:

```
plan_<action>   → возвращает план с plan_id (ничего не меняет, TTL 30 мин)
apply_<action>  → принимает plan_id; вызывается ТОЛЬКО из формы 1С по кнопке пользователя
```

Оркестратор имеет право вызывать `plan_*`. Для `apply_*` расширение проверяет, что вызов пришёл от интерактивного пользователя 1С (`ТекущийПользователь`, не фоновое задание, не HTTP от оркестратора). Попытка `apply_*` через HTTP → `ACCESS_DENIED`.

### A.9.1. `plan_set_link` / `apply_set_link`

**plan args:** `{ "receipt_uuid": "…", "esf_uuid": "…" }`
**plan result:**
```json
{
  "plan_id": "uuid",
  "action": "set_link",
  "expires_at": "…",
  "changes": [
    { "object": { "ref": { } }, "field": "ДокументОснование", "from": null, "to": { "ref": { } } }
  ],
  "checks": [
    { "check": "period_open", "ok": true },
    { "check": "esf_not_linked_elsewhere", "ok": true },
    { "check": "same_counterparty", "ok": true }
  ],
  "can_apply": true
}
```
**apply args:** `{ "plan_id": "uuid" }`
**apply result:** `{ "applied": true, "journal_id": "uuid", "objects": [ { "ref": { } } ] }`

### A.9.2. `plan_adjust_lines` / `apply_adjust_lines`

**plan args:**
```json
{
  "discrepancy_id": "…",
  "strategy": "esf_as_master | receipt_as_master | custom",
  "lines": [ { "n": 1, "price": "1200.00", "qty": null, "vat": "19200.00" } ]
}
```
`lines` обязателен только при `custom`.

**plan result:**
```json
{
  "plan_id": "uuid",
  "action": "adjust_lines",
  "document": { "ref": { } },
  "changes": [
    { "line": 1, "field": "СуммаВключаетНДС", "from": "Да", "to": "Нет" },
    { "line": 1, "field": "Сумма", "from": "103448.28", "to": "120000.00" },
    { "line": 1, "field": "СуммаНДС", "from": "16551.72", "to": "19200.00" }
  ],
  "totals_after": { "net": "120000.00", "vat": "19200.00", "total": "139200.00" },
  "postings_affected": [
    { "account_dt": "1330", "account_kt": "3310", "from": "103448.28", "to": "120000.00" },
    { "account_dt": "1420", "account_kt": "3310", "from": "16551.72", "to": "19200.00" }
  ],
  "will_repost": true,
  "checks": [ ],
  "can_apply": true
}
```

### A.9.3. `plan_set_vat_mode` / `apply_set_vat_mode`

**plan args:** `{ "receipt_uuid": "…", "vat_included": false }`
**plan result:** как A.9.2 — все пересчитанные строки и итоги.

### A.9.4. `plan_create_correction` / `apply_create_correction`

**plan args:** `{ "discrepancy_id": "…", "reason": "…" }`
**plan result:** проект документа «Корректировка поступления» (шапка, строки «было/стало»), `will_post: false`.

### A.9.5. `plan_create_receipt_from_esf` / `apply_create_receipt_from_esf`

**plan args:**
```json
{
  "esf_uuid": "…",
  "warehouse": "uuid | null",
  "contract": "uuid | null",
  "overrides": [ { "esf_line": 2, "item": "uuid" } ]
}
```

**plan result:**
```json
{
  "plan_id": "uuid",
  "action": "create_receipt_from_esf",
  "draft": {
    "organization": { "ref": { } },
    "counterparty": { "ref": { }, "confidence": "high" },
    "contract":     { "ref": { }, "confidence": "high", "alternatives": [] },
    "warehouse":    { "ref": { }, "confidence": "medium", "alternatives": [ { "ref": { } } ] },
    "date": "2026-05-14",
    "vat_included": false,
    "lines": [
      {
        "n": 1,
        "esf_name": "Бумага офисная А4 80 г/м2",
        "item": { "ref": { }, "confidence": "high", "source": "mapping | history | exact | fuzzy | none",
                  "alternatives": [ { "ref": { }, "score": 0.81 } ] },
        "unit": "пач", "qty": "100.000", "price": "1034.40", "vat_rate": "16",
        "account": { "code": "1330", "confidence": "high" },
        "needs_attention": false
      },
      {
        "n": 2,
        "esf_name": "Тонер HP CF283A оригинал",
        "item": { "ref": null, "confidence": "none", "source": "none", "alternatives": [ ] },
        "suggest_new_item": { "name": "Тонер HP CF283A оригинал", "unit": "шт", "vat_rate": "16" },
        "needs_attention": true
      }
    ],
    "totals": { "net": "…", "vat": "…", "total": "…" },
    "attention_count": 1
  },
  "checks": [ { "check": "period_open", "ok": true }, { "check": "esf_not_linked", "ok": true } ],
  "can_apply": false,
  "block_reason": "1 строка требует выбора номенклатуры"
}
```
`can_apply = false`, пока есть строки с `needs_attention = true` без `overrides`. Повторный `plan_*` с `overrides` снимает блок. Подтверждённые соответствия при `apply` пишутся в справочник соответствий.

### A.9.6. `plan_create_receipts_bulk` / `apply_create_receipts_bulk`

**plan args:** `{ "esf_uuids": [ ], "warehouse": "uuid | null" }`
**plan result:** `items[]` с `esf`, `plan_id` (индивидуальный), `attention_count`, `can_apply`. Apply принимает массив `plan_id` — применяются только те, где `can_apply = true`; остальные возвращаются в `skipped[]`.

### A.9.7. `mark_reviewed` (однофазный)

**Args:** `{ "discrepancy_id": "…", "comment": "…", "status": "reviewed | open" }`
Разрешён оркестратору напрямую (не меняет учётные данные). Пишет в регистр пометок.

---

## A.10. Служебные

### `get_settings` / `set_settings`
Объект `settings` из A.1. `set_settings` — только интерактивно (как `apply_*`).

### `open_object`
**Args:** `{ "ref": { } }` → **Result:** `{ "nav": "e1cib/…" }`. Используется формой для кликабельных ссылок.

### `get_journal`
**Args:** `{ "from": "…", "to": "…", "user": "uuid | null", "page": 1 }` → список применённых изменений и созданных документов с `plan_id`, `session_id`, откат-ссылкой.

### `health`
`GET /hs/bota/v1/health` без авторизации → `{ "ok": true, "extension_version": "…", "config_version": "…" }`. Для мониторинга.

---

## A.11. Описания инструментов для LLM (tool definitions)

Оркестратор передаёт модели описания в формате function calling. Исходник описаний — этот документ; ниже шаблон для одного инструмента, остальные — по аналогии.

```json
{
  "name": "reconcile_period",
  "description": "Запускает сверку поступлений и полученных ЭСФ за период по организации. Возвращает сводку по кодам расхождений, накопленную копеечную разницу по паттернам R1–R6 и id самых важных расхождений. Тяжёлая операция (до 90 с), результат кэшируется — не вызывай повторно для того же периода без force_recalc. Всегда вызывай get_context перед первым использованием.",
  "input_schema": {
    "type": "object",
    "properties": {
      "organization": { "type": "string", "description": "uuid организации из get_context" },
      "from": { "type": "string", "format": "date" },
      "to":   { "type": "string", "format": "date" },
      "counterparty": { "type": ["string", "null"], "description": "uuid контрагента для сужения" },
      "force_recalc": { "type": "boolean", "default": false }
    },
    "required": ["organization", "from", "to"]
  }
}
```

Правила для описаний:
- В `description` — когда вызывать, когда НЕ вызывать, что вернётся, стоимость.
- Инструменты `apply_*` модели **не передаются вообще**. Модель видит только `plan_*` и в ответе предлагает пользователю применить.
- `mark_reviewed` передаётся, но системный промпт требует вызывать его только по явной просьбе пользователя.

---

## A.12. Реестр инструментов (сводно)

| Имя | Тип | Кто вызывает | Тяжесть |
|---|---|---|---|
| `get_context` | чтение | LLM | лёгкий |
| `reconcile_period` | чтение/расчёт | LLM | тяжёлый, кэш |
| `list_discrepancies` | чтение | LLM | лёгкий |
| `get_discrepancy` | чтение | LLM | лёгкий |
| `get_document` | чтение | LLM | лёгкий |
| `find_esf_candidates` | чтение | LLM | средний |
| `find_receipt_candidates` | чтение | LLM | средний |
| `get_counterparty` | чтение | LLM | лёгкий |
| `get_vat_turnover` | чтение | LLM | средний |
| `plan_set_link` | план | LLM | лёгкий |
| `plan_adjust_lines` | план | LLM | средний |
| `plan_set_vat_mode` | план | LLM | средний |
| `plan_create_correction` | план | LLM | средний |
| `plan_create_receipt_from_esf` | план | LLM | средний |
| `plan_create_receipts_bulk` | план | LLM | тяжёлый |
| `apply_*` | запись | только форма 1С | — |
| `mark_reviewed` | запись (пометка) | LLM по просьбе | лёгкий |
| `get_settings` / `set_settings` | служебный | форма 1С | — |
| `open_object`, `get_journal`, `health` | служебный | форма / мониторинг | — |

---

## A.13. Версионирование контрактов

- Путь содержит `v1`. Несовместимые изменения → `v2`, старая версия поддерживается минимум 6 месяцев.
- Добавление необязательных полей в `result` — совместимое изменение, версия не меняется.
- Оркестратор при старте сессии сверяет `extension_version` из `get_context` с матрицей совместимости и отключает инструменты, которых нет в старом расширении.

---

## A.14. Открытые вопросы к реализации

1. Точные имена реквизитов связи поступление ↔ ЭСФ в текущем релизе БК 3.0 (уточнить по метаданным пилотных баз).
2. Где хранить кэш расчёта: регистр сведений (переживает перезапуск, нужна очистка) vs временное хранилище сеанса (проще, но теряется). Предложение — регистр с TTL 24 ч и инвалидацией при записи любого поступления/ЭСФ периода.
3. Нужен ли `get_document` для документов вне пары (например, авансовый отчёт) в MVP.
4. Формат `nav` — навигационная ссылка `e1cib/` работает в тонком клиенте; для веб-клиента потребуется отдельная обработка.

---

## A.15. Сознательно исключено из v1

Раздел нужен, чтобы к этим вопросам не возвращаться повторно: перечисленное отсутствует не по недосмотру.

### Отложено до следующих сценариев

| Инструмент | Есть в | Почему не в v1 |
|---|---|---|
| `get_change_history` (`ПолучитьИсториюИзменений`) | ТЗ v0.2, п. 5.1 | Зависит от включённого версионирования объектов, а у большинства клиентов аутсорсинга оно выключено — раздувает базу; в половине баз инструмент возвращал бы пустоту. Первому сценарию история не нужна: сверка, привязка и создание документов смотрят на текущее состояние. Реальная польза — ответ на «кто и когда это поменял» при разборе уведомлений КГД, то есть сценарии 3–4. |

История действий самой Bota этим **не затрагивается**: она обязательна и покрыта инструментом `get_journal` (A.10) — бухгалтер должен видеть, что сделал агент, и чем это откатить.

### Доступно только интерактивно, агенту не выдаётся

| Инструмент | Причина |
|---|---|
| `apply_*` (все) | Применение изменений инициирует пользователь кнопкой в форме 1С. Расширение проверяет, что вызов пришёл от интерактивного пользователя, а вызов по HTTP отвергает с `ACCESS_DENIED` (A.9). |
| `set_settings` | Меняет допуски и режим маскирования — то, на чём строится вся классификация расхождений. Правит бухгалтер, не агент (A.10). |

В реестре инструментов оркестратора этих имён нет вовсе: модель не знает об их существовании. Это второй независимый рубеж поверх проверки в расширении.
