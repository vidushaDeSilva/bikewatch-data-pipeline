"""Test the real collector using controlled feeds and PostgreSQL."""

import json
import os
from collections import Counter
from datetime import datetime, timezone

import pytest

from bikewatch.ingestion import collect as collector
from scripts.ci_database import ci_port, connect_ci


@pytest.fixture
def feeds(tmp_path, monkeypatch):
    if os.getenv("APP_ENV") != "ci":
        pytest.skip("Requires disposable CI PostgreSQL.")

    # Point the real collector to the local CI PostgreSQL database
    monkeypatch.setenv(
        "BIKEWATCH_DATABASE_URL",
        "postgresql://bikewatch_ingest:ci_only"
        f"@127.0.0.1:{ci_port()}/bikewatch_test?sslmode=disable",
    )

    with connect_ci() as db:
        db.execute(
            """
            TRUNCATE
                raw.station_metadata,
                raw.station_observation,
                ops.rejected_record,
                ops.pipeline_run
            CASCADE
            """
        )

    # Three stations used by the controlled test feed.
    ids = ["A", "B", "C"]

    path = tmp_path / "tracked_stations.json"
    path.write_text(json.dumps(ids))

    # Freeze time so every test produces deterministic timestamps/buckets.
    stamp = datetime(
        2026,
        1,
        1,
        12,
        7,
        tzinfo=timezone.utc,
    )

    monkeypatch.setattr(collector, "STATIONS_FILE", path)
    monkeypatch.setattr(collector, "now_utc", lambda: stamp)

    monkeypatch.setattr(
        collector,
        "discover",
        lambda *_: {
            "station_information": "information",
            "station_status": "status",
        },
    )

    # Fake station metadata returned by station_information.
    information = [
        {
            "station_id": station,
            "name": station,
            "lat": 40.75,
            "lon": -73.99,
            "capacity": 10,
        }
        for station in ids
    ]

    # Fake station status returned by station_status.
    status = [
        {
            "station_id": station,
            "num_bikes_available": 4,
            "num_docks_available": 6,
            "is_installed": 1,
            "is_renting": 1,
            "is_returning": 1,
            "last_reported": int(stamp.timestamp()),
        }
        for station in ids
    ]

    # Map the fake feed URLs to their station data.
    payloads = {
        "information": information,
        "status": status,
    }

    calls = Counter()

    def fetch(_client, url):
        # Record each request made by the collector.
        calls[url] += 1

        # Return a controlled GBFS-like response.
        return {
            "last_updated": int(stamp.timestamp()),
            "ttl": 60,
            "version": "1.1",
            "data": {
                "stations": payloads[url],
            },
        }

    monkeypatch.setattr(collector, "fetch_json", fetch)

    return payloads, calls


def test_duplicate_bucket_and_daily_metadata_reuse(feeds):
    """Verify duplicate observations are ignored and metadata is reused."""

    _, calls = feeds

    # Run the collector twice at the exact same mocked time.
    assert collector.collect(None, "fixture") == 0
    assert collector.collect(None, "fixture") == 0

    assert calls == {
        "information": 1,
        "status": 2,
    }

    with connect_ci() as db:
        # Only one observation per station should exist for this bucket.
        count = db.execute("SELECT count(*) FROM raw.station_observation").fetchone()[0]

        assert count == 3

        metrics = db.execute("SELECT metrics FROM ops.pipeline_run").fetchall()

        duplicates = sorted(row[0]["station_status"]["duplicate"] for row in metrics)

        assert duplicates == [0, 3]


def test_rejected_and_missing_records_are_counted(feeds):
    """Verify invalid and missing station-status records are counted."""

    payloads, _ = feeds

    # Make station B invalid by giving it a negative bike count.
    payloads["status"][1]["num_bikes_available"] = -1

    # Remove station C completely from the status feed.
    payloads["status"].pop()

    # Collector returns non-zero because the run is only partially successful.
    assert collector.collect(None, "fixture") == 1

    with connect_ci() as db:
        status, metrics = db.execute("SELECT status, metrics FROM ops.pipeline_run").fetchone()

        # One invalid row and one missing station should produce a partial run.
        assert status == "partial"
        assert metrics["station_status"]["rejected"] == 1
        assert metrics["station_status"]["missing"] == 1

        # The invalid station should also be written to rejected_record.
        rejected = db.execute("SELECT count(*) FROM ops.rejected_record").fetchone()[0]

        assert rejected == 1


def test_status_batch_rolls_back_but_audit_and_metadata_survive(feeds):
    """Verify a status insert failure rolls back only the status batch."""

    with connect_ci() as db:
        db.execute(
            """
            CREATE FUNCTION raw.ci_fail()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF NEW.station_id = 'B' THEN
                    RAISE EXCEPTION 'fixture failure';
                END IF;

                RETURN NEW;
            END
            $$;

            -- Run the failure function before every observation insert.
            CREATE TRIGGER ci_fail
            BEFORE INSERT ON raw.station_observation
            FOR EACH ROW
            EXECUTE FUNCTION raw.ci_fail();
            """
        )

        try:
            # Station B causes the observation batch to fail.
            assert collector.collect(None, "fixture") == 1

            observations = db.execute("SELECT count(*) FROM raw.station_observation").fetchone()[0]

            # Metadata was committed separately and should survive.
            metadata = db.execute("SELECT count(*) FROM raw.station_metadata").fetchone()[0]

            assert observations == 0
            assert metadata == 3

            # The audit record should still show that the pipeline failed.
            status, metrics = db.execute("SELECT status, metrics FROM ops.pipeline_run").fetchone()

            assert status == "failed"

            # Metadata stage completed successfully before the failure.
            assert "station_information" in metrics

            # Status stage did not complete successfully.
            assert "station_status" not in metrics

        finally:
            db.execute(
                """
                DROP TRIGGER ci_fail
                    ON raw.station_observation;

                DROP FUNCTION raw.ci_fail();
                """
            )
