CREATE TABLE IF NOT EXISTS tasks (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    record_id    BIGINT      NOT NULL REFERENCES donations (id),
    status       VARCHAR(8)  NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
    seed         BIGINT      NOT NULL,
    attempts     INTEGER     NOT NULL DEFAULT 0,
    max_attempts INTEGER     NOT NULL DEFAULT 3,
    token        BIGINT      NOT NULL DEFAULT 0,
    lease_until  TIMESTAMPTZ,
    worker_id    TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS tasks_status_created_at_idx ON tasks (status, created_at);

CREATE TABLE IF NOT EXISTS task_results (
    task_id    BIGINT      PRIMARY KEY REFERENCES tasks (id),
    token      BIGINT      NOT NULL,
    worker_id  TEXT        NOT NULL,
    result     JSONB       NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
