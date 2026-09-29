-- Размерность 768 и косинус — карточка google/embeddinggemma-300m. Метрика дальше не меняется.
ALTER TABLE fragments
    ADD COLUMN embedding vector(768),
    ADD COLUMN search_tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('russian', text)) STORED;

CREATE INDEX fragments_embedding_hnsw_idx
    ON fragments USING hnsw (embedding vector_cosine_ops);

CREATE INDEX fragments_search_tsv_idx
    ON fragments USING gin (search_tsv);
