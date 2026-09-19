-- Execute as the database owner on the BikeWatch Neon branch.
BEGIN;
CREATE SCHEMA IF NOT EXISTS analytics;
REVOKE ALL ON SCHEMA analytics FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA analytics TO bikewatch_transform;
GRANT USAGE, CREATE ON SCHEMA staging TO bikewatch_transform;
GRANT USAGE ON SCHEMA raw, ops TO bikewatch_transform;
GRANT SELECT ON raw.station_metadata, raw.station_observation, ops.pipeline_run
    TO bikewatch_transform;
COMMIT;
-- Dashboard grants and atomic publication are a later task.
