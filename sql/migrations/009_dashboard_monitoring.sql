-- Provide safe, read-only operational views for the dashboard.
-- Raw rejected payloads and database credentials are never exposed.

BEGIN;

CREATE OR REPLACE VIEW analytics.dashboard_ingestion_runs
WITH (security_barrier = true)
AS
SELECT
    run_id,
    collection_bucket,
    started_at,
    finished_at,
    status AS run_status,
    stage,
    error_type,

    CASE
        WHEN jsonb_typeof(tracked_station_ids) = 'array'
        THEN jsonb_array_length(tracked_station_ids)
    END AS tracked_station_count,

    extract(epoch FROM finished_at - started_at)
        AS duration_seconds,

    CASE
        WHEN jsonb_typeof(metrics -> 'station_status') = 'object'
         AND jsonb_typeof(
             metrics -> 'station_status' -> 'received'
         ) = 'number'
        THEN (
            metrics -> 'station_status' ->> 'received'
        )::bigint
    END AS received_count,

    CASE
        WHEN jsonb_typeof(metrics -> 'station_status') = 'object'
         AND jsonb_typeof(
             metrics -> 'station_status' -> 'accepted'
         ) = 'number'
        THEN (
            metrics -> 'station_status' ->> 'accepted'
        )::bigint
    END AS accepted_count,

    CASE
        WHEN jsonb_typeof(metrics -> 'station_status') = 'object'
         AND jsonb_typeof(
             metrics -> 'station_status' -> 'duplicate'
         ) = 'number'
        THEN (
            metrics -> 'station_status' ->> 'duplicate'
        )::bigint
    END AS duplicate_count,

    CASE
        WHEN jsonb_typeof(metrics -> 'station_status') = 'object'
         AND jsonb_typeof(
             metrics -> 'station_status' -> 'rejected'
         ) = 'number'
        THEN (
            metrics -> 'station_status' ->> 'rejected'
        )::bigint
    END AS rejected_count,

    CASE
        WHEN jsonb_typeof(metrics -> 'station_status') = 'object'
         AND jsonb_typeof(
             metrics -> 'station_status' -> 'missing'
         ) = 'number'
        THEN (
            metrics -> 'station_status' ->> 'missing'
        )::bigint
    END AS missing_station_count

FROM ops.pipeline_run;


CREATE OR REPLACE VIEW analytics.dashboard_publication_runs
WITH (security_barrier = true)
AS
SELECT
    run_id,
    reporting_as_of,
    started_at,
    finished_at,
    status AS publication_status,
    phase,
    error_type,

    extract(epoch FROM finished_at - started_at)
        AS duration_seconds,

    CASE
        WHEN jsonb_typeof(check_summary -> 'models') = 'number'
        THEN (check_summary ->> 'models')::integer
    END AS model_count,

    CASE
        WHEN jsonb_typeof(check_summary -> 'tests') = 'number'
        THEN (check_summary ->> 'tests')::integer
    END AS test_count,

    CASE
        WHEN jsonb_typeof(check_summary -> 'warnings') = 'array'
        THEN jsonb_array_length(check_summary -> 'warnings')
    END AS warning_count,

    check_summary ->> 'coverage_mode'
        AS coverage_mode,

    check_summary -> 'published_row_counts'
        AS published_row_counts,

    check_summary -> 'freshness_before_build'
        AS freshness_before_build,

    check_summary -> 'freshness_before_publish'
        AS freshness_before_publish

FROM ops.publication_run;


CREATE OR REPLACE VIEW analytics.dashboard_rejection_summary
WITH (security_barrier = true)
AS
SELECT
    rejected.run_id,
    pipeline.collection_bucket,
    pipeline.started_at AS run_started_at,
    rejected.feed_name,
    rejected.reason,
    count(*)::bigint AS rejected_record_count
FROM ops.rejected_record AS rejected
JOIN ops.pipeline_run AS pipeline
    ON pipeline.run_id = rejected.run_id
GROUP BY
    rejected.run_id,
    pipeline.collection_bucket,
    pipeline.started_at,
    rejected.feed_name,
    rejected.reason;


CREATE OR REPLACE VIEW analytics.dashboard_collection_intervals
WITH (security_barrier = true)
AS
WITH bounds AS (
    SELECT
        greatest(
            date_bin(
                interval '15 minutes',
                clock_timestamp() - interval '30 days',
                timestamptz '2000-01-01 00:00:00+00'
            ),
            coalesce(
                (
                    SELECT min(collection_bucket)
                    FROM ops.pipeline_run
                ),
                date_bin(
                    interval '15 minutes',
                    clock_timestamp(),
                    timestamptz '2000-01-01 00:00:00+00'
                )
            )
        ) AS first_bucket,

        date_bin(
            interval '15 minutes',
            clock_timestamp(),
            timestamptz '2000-01-01 00:00:00+00'
        ) AS current_bucket
),

expected AS (
    SELECT generate_series(
        first_bucket,
        current_bucket,
        interval '15 minutes'
    ) AS collection_bucket
    FROM bounds
),

run_rollup AS (
    SELECT
        collection_bucket,
        count(*)::bigint AS run_count,

        bool_or(status = 'succeeded')
            AS has_successful_run,

        bool_or(status = 'partial')
            AS has_partial_run,

        bool_or(status = 'failed')
            AS has_failed_run,

        bool_or(status = 'running')
            AS has_running_run,

        max(started_at)
            AS latest_started_at,

        max(finished_at)
            AS latest_finished_at,

        (
            array_agg(
                status
                ORDER BY started_at DESC, run_id DESC
            )
        )[1] AS latest_run_status

    FROM ops.pipeline_run
    GROUP BY collection_bucket
)

SELECT
    expected.collection_bucket,
    coalesce(runs.run_count, 0) AS run_count,
    runs.latest_started_at,
    runs.latest_finished_at,
    runs.latest_run_status,

    clock_timestamp() - expected.collection_bucket
        AS bucket_age,

    CASE
        WHEN runs.has_successful_run
            THEN 'succeeded'

        WHEN runs.has_partial_run
            THEN 'partial'

        WHEN runs.has_failed_run
            THEN 'failed'

        WHEN runs.has_running_run
            THEN 'running'

        -- The workflow is scheduled seven minutes after the bucket starts.
        -- A further grace period prevents premature missing-run alerts.
        WHEN clock_timestamp()
             < expected.collection_bucket + interval '25 minutes'
            THEN 'awaiting'

        ELSE 'missing'
    END AS interval_status

FROM expected
LEFT JOIN run_rollup AS runs
    USING (collection_bucket);


COMMENT ON VIEW analytics.dashboard_ingestion_runs IS
    'Safe ingestion history for the pipeline-health dashboard.';

COMMENT ON VIEW analytics.dashboard_publication_runs IS
    'Safe publication history without credentials or build artifacts.';

COMMENT ON VIEW analytics.dashboard_rejection_summary IS
    'Aggregated rejected-record counts; source payloads remain private.';

COMMENT ON VIEW analytics.dashboard_collection_intervals IS
    'Expected quarter-hour intervals compared with collector audit rows.';


GRANT USAGE ON SCHEMA analytics
TO bikewatch_dashboard;

GRANT SELECT ON
    analytics.dashboard_ingestion_runs,
    analytics.dashboard_publication_runs,
    analytics.dashboard_rejection_summary,
    analytics.dashboard_collection_intervals
TO bikewatch_dashboard;

COMMIT;