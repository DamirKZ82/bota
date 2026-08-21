-- Состояние запроса к агенту: текущий шаг и готовый ответ (ТЗ п.8, индикация).
--
-- Форма «Агент» в 1С опрашивает эту таблицу раз в секунду, пока агент работает.
-- Стриминга нет намеренно: платформа 1С не читает HTTP-ответ потоком, а опрос
-- вдобавок переживает перезапуск оркестратора и работает при нескольких воркерах.
--
-- Здесь лежит текст ответа — он в псевдонимах, как и вся рабочая история:
-- маскирование выполняет расширение 1С (A.0.5).

CREATE TABLE chat_requests (
    request_id      UUID PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
    dialog_id       UUID NOT NULL REFERENCES dialogs (id) ON DELETE CASCADE,
    user_key        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'done', 'failed')),
    step_no         INTEGER NOT NULL DEFAULT 0,
    step_label      TEXT NOT NULL DEFAULT 'Обдумываю ответ',
    tool            TEXT,
    answer          TEXT,
    calls           JSONB NOT NULL DEFAULT '[]'::JSONB,
    error_message   TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    CONSTRAINT chat_requests_finished_consistent
        CHECK ((status = 'running') = (finished_at IS NULL))
);

CREATE INDEX chat_requests_dialog_idx ON chat_requests (dialog_id, started_at DESC);

-- Для поиска зависших запросов: агент упал, а форма ждёт ответа.
CREATE INDEX chat_requests_running_idx
    ON chat_requests (updated_at)
    WHERE status = 'running';

COMMENT ON COLUMN chat_requests.step_label IS
    'Что показывать пользователю прямо сейчас: «сверяю период», «разбираю расхождение»';
