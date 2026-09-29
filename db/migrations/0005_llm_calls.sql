CREATE TABLE llm_calls (
    id text PRIMARY KEY,
    job_id text,
    request_id text,
    model text NOT NULL,
    prompt_tokens integer NOT NULL DEFAULT 0,
    completion_tokens integer NOT NULL DEFAULT 0,
    cost numeric(18, 8) NOT NULL,
    duration_ms integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX llm_calls_job_id_idx ON llm_calls (job_id);
CREATE INDEX llm_calls_model_idx ON llm_calls (model);
