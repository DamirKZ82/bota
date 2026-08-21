-- База знаний для RAG (ТЗ п.6.3): правила выписки ЭСФ, зачёт НДС по НК РК
-- (ст. 400–403), типовые причины расхождений в 1С.
--
-- Вынесено отдельной миграцией по двум причинам:
--   1. требует расширения pgvector, которого может не быть на площадке;
--   2. размерность вектора зависит от модели эмбеддингов, а она ещё не выбрана
--      (Claude API эмбеддинги не отдаёт — нужен отдельный провайдер либо
--      локальная модель, что связано с открытым вопросом 5 раздела 10 ТЗ).
--
-- Здесь нет данных клиентов: это общие нормативные тексты, одни для всех баз.
-- Если размерность изменится, миграцию заменяем целиком — таблицы пересоздаются
-- вместе с переиндексацией корпуса.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE knowledge_documents (
    id              BIGSERIAL PRIMARY KEY,
    title           TEXT NOT NULL,
    -- Ссылка на источник для блока «на что опирался ответ»: «НК РК, ст. 401 п. 1».
    source_ref      TEXT NOT NULL,
    url             TEXT,
    -- Дата редакции нормативного акта: правила зачёта НДС меняются, и ответ
    -- обязан опираться на редакцию, действовавшую в проверяемом периоде.
    effective_from  DATE NOT NULL,
    effective_to    DATE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT knowledge_documents_period_order
        CHECK (effective_to IS NULL OR effective_from <= effective_to)
);

CREATE INDEX knowledge_documents_effective_idx
    ON knowledge_documents (effective_from, effective_to);

CREATE TABLE knowledge_chunks (
    id              BIGSERIAL PRIMARY KEY,
    document_id     BIGINT NOT NULL REFERENCES knowledge_documents (id) ON DELETE CASCADE,
    ordinal         INTEGER NOT NULL,
    content         TEXT NOT NULL,
    -- Точная ссылка на пункт внутри документа — она попадает в ответ агента.
    anchor          TEXT,
    embedding       VECTOR(1024),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX knowledge_chunks_ordinal_idx ON knowledge_chunks (document_id, ordinal);

-- HNSW по косинусному расстоянию: корпус маленький и меняется редко,
-- скорость поиска важнее стоимости построения.
CREATE INDEX knowledge_chunks_embedding_idx
    ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);
