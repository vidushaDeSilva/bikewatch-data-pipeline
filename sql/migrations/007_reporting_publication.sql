BEGIN;

CREATE SCHEMA IF NOT EXISTS reporting_versions;
CREATE SCHEMA IF NOT EXISTS analytics_work;
CREATE SCHEMA IF NOT EXISTS bw_candidate;
CREATE SCHEMA IF NOT EXISTS bw_candidate_analytics;

REVOKE ALL ON SCHEMA
    reporting_versions,
    analytics_work,
    bw_candidate,
    bw_candidate_analytics
FROM PUBLIC;

GRANT USAGE, CREATE ON SCHEMA
    reporting_versions,
    analytics_work,
    bw_candidate,
    bw_candidate_analytics,
    analytics
TO bikewatch_transform;

-- Allow the transformation role to access operational metadata.
GRANT USAGE ON SCHEMA ops TO bikewatch_transform;

-- Track every reporting publication attempt and its outcome.
CREATE TABLE IF NOT EXISTS ops.publication_run (
    run_id uuid PRIMARY KEY,

    started_at timestamptz NOT NULL
        DEFAULT clock_timestamp(),

    finished_at timestamptz,

    reporting_as_of timestamptz NOT NULL,

    status text NOT NULL CHECK (
        status IN (
            'running',
            'blocked',
            'failed',
            'interrupted',
            'published'
        )
    ),

    phase text NOT NULL,
    error_type text,

    check_summary jsonb NOT NULL
        DEFAULT '{}'::jsonb,

    CHECK (
        finished_at IS NULL
        OR finished_at >= started_at
    )
);

-- Store the currently published reporting release and its relation mapping.
CREATE TABLE IF NOT EXISTS ops.reporting_release (
    singleton boolean PRIMARY KEY
        DEFAULT true CHECK (singleton),

    run_id uuid NOT NULL
        REFERENCES ops.publication_run(run_id),

    published_at timestamptz NOT NULL,
    reporting_as_of timestamptz NOT NULL,
    relations jsonb NOT NULL
);

GRANT SELECT, INSERT, UPDATE
ON ops.publication_run, ops.reporting_release
TO bikewatch_transform;

-- Expose the latest publication attempt and currently published release as pipeline health.
CREATE OR REPLACE VIEW analytics.pipeline_health AS
SELECT
    a.run_id AS latest_attempt_id,
    a.status AS latest_attempt_status,
    a.phase AS latest_attempt_phase,
    a.started_at AS latest_attempt_started_at,
    a.finished_at AS latest_attempt_finished_at,
    a.error_type AS latest_attempt_error_type,
    a.check_summary,

    r.run_id AS published_release_id,
    r.published_at,
    r.reporting_as_of AS published_reporting_as_of,
    r.relations AS published_relations

FROM (SELECT 1) anchor

-- Select the most recent publication attempt.
LEFT JOIN LATERAL (
    SELECT *
    FROM ops.publication_run
    ORDER BY started_at DESC, run_id DESC
    LIMIT 1
) a ON true

-- Attach the single currently published release.
LEFT JOIN ops.reporting_release r
    ON r.singleton;

-- Allow the dashboard role to access analytics objects.
GRANT USAGE ON SCHEMA analytics
TO bikewatch_dashboard;

-- Allow the dashboard to read pipeline health information.
GRANT SELECT ON analytics.pipeline_health
TO bikewatch_dashboard;

COMMIT;
