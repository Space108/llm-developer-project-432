CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);
