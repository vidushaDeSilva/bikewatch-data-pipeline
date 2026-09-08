import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from dotenv import load_dotenv
from psycopg import sql
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from bikewatch.ingestion.gbfs import discover, fetch_json
from bikewatch.ingestion.models import (
    StationEnvelope,
    StationInformation,
    StationStatus,
)


STATIONS_FILE = Path("config/tracked_stations.json")

FEED_MODELS = {
    "station_information": StationInformation,
    "station_status": StationStatus,
}

FEED_TABLES = {
    "station_information": "station_metadata",
    "station_status": "station_observation",
}


def now_utc():
    return datetime.now(UTC)


def load_station_ids():
    values = json.loads(STATIONS_FILE.read_text())

    if (
        not isinstance(values, list)
        or not 1 <= len(values) <= 20
        or any(not isinstance(value, str) or not value for value in values)
        or len(set(values)) != len(values)
    ):
        raise ValueError(
            "Configure 1–20 distinct, non-empty station ID strings"
        )

    return set(values)


def set_stage(db, run_id, stage):
    db.execute(
        "UPDATE ops.pipeline_run SET stage = %s WHERE run_id = %s",
        (stage, run_id),
    )


def quarantine(db, run_id, feed_name, reason, payload):
    db.execute(
        """
        INSERT INTO ops.rejected_record
            (run_id, feed_name, reason, payload)
        VALUES (%s, %s, %s, %s)
        """,
        (run_id, feed_name, reason, Jsonb(payload)),
    )


def load_feed(db, client, run_id, feed_name, url, bucket, tracked):
    payload = fetch_json(client, url)
    collected_at = now_utc()

    # An invalid envelope fails the feed/run, rather than being
    # misinterpreted as an empty successful collection.
    envelope = StationEnvelope.model_validate(payload)
    source_time = datetime.fromtimestamp(envelope.last_updated, UTC)
    records = envelope.data.stations

    counts = {
        "received": len(records),
        "ignored": 0,
        "valid": 0,
        "accepted": 0,
        "duplicate": 0,
        "rejected": 0,
        "missing": 0,
    }

    seen = set()

    insert = sql.SQL(
        """
        INSERT INTO raw.{} (
            station_id, collection_bucket, collected_at,
            feed_last_updated, source_url, source_version,
            run_id, payload
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (station_id, collection_bucket) DO NOTHING
        RETURNING station_id
        """
    ).format(sql.Identifier(FEED_TABLES[feed_name]))

    # Valid rows, rejected rows, and feed metrics commit together.
    with db.transaction():
        for record in records:
            station_id = (
                record.get("station_id")
                if isinstance(record, dict)
                else None
            )

            if not isinstance(station_id, str) or not station_id:
                counts["rejected"] += 1
                quarantine(
                    db, run_id, feed_name, "missing_station_id", record
                )
                continue

            if station_id not in tracked:
                counts["ignored"] += 1
                continue

            if station_id in seen:
                counts["rejected"] += 1
                quarantine(
                    db, run_id, feed_name, "repeated_station_id", record
                )
                continue

            seen.add(station_id)

            try:
                validated = FEED_MODELS[feed_name].model_validate(record)
            except ValidationError as exc:
                counts["rejected"] += 1

                # Record field paths and error types without echoing
                # input values into application logs.
                reason = "; ".join(
                    f"{'.'.join(map(str, error['loc']))}:{error['type']}"
                    for error in exc.errors(
                        include_input=False,
                        include_url=False,
                        include_context=False,
                    )
                )

                quarantine(db, run_id, feed_name, reason, record)
                continue

            counts["valid"] += 1

            inserted = db.execute(
                insert,
                (
                    validated.station_id,
                    bucket,
                    collected_at,
                    source_time,
                    url,
                    envelope.version,
                    run_id,
                    Jsonb(record),
                ),
            ).fetchone()

            key = "accepted" if inserted else "duplicate"
            counts[key] += 1

        counts["missing"] = len(tracked - seen)

        # Every valid record is either newly inserted or already stored.
        if counts["valid"] != (
            counts["accepted"] + counts["duplicate"]
        ):
            raise RuntimeError("Invalid valid-record accounting")

        # Every source entry must have exactly one processing outcome.
        # Missing stations are absent from the response, so they are
        # deliberately excluded from this equation.
        if counts["received"] != (
            counts["ignored"]
            + counts["rejected"]
            + counts["valid"]
        ):
            raise RuntimeError("Invalid source-record accounting")

        db.execute(
            """
            UPDATE ops.pipeline_run
            SET metrics = metrics || %s
            WHERE run_id = %s
            """,
            (Jsonb({feed_name: counts}), run_id),
        )

    # The transaction has successfully committed before this log.
    # If loading or committing raises an exception, this is skipped.
    print(json.dumps({
        "event": "batch_committed",
        "logged_at": now_utc().isoformat(),
        "run_id": str(run_id),
        "feed": feed_name,
        "collection_bucket": bucket.isoformat(),
        "counts": counts,
    }), flush=True)

    return counts


def initialize_stations(client, discovery_url):
    """One-time configuration utility, not a collection run."""
    if STATIONS_FILE.exists():
        raise ValueError(
            "Station configuration already exists; inspect it before changing it"
        )

    feeds = discover(client, discovery_url)
    payload = fetch_json(client, feeds["station_information"])
    envelope = StationEnvelope.model_validate(payload)

    candidates = {}
    for record in envelope.data.stations:
        try:
            station = StationInformation.model_validate(record)
        except ValidationError:
            continue

        # A small Midtown Manhattan area for an understandable portfolio scope.
        if (
            40.750 <= station.lat <= 40.770
            and -73.995 <= station.lon <= -73.975
            and station.capacity > 0
        ):
            candidates[station.station_id] = station

    # Stable selection near Times Square, saved once instead of
    # selecting a different population on every collection.
    selected = sorted(
        candidates.values(),
        key=lambda station: (
            (station.lat - 40.7580) ** 2
            + ((station.lon + 73.9855) * 0.76) ** 2,
            station.station_id,
        ),
    )[:20]

    if len(selected) != 20:
        raise ValueError("Fewer than 20 eligible stations; review selection bounds")

    STATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with STATIONS_FILE.open("x") as output:
        json.dump(
            [station.station_id for station in selected],
            output,
            indent=2,
        )
        output.write("\n")

    for station in selected:
        print(f"{station.station_id} | {station.name}")


def collect(client, discovery_url):
    tracked = load_station_ids()
    started_at = now_utc()
    run_id = uuid4()

    bucket = started_at.replace(
        minute=(started_at.minute // 15) * 15,
        second=0,
        microsecond=0,
    )
    metadata_bucket = started_at.replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    # Autocommit persists the run and stage markers independently.
    # Explicit transactions below protect each data batch.
    with psycopg.connect(
        os.environ["BIKEWATCH_DATABASE_URL"],
        autocommit=True,
        connect_timeout=20,
    ) as db:
        db.execute(
            """
            INSERT INTO ops.pipeline_run (
                run_id, started_at, collection_bucket, tracked_station_ids
            )
            VALUES (%s, %s, %s, %s)
            """,
            (run_id, started_at, bucket, Jsonb(sorted(tracked))),
        )

        try:
            set_stage(db, run_id, "discovering")
            feeds = discover(client, discovery_url)

            set_stage(db, run_id, "checking_metadata")
            existing = {
                row[0]
                for row in db.execute(
                    """
                    SELECT station_id
                    FROM raw.station_metadata
                    WHERE collection_bucket = %s
                      AND station_id = ANY(%s)
                    """,
                    (metadata_bucket, sorted(tracked)),
                )
            }

            metadata_counts = None
            if not tracked.issubset(existing):
                set_stage(db, run_id, "collecting_metadata")
                metadata_counts = load_feed(
                    db, client, run_id,
                    "station_information",
                    feeds["station_information"],
                    metadata_bucket,
                    tracked,
                )

            set_stage(db, run_id, "collecting_status")
            status_counts = load_feed(
                db, client, run_id,
                "station_status",
                feeds["station_status"],
                bucket,
                tracked,
            )

            summaries = [status_counts]
            if metadata_counts is not None:
                summaries.append(metadata_counts)

            if status_counts["valid"] == 0:
                outcome = "failed"
            elif any(
                summary["rejected"] or summary["missing"]
                for summary in summaries
            ):
                outcome = "partial"
            else:
                outcome = "succeeded"

            db.execute(
                """
                UPDATE ops.pipeline_run
                SET status = %s, stage = 'finished', finished_at = %s
                WHERE run_id = %s
                """,
                (outcome, now_utc(), run_id),
            )

            print(json.dumps({
                "run_id": str(run_id),
                "status": outcome,
                "metadata": metadata_counts or "already available today",
                "station_status": status_counts,
            }, indent=2))

            return 0 if outcome == "succeeded" else 1

        except Exception as exc:
            # Keep the failing stage and store only the exception type.
            try:
                db.execute(
                    """
                    UPDATE ops.pipeline_run
                    SET status = 'failed',
                        finished_at = %s,
                        error_type = %s
                    WHERE run_id = %s
                    """,
                    (now_utc(), type(exc).__name__, run_id),
                )
            except psycopg.Error:
                print(
                    f"run_id={run_id}: could not persist failure status",
                    flush=True,
                )

            print(
                f"run_id={run_id}: failed ({type(exc).__name__})",
                flush=True,
            )
            return 1


def main():
    load_dotenv()

    parser = argparse.ArgumentParser()
    parser.add_argument("--init-stations", action="store_true")
    args = parser.parse_args()

    discovery_url = os.getenv(
        "GBFS_DISCOVERY_URL",
        "https://gbfs.citibikenyc.com/gbfs/gbfs.json",
    )

    with httpx.Client(
        timeout=20,
        follow_redirects=True,
        headers={
            "User-Agent": "BikeWatch/0.1 portfolio-project",
            "Accept": "application/json",
        },
    ) as client:
        if args.init_stations:
            initialize_stations(client, discovery_url)
            return 0

        return collect(client, discovery_url)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Configuration/connection failures before a run can be recorded.
        print(f"Startup failed ({type(exc).__name__})")
        raise SystemExit(1)