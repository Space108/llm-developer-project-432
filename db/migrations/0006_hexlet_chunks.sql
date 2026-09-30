CREATE TABLE chunks (
    id text PRIMARY KEY,
    doc_id text NOT NULL,
    ordinal integer NOT NULL DEFAULT 0,
    text text NOT NULL DEFAULT '',
    content text NOT NULL DEFAULT '',
    embedding vector,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX chunks_doc_id_idx ON chunks (doc_id);
