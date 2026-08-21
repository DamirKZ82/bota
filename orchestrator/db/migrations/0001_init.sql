-- Начальная схема оркестратора.
--
-- Принцип, определяющий состав таблиц: в базе оркестратора не должно быть
-- наименований контрагентов, номенклатуры и номеров документов клиента.
-- Поэтому здесь НЕТ:
--   * отображаемой истории диалога — её ведёт форма «Агент» в 1С (ТЗ п.7);
--   * тел планов изменений — план формирует и применяет 1С, здесь только его id;
--   * кэша сверки — он кэшируется в базе 1С (ТЗ п.8).
-- Хранится замаскированная рабочая история (то, что видела модель), метаданные
-- вызовов и агрегаты для метрик пилота.

CREATE TABLE schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Базы 1С (multi-tenant, ТЗ п.3.2)
-- ---------------------------------------------------------------------------

CREATE TABLE tenants (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    -- Сам токен не хранится: только SHA-256. Утечка дампа не даёт доступа к базам.
    token_sha256        BYTEA NOT NULL,
    -- Ключ подписи запросов нужен в открытом виде для HMAC, поэтому лежит
    -- зашифрованным (AES-GCM ключом приложения). Шифрование делается в Python,
    -- а не в SQL: иначе ключ попадёт в текст запроса, а тексты упавших запросов
    -- сохраняются в errors_back.
    signing_key_enc     BYTEA,
    transport           TEXT NOT NULL DEFAULT 'polling'
                        CHECK (transport IN ('direct', 'polling', 'mock')),
    base_url            TEXT,
    masking_enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT tenants_direct_needs_url
        CHECK (transport <> 'direct' OR base_url IS NOT NULL)
);

CREATE UNIQUE INDEX tenants_token_idx ON tenants (token_sha256);

COMMENT ON TABLE tenants IS 'Базы 1С, обслуживаемые оркестратором; каждая изолирована';

-- ---------------------------------------------------------------------------
-- Диалоги
-- ---------------------------------------------------------------------------

CREATE TABLE dialogs (
    id                  UUID PRIMARY KEY,
    tenant_id           TEXT NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    -- Пользователь 1С. Не ФИО, а идентификатор пользователя ИБ.
    user_key            TEXT NOT NULL,
    organization_uuid   TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_activity_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at           TIMESTAMPTZ
);

CREATE INDEX dialogs_tenant_activity_idx
    ON dialogs (tenant_id, last_activity_at DESC);

CREATE TABLE dialog_turns (
    id                  BIGSERIAL PRIMARY KEY,
    dialog_id           UUID NOT NULL REFERENCES dialogs (id) ON DELETE CASCADE,
    -- Денормализация ради правила «tenant_id в условии каждого запроса».
    tenant_id           TEXT NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    seq                 INTEGER NOT NULL,
    role                TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    -- Замаскированный ход в нейтральном формате llm/base.py. Это ровно то,
    -- что видела модель: наименований и БИН здесь нет.
    content             JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX dialog_turns_seq_idx ON dialog_turns (dialog_id, seq);

COMMENT ON COLUMN dialog_turns.content IS
    'Замаскированный ход диалога; отображаемая пользователю история живёт в 1С';

-- Словарь псевдонимов сессии маскирования (ТЗ п.6.4).
--
-- Единственное место, где реальные значения вообще попадают в эту базу, поэтому
-- значение зашифровано ключом приложения, а строка удаляется вместе с диалогом.
-- Без него после перезапуска оркестратора нельзя вернуть пользователю реальные
-- наименования, и диалог пришлось бы начинать заново.
CREATE TABLE dialog_aliases (
    dialog_id           UUID NOT NULL REFERENCES dialogs (id) ON DELETE CASCADE,
    tenant_id           TEXT NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    alias               TEXT NOT NULL,
    value_enc           BYTEA NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (dialog_id, alias)
);

-- ---------------------------------------------------------------------------
-- Журнал (ТЗ п.8: все вызовы и все изменения, хранение 12 месяцев)
-- ---------------------------------------------------------------------------

CREATE TABLE tool_calls (
    id                  BIGSERIAL PRIMARY KEY,
    tenant_id           TEXT NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    dialog_id           UUID REFERENCES dialogs (id) ON DELETE SET NULL,
    user_key            TEXT NOT NULL,
    tool_name           TEXT NOT NULL,
    onec_method         TEXT NOT NULL,
    -- Аргументы приходят от модели, то есть уже в псевдонимах.
    arguments           JSONB NOT NULL DEFAULT '{}'::JSONB,
    ok                  BOOLEAN NOT NULL,
    error_message       TEXT,
    duration_ms         INTEGER NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX tool_calls_tenant_time_idx ON tool_calls (tenant_id, created_at DESC);
CREATE INDEX tool_calls_dialog_idx ON tool_calls (dialog_id);

-- Предложенные планы изменений. Тело плана хранится в 1С — здесь только то,
-- что нужно для журнала и метрик: что предложили и чем закончилось.
CREATE TABLE change_plans (
    plan_id             TEXT PRIMARY KEY,
    tenant_id           TEXT NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    dialog_id           UUID REFERENCES dialogs (id) ON DELETE SET NULL,
    tool_name           TEXT NOT NULL,
    discrepancy_id      TEXT,
    -- Заголовок в псевдонимах — тот же текст, что видел пользователь на кнопке.
    title               TEXT NOT NULL,
    changes_count       INTEGER NOT NULL DEFAULT 0,
    blocked             BOOLEAN NOT NULL DEFAULT FALSE,
    block_reason        TEXT,
    status              TEXT NOT NULL DEFAULT 'proposed'
                        CHECK (status IN ('proposed', 'applied', 'rejected', 'failed', 'expired')),
    -- Кто и когда применил (ТЗ п.5.2: кто, когда, что, по какому расхождению).
    resolved_at         TIMESTAMPTZ,
    resolved_by         TEXT,
    -- Ссылки на созданные агентом документы — чтобы пачку можно было найти и откатить.
    created_documents   JSONB NOT NULL DEFAULT '[]'::JSONB,
    failure_message     TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT change_plans_resolved_consistent
        CHECK ((status = 'proposed') = (resolved_at IS NULL))
);

CREATE INDEX change_plans_tenant_status_idx ON change_plans (tenant_id, status);
CREATE INDEX change_plans_dialog_idx ON change_plans (dialog_id);

-- ---------------------------------------------------------------------------
-- Метрики пилота (ТЗ п.1.3). Только агрегаты, без данных документов.
-- ---------------------------------------------------------------------------

CREATE TABLE reconciliation_runs (
    id                  BIGSERIAL PRIMARY KEY,
    tenant_id           TEXT NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    organization_uuid   TEXT NOT NULL,
    period_from         DATE NOT NULL,
    period_to           DATE NOT NULL,
    pairs_total         INTEGER NOT NULL,
    receipts_total      INTEGER NOT NULL,
    esf_total           INTEGER NOT NULL,
    -- Накопленная разница округлений за период — «хвост» в декларации (ТЗ п.4.4).
    rounding_total      NUMERIC(15, 2) NOT NULL,
    -- [{"code": "D14", "count": 37, "amount_impact": "3.47"}, ...]
    by_code             JSONB NOT NULL DEFAULT '[]'::JSONB,
    duration_ms         INTEGER NOT NULL,
    from_cache          BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT reconciliation_runs_period_order CHECK (period_from <= period_to)
);

CREATE INDEX reconciliation_runs_tenant_time_idx
    ON reconciliation_runs (tenant_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- Очередь обратного транспорта (ТЗ п.3.2, режим поллинга)
-- ---------------------------------------------------------------------------

CREATE TABLE poll_tasks (
    id                  UUID PRIMARY KEY,
    tenant_id           TEXT NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    onec_method         TEXT NOT NULL,
    params              JSONB NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'leased', 'done', 'failed', 'expired')),
    attempts            SMALLINT NOT NULL DEFAULT 0,
    leased_at           TIMESTAMPTZ,
    lease_expires_at    TIMESTAMPTZ,
    result              JSONB,
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ
);

-- Частичный индекс: выборка следующей задачи идёт только по pending.
CREATE INDEX poll_tasks_pending_idx
    ON poll_tasks (tenant_id, created_at)
    WHERE status = 'pending';

-- Для возврата протухших аренд обратно в очередь.
CREATE INDEX poll_tasks_lease_idx
    ON poll_tasks (lease_expires_at)
    WHERE status = 'leased';

COMMENT ON TABLE poll_tasks IS
    'Задачи для фонового задания 1С; выбираются через FOR UPDATE SKIP LOCKED';

-- ---------------------------------------------------------------------------
-- Ошибки и предупреждения (гайдлайны, разделение warn/error)
-- ---------------------------------------------------------------------------

CREATE TABLE errors_back (
    id                  BIGSERIAL PRIMARY KEY,
    tenant_id           TEXT REFERENCES tenants (id) ON DELETE SET NULL,
    message             TEXT NOT NULL,
    traceback           TEXT,
    sql                 TEXT,
    method              TEXT,
    path                TEXT,
    user_key            TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX errors_back_time_idx ON errors_back (created_at DESC);

CREATE TABLE warns (
    id                  BIGSERIAL PRIMARY KEY,
    tenant_id           TEXT REFERENCES tenants (id) ON DELETE SET NULL,
    status_code         SMALLINT NOT NULL,
    message             TEXT NOT NULL,
    method              TEXT,
    path                TEXT,
    user_key            TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX warns_time_idx ON warns (created_at DESC);

-- ---------------------------------------------------------------------------
-- Ретеншн: журнал хранится 12 месяцев (ТЗ п.8), диалоги — заметно меньше.
-- Вызывается по расписанию; срок передаётся параметром из конфига.
-- ---------------------------------------------------------------------------

CREATE FUNCTION bota_purge_expired(
    journal_months INTEGER DEFAULT 12,
    dialog_days    INTEGER DEFAULT 30
) RETURNS TABLE (purged_table TEXT, purged_rows BIGINT)
LANGUAGE plpgsql AS $$
DECLARE
    journal_before TIMESTAMPTZ := now() - make_interval(months => journal_months);
    dialog_before  TIMESTAMPTZ := now() - make_interval(days => dialog_days);
    affected       BIGINT;
BEGIN
    DELETE FROM tool_calls WHERE created_at < journal_before;
    GET DIAGNOSTICS affected = ROW_COUNT;
    RETURN QUERY SELECT 'tool_calls'::TEXT, affected;

    DELETE FROM errors_back WHERE created_at < journal_before;
    GET DIAGNOSTICS affected = ROW_COUNT;
    RETURN QUERY SELECT 'errors_back'::TEXT, affected;

    DELETE FROM warns WHERE created_at < journal_before;
    GET DIAGNOSTICS affected = ROW_COUNT;
    RETURN QUERY SELECT 'warns'::TEXT, affected;

    -- Диалоги вместе с ходами и словарём псевдонимов: чем меньше живёт
    -- расшифровка псевдонимов, тем лучше.
    DELETE FROM dialogs WHERE last_activity_at < dialog_before;
    GET DIAGNOSTICS affected = ROW_COUNT;
    RETURN QUERY SELECT 'dialogs'::TEXT, affected;

    DELETE FROM poll_tasks
     WHERE completed_at IS NOT NULL AND completed_at < dialog_before;
    GET DIAGNOSTICS affected = ROW_COUNT;
    RETURN QUERY SELECT 'poll_tasks'::TEXT, affected;
END;
$$;
