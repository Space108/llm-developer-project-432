CREATE TABLE jobs (
    id text PRIMARY KEY,
    idempotency_key text UNIQUE,
    status text NOT NULL DEFAULT 'pending',
    payload jsonb NOT NULL,
    result jsonb,
    attempts integer NOT NULL DEFAULT 0,
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
