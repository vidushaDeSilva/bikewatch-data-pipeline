BEGIN;

CREATE TABLE ops.pipeline_run (
    run_id uuid PRIMARY KEY,
    status text NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'succeeded', 'partial', 'failed')),
    stage text NOT NULL DEFAULT 'starting',
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    collection_bucket timestamptz NOT NULL,
    tracked_station_ids jsonb NOT NULL,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_type text
);

CREATE TABLE ops.rejected_record (
    rejection_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES ops.pipeline_run(run_id),
    feed_name text NOT NULL,
    reason text NOT NULL,
    payload jsonb NOT NULL,
    rejected_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE raw.station_metadata (
    station_id text NOT NULL,
    collection_bucket timestamptz NOT NULL,
    collected_at timestamptz NOT NULL,
    feed_last_updated timestamptz NOT NULL,
    source_url text NOT NULL,
    source_version text NOT NULL,
    run_id uuid NOT NULL REFERENCES ops.pipeline_run(run_id),
    payload jsonb NOT NULL,
    PRIMARY KEY (station_id, collection_bucket)
);

CREATE TABLE raw.station_observation (
    station_id text NOT NULL,
    collection_bucket timestamptz NOT NULL,
    collected_at timestamptz NOT NULL,
    feed_last_updated timestamptz NOT NULL,
    source_url text NOT NULL,
    source_version text NOT NULL,
    run_id uuid NOT NULL REFERENCES ops.pipeline_run(run_id),
    payload jsonb NOT NULL,
    PRIMARY KEY (station_id, collection_bucket)
);

CREATE INDEX pipeline_run_started_idx
ON ops.pipeline_run (started_at DESC);

CREATE INDEX rejected_record_run_idx
ON ops.rejected_record (run_id);

-- Narrow the earlier blanket defaults for future operational tables.
ALTER DEFAULT PRIVILEGES IN SCHEMA ops
REVOKE SELECT, INSERT, UPDATE ON TABLES FROM bikewatch_ingest;

ALTER DEFAULT PRIVILEGES IN SCHEMA ops
REVOKE USAGE ON SEQUENCES FROM bikewatch_ingest;

ALTER DEFAULT PRIVILEGES IN SCHEMA ops
REVOKE SELECT ON TABLES FROM bikewatch_transform;

-- Grant access specifically to these operational tables.
GRANT SELECT, INSERT, UPDATE
ON ops.pipeline_run
TO bikewatch_ingest;

GRANT INSERT
ON ops.rejected_record
TO bikewatch_ingest;

GRANT USAGE
ON SEQUENCE ops.rejected_record_rejection_id_seq
TO bikewatch_ingest;

GRANT SELECT, INSERT
ON raw.station_metadata, raw.station_observation
TO bikewatch_ingest;

GRANT SELECT
ON raw.station_metadata, raw.station_observation, ops.pipeline_run
TO bikewatch_transform;

COMMIT;