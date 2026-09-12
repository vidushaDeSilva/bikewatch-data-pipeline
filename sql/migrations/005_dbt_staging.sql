BEGIN;

CREATE SCHEMA IF NOT EXISTS staging;

REVOKE ALL ON SCHEMA staging FROM PUBLIC;

GRANT USAGE, CREATE
ON SCHEMA staging
TO bikewatch_transform;

GRANT USAGE
ON SCHEMA raw, ops
TO bikewatch_transform;

GRANT SELECT
ON TABLE
    raw.station_metadata,
    raw.station_observation,
    ops.pipeline_run
TO bikewatch_transform;

COMMIT;