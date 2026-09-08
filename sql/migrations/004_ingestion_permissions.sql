BEGIN;

-- Run as the same database owner that created the tables.
-- Earlier results identified this role as neondb_owner.

GRANT USAGE ON SCHEMA raw, ops TO bikewatch_ingest;
GRANT USAGE ON SCHEMA raw, ops TO bikewatch_transform;

-- Remove earlier broad defaults for future ingestion tables.
-- These statements affect objects created by the executing role.

ALTER DEFAULT PRIVILEGES IN SCHEMA raw
REVOKE SELECT, INSERT, UPDATE ON TABLES FROM bikewatch_ingest;

ALTER DEFAULT PRIVILEGES IN SCHEMA ops
REVOKE SELECT, INSERT, UPDATE ON TABLES FROM bikewatch_ingest;

ALTER DEFAULT PRIVILEGES IN SCHEMA ops
REVOKE USAGE ON SEQUENCES FROM bikewatch_ingest;

ALTER DEFAULT PRIVILEGES IN SCHEMA ops
REVOKE SELECT ON TABLES FROM bikewatch_transform;

-- Reset direct grants on existing tables.

REVOKE ALL PRIVILEGES
ON TABLE
    raw.station_metadata,
    raw.station_observation,
    ops.pipeline_run,
    ops.rejected_record
FROM bikewatch_ingest;

REVOKE ALL PRIVILEGES
ON SEQUENCE ops.rejected_record_rejection_id_seq
FROM bikewatch_ingest;

REVOKE ALL PRIVILEGES
ON TABLE ops.pipeline_run, ops.rejected_record
FROM bikewatch_transform;

-- Grant exactly what the current collector uses.

GRANT SELECT, INSERT
ON TABLE raw.station_metadata, raw.station_observation
TO bikewatch_ingest;

GRANT SELECT, INSERT, UPDATE
ON TABLE ops.pipeline_run
TO bikewatch_ingest;

GRANT INSERT
ON TABLE ops.rejected_record
TO bikewatch_ingest;

GRANT USAGE
ON SEQUENCE ops.rejected_record_rejection_id_seq
TO bikewatch_ingest;

GRANT SELECT
ON TABLE
    raw.station_metadata,
    raw.station_observation,
    ops.pipeline_run
TO bikewatch_transform;

COMMIT;