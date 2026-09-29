CREATE TABLE documents (
    id text PRIMARY KEY,
    filename text NOT NULL,
    content_hash text NOT NULL UNIQUE,
    status text NOT NULL,
    error text,
    path text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE fragments (
    id text PRIMARY KEY,
    document_id text NOT NULL REFERENCES documents (id),
    page integer NOT NULL,
    section text NOT NULL DEFAULT '',
    article text NOT NULL DEFAULT '',
    brand text NOT NULL DEFAULT '',
    text text NOT NULL,
    position integer NOT NULL
);

CREATE INDEX fragments_document_id_idx ON fragments (document_id);
