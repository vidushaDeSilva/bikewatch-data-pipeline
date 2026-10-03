-- Record daily cleanup and weekly reporting outcomes.
-- Maintenance uses the existing transformation role.

BEGIN;

CREATE TABLE IF NOT EXISTS ops.maintenance_run (
    task text NOT NULL,
    run_key date NOT NULL,
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    status text NOT NULL
        CHECK (status IN ('running', 'succeeded', 'failed')),
    summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (task, run_key)
);

GRANT USAGE ON SCHEMA ops TO bikewatch_transform;

GRANT SELECT, INSERT, UPDATE
ON ops.maintenance_run
TO bikewatch_transform;

COMMIT;