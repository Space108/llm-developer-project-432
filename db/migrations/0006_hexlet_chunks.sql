CREATE TABLE chunks (
    id text PRIMARY KEY,
    doc_id text NOT NULL,
    content text NOT NULL DEFAULT '',
    embedding vector(768),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX chunks_doc_id_idx ON chunks (doc_id);
