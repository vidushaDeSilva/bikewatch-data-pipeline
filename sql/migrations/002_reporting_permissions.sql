BEGIN;

-- Run while connected as bikewatch_transform.
-- Future reporting tables/views created by this role
-- will automatically be readable by the dashboard.
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics
GRANT SELECT ON TABLES
TO bikewatch_dashboard;

COMMIT;