"""Check seven days of scheduled BikeWatch operation and measured Neon usage."""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
BUCKET = timedelta(minutes=15)
DAYS = 7
GRACE = timedelta(minutes=30)


def utc_time(value):
    """Parse an ISO timestamp and require an explicit UTC offset."""
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))

    if result.tzinfo is None:
        raise ValueError("Timestamps must include Z or a UTC offset.")

    return result.astimezone(UTC)


def bucket_for(value):
    """Return the UTC 15-minute bucket containing a timestamp."""
    minute = value.minute - value.minute % 15
    return value.replace(minute=minute, second=0, microsecond=0)


def database_connection():
    """Connect as the existing transformation role without printing credentials."""
    return psycopg.connect(
        host=os.environ["DBT_HOST"],
        port=int(os.getenv("DBT_PORT", "5432")),
        dbname=os.getenv("DBT_DBNAME", "bikewatch"),
        user="bikewatch_transform",
        password=os.environ["DBT_ENV_SECRET_PASSWORD"],
        sslmode="require",
        connect_timeout=20,
    )


def tracked_station_count():
    """Read the station count from the collector's tracked-station file."""
    path = ROOT / "config/tracked_stations.json"
    station_ids = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(station_ids, list) or not station_ids:
        raise ValueError("tracked_stations.json must contain a nonempty array.")

    if len(station_ids) != len(set(station_ids)):
        raise ValueError("Tracked station IDs must be unique.")

    return len(station_ids)


def github_schedule_results(path, start, end):
    """Count scheduled GitHub runs in the requested seven-day window."""
    runs = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(runs, list):
        raise ValueError("The GitHub runs file must contain a JSON array.")

    selected = [
        run
        for run in runs
        if run.get("event") == "schedule" and start <= utc_time(run["createdAt"]) < end
    ]

    successful = sum(run.get("conclusion") == "success" for run in selected)

    return {
        "scheduled_runs": len(selected),
        "successful_scheduled_runs": successful,
        "other_scheduled_runs": len(selected) - successful,
    }


def database_results(start, end):
    """Read collection, publication, coverage, and cleanup evidence."""
    collection = defaultdict(set)
    publication = defaultdict(set)
    coverage = {}
    cleanup = {}

    with database_connection() as db:
        for bucket, status in db.execute(
            """
            SELECT collection_bucket, status
            FROM ops.pipeline_run
            WHERE collection_bucket >= %s
              AND collection_bucket < %s
            """,
            (start, end),
        ):
            collection[bucket].add(status)

        for bucket, count in db.execute(
            """
            SELECT collection_bucket, count(DISTINCT station_id)
            FROM raw.station_observation
            WHERE collection_bucket >= %s
              AND collection_bucket < %s
            GROUP BY collection_bucket
            """,
            (start, end),
        ):
            coverage[bucket] = count

        for reporting_as_of, status in db.execute(
            """
            SELECT reporting_as_of, status
            FROM ops.publication_run
            WHERE reporting_as_of >= %s
              AND reporting_as_of < %s
            """,
            (start, end),
        ):
            publication[bucket_for(reporting_as_of)].add(status)

        for run_key, status in db.execute(
            """
            SELECT run_key, status
            FROM ops.maintenance_run
            WHERE task = 'daily_snapshot_cleanup'
              AND run_key >= %s
              AND run_key < %s
            """,
            (start.date(), end.date()),
        ):
            cleanup[run_key] = status

        database_bytes = db.execute("SELECT pg_database_size(current_database())").fetchone()[0]

    return collection, publication, coverage, cleanup, database_bytes


def neon_results(args):
    """Compare console measurements with the limits supplied by the operator."""
    values = (
        args.neon_week_cu_hours,
        args.neon_monthly_cu_hours_limit,
        args.neon_storage_gb,
        args.neon_storage_gb_limit,
    )

    if any(value is None for value in values):
        return {
            "measured": False,
            "note": "Enter actual Neon Console usage and your plan's limits.",
        }

    if any(value < 0 for value in values):
        raise ValueError("Neon usage values and limits cannot be negative.")

    # A 31-day projection is a conservative estimate, not a billing forecast.
    projected_31_day_cu_hours = args.neon_week_cu_hours * 31 / DAYS

    return {
        "measured": True,
        "week_cu_hours": args.neon_week_cu_hours,
        "projected_31_day_cu_hours": round(projected_31_day_cu_hours, 2),
        "monthly_cu_hours_limit": args.neon_monthly_cu_hours_limit,
        "storage_gb": args.neon_storage_gb,
        "storage_gb_limit": args.neon_storage_gb_limit,
        "within_supplied_limits": (
            projected_31_day_cu_hours <= args.neon_monthly_cu_hours_limit
            and args.neon_storage_gb <= args.neon_storage_gb_limit
        ),
    }


def main():
    """Print a reviewable validation result and return its exit status."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--start",
        required=True,
        help="UTC midnight at the start of seven scheduled days; ISO format.",
    )
    parser.add_argument(
        "--github-runs",
        required=True,
        type=Path,
        help="JSON exported from scheduled collect.yml GitHub runs.",
    )
    parser.add_argument("--neon-week-cu-hours", type=float)
    parser.add_argument("--neon-monthly-cu-hours-limit", type=float)
    parser.add_argument("--neon-storage-gb", type=float)
    parser.add_argument("--neon-storage-gb-limit", type=float)
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")

    start = utc_time(args.start)

    if start.time().hour != 0 or start.time().minute != 0:
        raise ValueError("--start must be midnight UTC.")

    end = start + timedelta(days=DAYS)
    now = datetime.now(UTC)
    complete = now >= end + GRACE

    # Do not score buckets that have not had time to finish.
    evaluated_end = min(end, bucket_for(now - GRACE))
    evaluated_count = max(
        0,
        int((evaluated_end - start) / BUCKET),
    )
    buckets = [start + index * BUCKET for index in range(evaluated_count)]

    station_count = tracked_station_count()
    collection, publication, coverage, cleanup, database_bytes = database_results(
        start, evaluated_end
    )

    missing_collection = [
        bucket.isoformat() for bucket in buckets if "succeeded" not in collection[bucket]
    ]
    missing_publication = [
        bucket.isoformat() for bucket in buckets if "published" not in publication[bucket]
    ]
    coverage_shortfalls = [
        {
            "bucket": bucket.isoformat(),
            "stations": coverage.get(bucket, 0),
        }
        for bucket in buckets
        if coverage.get(bucket, 0) != station_count
    ]

    dates = [start.date() + timedelta(days=offset) for offset in range(DAYS)]
    cleanup_failures = [day.isoformat() for day in dates if cleanup.get(day) != "succeeded"]

    github = github_schedule_results(args.github_runs, start, end)
    neon = neon_results(args)
    expected_count = DAYS * 24 * 4

    checks = {
        "full_seven_days_elapsed": complete,
        "collection_in_every_bucket": not missing_collection,
        "publication_in_every_bucket": not missing_publication,
        "all_tracked_stations_in_every_bucket": not coverage_shortfalls,
        "daily_cleanup_succeeded": not cleanup_failures,
        "scheduled_github_runs_succeeded": (
            github["successful_scheduled_runs"] >= expected_count
            and github["other_scheduled_runs"] == 0
        ),
        "neon_usage_measured_and_within_limits": (
            neon["measured"] and neon["within_supplied_limits"]
        ),
    }

    if not complete or not neon["measured"]:
        result = "pending"
    elif all(checks.values()):
        result = "pass"
    else:
        result = "needs_review"

    print(
        json.dumps(
            {
                "result": result,
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
                "expected_buckets": expected_count,
                "evaluated_buckets": evaluated_count,
                "tracked_stations": station_count,
                "checks": checks,
                "missing_or_unsuccessful_collection_count": len(missing_collection),
                "collection_examples": missing_collection[:12],
                "missing_publication_count": len(missing_publication),
                "publication_examples": missing_publication[:12],
                "coverage_shortfall_count": len(coverage_shortfalls),
                "coverage_examples": coverage_shortfalls[:12],
                "cleanup_days_without_success": cleanup_failures,
                "github": github,
                "postgres_database_bytes": database_bytes,
                "neon": neon,
            },
            indent=2,
        )
    )

    return {"pass": 0, "needs_review": 1, "pending": 2}[result]


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        FileNotFoundError,
        KeyError,
        ValueError,
        psycopg.Error,
    ) as error:
        # Database exceptions can contain connection details: print only the type.
        print(
            f"Validation could not finish ({type(error).__name__}).",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
