"""Create the disposable database contract used by CI."""

import os
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

ROOT = Path(__file__).resolve().parents[1]


def ci_port():
    port = int(os.getenv("CI_DB_PORT", "5433"))

    if not 1 <= port <= 65535:
        raise ValueError("CI_DB_PORT must be between 1 and 65535.")

    return port

def connect_ci():
    dsn = os.environ["TEST_DATABASE_URL"]
    info = conninfo_to_dict(dsn)
    port = ci_port()

    if (
        os.getenv("APP_ENV") != "ci"
        or info.get("host") != "127.0.0.1"
        or info.get("port") != str(port)
        or info.get("dbname") != "bikewatch_test"
    ):
        raise RuntimeError(
            f"CI requires APP_ENV=ci and "
            f"127.0.0.1:{port}/bikewatch_test."
        )

    return psycopg.connect(
        dsn,
        autocommit=True,
        connect_timeout=10,
    )


def reset_database():
    with connect_ci() as db:
        for role in (
            "bikewatch_ingest",
            "bikewatch_transform",
            "bikewatch_dashboard",
        ):
            exists = db.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s",
                (role,),
            ).fetchone()

            if not exists:
                db.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role)))

            db.execute(sql.SQL("ALTER ROLE {} PASSWORD 'ci_only'").format(sql.Identifier(role)))

        db.execute(
            """
            DROP SCHEMA IF EXISTS
                raw,
                ops,
                staging,
                analytics,
                analytics_work,
                bw_candidate,
                bw_candidate_analytics,
                reporting_versions
            CASCADE;

            CREATE SCHEMA raw;
            CREATE SCHEMA ops;
            CREATE SCHEMA analytics;
            CREATE SCHEMA staging;

            CREATE TABLE ops.pipeline_run (
                run_id uuid PRIMARY KEY,
                started_at timestamptz NOT NULL DEFAULT now(),
                finished_at timestamptz,
                collection_bucket timestamptz NOT NULL,
                tracked_station_ids jsonb NOT NULL,
                status text NOT NULL DEFAULT 'running',
                stage text NOT NULL DEFAULT 'starting',
                metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
                error_type text
            );

            CREATE TABLE ops.rejected_record (
                rejected_id bigint
                    GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                run_id uuid NOT NULL REFERENCES ops.pipeline_run,
                feed_name text NOT NULL,
                reason text NOT NULL,
                payload jsonb NOT NULL
            );

            CREATE TABLE raw.station_metadata (
                station_id text NOT NULL,
                collection_bucket timestamptz NOT NULL,
                collected_at timestamptz NOT NULL,
                feed_last_updated timestamptz NOT NULL,
                source_url text NOT NULL,
                source_version text NOT NULL,
                run_id uuid NOT NULL REFERENCES ops.pipeline_run,
                payload jsonb NOT NULL,
                PRIMARY KEY (station_id, collection_bucket)
            );

            CREATE TABLE raw.station_observation (
                LIKE raw.station_metadata INCLUDING ALL,
                FOREIGN KEY (run_id) REFERENCES ops.pipeline_run
            );

            GRANT USAGE ON SCHEMA raw, ops
                TO bikewatch_ingest, bikewatch_transform;

            GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA raw
                TO bikewatch_ingest;

            GRANT SELECT, INSERT, UPDATE ON ops.pipeline_run
                TO bikewatch_ingest;

            GRANT INSERT ON ops.rejected_record
                TO bikewatch_ingest;

            GRANT USAGE ON ALL SEQUENCES IN SCHEMA ops
                TO bikewatch_ingest;

            GRANT SELECT ON ALL TABLES IN SCHEMA raw, ops
                TO bikewatch_transform;

            GRANT USAGE, CREATE ON SCHEMA staging, analytics
                TO bikewatch_transform;
            """
        )

        migration = ROOT / "sql/migrations/007_reporting_publication.sql"

        db.execute(migration.read_text())


if __name__ == "__main__":
    reset_database()


# Verify this is the safe local CI DB
#             ↓
# Create required PostgreSQL roles
#             ↓
# Delete old test schemas/data
#             ↓
# Recreate raw + ops + staging + analytics
#             ↓
# Create ingestion tables
#             ↓
# Apply role permissions
#             ↓
# Run 007_reporting_publication.sql
#             ↓
# Clean database ready for CI tests

# This script prepares a fresh local BikeWatch database for CI so your ingestion, dbt, permissions, migrations,
# and publication logic can be tested safely and consistently without touching Neon.
