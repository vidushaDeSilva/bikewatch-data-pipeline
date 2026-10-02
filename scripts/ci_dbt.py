"""Run isolated dbt checks against controlled PostgreSQL fixtures."""

import json
import subprocess
from datetime import timedelta
from uuid import uuid4

import yaml
from ci_database import ROOT, ci_port, connect_ci, reset_database
from psycopg import sql
from psycopg.types.json import Jsonb
from publish_reporting import (
    check_build_artifacts,
    check_freshness_artifact,
)


def main():
    # Start from a clean CI database so the test is fully isolated.
    reset_database()

    output = ROOT / "artifacts/ci-dbt"
    output.mkdir(parents=True, exist_ok=True)

    with connect_ci() as db:
        now = db.execute("SELECT clock_timestamp()").fetchone()[0]

        # Build a one-hour reporting window aligned to 15-minute buckets.
        end = now.replace(
            minute=(now.minute // 15) * 15,
            second=0,
            microsecond=0,
        )

        start = end - timedelta(hours=1)
        run_id = uuid4()

        # Create a successful pipeline run associated with the fixture data.
        db.execute(
            """
            INSERT INTO ops.pipeline_run (
                run_id,
                started_at,
                finished_at,
                collection_bucket,
                tracked_station_ids,
                status,
                stage
            )
            VALUES (
                %s, %s, %s, %s,
                '["CI_A"]',
                'succeeded',
                'finished'
            )
            """,
            (
                run_id,
                start - timedelta(minutes=2),
                now,
                start,
            ),
        )

        # Helper for inserting controlled raw source records.
        def insert(table, bucket, received, payload):
            db.execute(
                sql.SQL(
                    """
                    INSERT INTO raw.{} (
                        station_id,
                        collection_bucket,
                        collected_at,
                        feed_last_updated,
                        source_url,
                        source_version,
                        run_id,
                        payload
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    """
                ).format(sql.Identifier(table)),
                (
                    "CI_A",
                    bucket,
                    received,
                    received - timedelta(seconds=10),
                    "https://fixture.invalid/feed.json",
                    "1.1",
                    run_id,
                    Jsonb(payload),
                ),
            )

        # Insert station metadata required by the downstream dbt models.
        received = start - timedelta(minutes=1)

        insert(
            "station_metadata",
            received.replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            ),
            received,
            {
                "station_id": "CI_A",
                "name": "Fixture station",
                "lat": 40.75,
                "lon": -73.99,
                "capacity": 10,
            },
        )

        # Insert three of four expected 15-minute observations,
        # deliberately leaving the 30-minute bucket missing.
        for minute, bikes in (
            (0, 0),
            (15, 4),
            (45, 8),
        ):
            bucket = start + timedelta(minutes=minute)
            received = bucket + timedelta(minutes=1)

            insert(
                "station_observation",
                bucket,
                received,
                {
                    "station_id": "CI_A",
                    "num_bikes_available": bikes,
                    "num_docks_available": 10 - bikes,
                    "is_installed": 1,
                    "is_renting": 1,
                    "is_returning": 1,
                    "last_reported": int(received.timestamp()) - 10,
                },
            )

    # Supply deterministic reporting parameters to dbt for this fixture window.
    variables = {
        "reporting_as_of": now.isoformat(),
        "reporting_timezone": "America/New_York",
        "coverage_tracking_periods": [
            {
                "station_id": "CI_A",
                "start_bucket": start.isoformat(),
                "end_bucket": end.isoformat(),
            }
        ],
    }

    # Create an isolated dbt profile that points only to the CI database.
    profile = {
        "bikewatch": {
            "target": "ci",
            "outputs": {
                "ci": {
                    "type": "postgres",
                    "host": "127.0.0.1",
                    "port": ci_port(),
                    "dbname": "bikewatch_test",
                    "user": "bikewatch_transform",
                    "password": "ci_only",
                    "schema": "bw_candidate",
                    "threads": 1,
                    "sslmode": "disable",
                    "connect_timeout": 10,
                }
            },
        }
    }

    (output / "profiles.yml").write_text(yaml.safe_dump(profile))

    target = output / "target"

    # Run dbt commands with the same isolated profile, variables, and artifact paths.
    def run(*args):
        subprocess.run(
            [
                "dbt",
                *args,
                "--project-dir",
                str(ROOT / "dbt_bikewatch"),
                "--profiles-dir",
                str(output),
                "--target",
                "ci",
                "--target-path",
                str(target),
                "--log-path",
                str(output / "logs"),
                "--vars",
                json.dumps(variables),
            ],
            check=True,
            timeout=360,
        )

    # Verify source freshness before building the dbt project.
    run(
        "source",
        "freshness",
        "--select",
        "source:bikewatch_raw",
    )

    check_freshness_artifact(target / "sources.json")

    # Build models/tests and validate the generated dbt artifacts.
    run("build")

    print(
        json.dumps(
            check_build_artifacts(target),
            indent=2,
        )
    )

    # Confirm the fixture produces the expected coverage gap and fact rows.
    with connect_ci() as db:
        counts = db.execute(
            """
            SELECT
                sum(expected_observation_count),
                sum(scheduled_received_count),
                sum(missing_observation_count)
            FROM bw_candidate_analytics.mart_station_hourly
            """
        ).fetchone()

        if counts != (4, 3, 1):
            raise RuntimeError(f"Wrong coverage totals: {counts}; expected (4, 3, 1).")

        count = db.execute(
            """
            SELECT count(*)
            FROM bw_candidate_analytics.fct_station_observation
            """
        ).fetchone()[0]

        if count != 3:
            raise RuntimeError("Fixture must produce exactly three fact observations.")

    print("PASS: four expected buckets, three received, one missing.")


if __name__ == "__main__":
    main()


# Resets the CI database and inserts a known fixture dataset for one fake station, CI_A.
# Creates a one-hour period with 4 expected 15-minute observation buckets, but deliberately inserts only 3 observations, leaving one missing.
# Runs dbt source freshness and dbt build using an isolated CI profile and validates the generated dbt artifacts.
# Checks the final transformed tables to confirm dbt correctly reports:- 4 expected observations
# - 3 received observations
# - 1 missing observation
# - 3 fact-table rows
