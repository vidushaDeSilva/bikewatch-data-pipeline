# Maintain reporting snapshots and record weekly database activity.
# Snapshot cleanup shares the publisher's lock and preserves active data.

"""Run audited snapshot cleanup and database usage reporting."""

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import sql
from psycopg.types.json import Jsonb
from publication_db import MODELS

ROOT = Path(__file__).resolve().parents[1]


def connect():
    """Connect using the existing transformation credentials."""

    return psycopg.connect(
        host=os.environ["DBT_HOST"],
        port=int(os.getenv("DBT_PORT", "5432")),
        dbname=os.getenv("DBT_DBNAME", "bikewatch"),
        user="bikewatch_transform",
        password=os.environ["DBT_ENV_SECRET_PASSWORD"],
        sslmode=os.getenv("DBT_SSLMODE", "require"),
        connect_timeout=20,
        application_name="bikewatch-maintenance",
    )


def set_timeouts(db):
    """Apply transaction-local limits compatible with the pooler."""

    db.execute("SET LOCAL statement_timeout = '60s'")
    db.execute("SET LOCAL lock_timeout = '5s'")


def record_result(db, task, run_key, started_at, status, summary):
    """Save the latest outcome for a daily or weekly task."""

    db.execute(
        """
        INSERT INTO ops.maintenance_run (
            task, run_key, started_at, finished_at, status, summary
        )
        VALUES (%s, %s, %s, clock_timestamp(), %s, %s)
        ON CONFLICT (task, run_key)
        DO UPDATE SET
            started_at = EXCLUDED.started_at,
            finished_at = EXCLUDED.finished_at,
            status = EXCLUDED.status,
            summary = EXCLUDED.summary
        """,
        (
            task,
            run_key,
            started_at,
            status,
            Jsonb(summary),
        ),
    )


def obsolete_snapshots(published_ids, current_id, existing, keep=2):
    """Select known snapshots outside the protected release set."""

    protected = set(published_ids[:keep]) | {current_id}

    return sorted(
        name
        for run_id in published_ids
        if run_id not in protected
        for model in MODELS
        if (name := f"v_{run_id.hex}_{model}") in existing
    )


def cleanup(db, run_key, started_at, apply):
    """Remove superseded snapshots once per UTC day."""

    # Use the same transaction lock as publish_reporting.py.
    locked = db.execute("SELECT pg_try_advisory_xact_lock(5303, 1)").fetchone()[0]

    if not locked:
        raise RuntimeError("Publication or maintenance is already running.")

    if apply:
        completed = db.execute(
            """
            SELECT 1
            FROM ops.maintenance_run
            WHERE task = 'daily_snapshot_cleanup'
              AND run_key = %s
              AND status = 'succeeded'
            """,
            (run_key,),
        ).fetchone()

        if completed:
            return {"status": "skipped", "reason": "already_completed_today"}

    release = db.execute(
        """
        SELECT run_id, published_at
        FROM ops.reporting_release
        WHERE singleton
        FOR UPDATE
        """
    ).fetchone()

    if release is None:
        raise RuntimeError("Cleanup requires a published release.")

    current_id, published_at = release

    if apply and published_at < started_at - timedelta(hours=1):
        raise RuntimeError("Cleanup requires a publication within the last hour.")

    published_ids = [
        row[0]
        for row in db.execute(
            """
            SELECT run_id
            FROM ops.publication_run
            WHERE status = 'published'
            ORDER BY finished_at DESC NULLS LAST, run_id DESC
            """
        ).fetchall()
    ]

    if current_id not in published_ids:
        raise RuntimeError("Active release is missing its successful audit row.")

    existing = {
        row[0]
        for row in db.execute(
            """
            SELECT relation.relname
            FROM pg_class AS relation
            JOIN pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = 'reporting_versions'
              AND relation.relkind = 'r'
            """
        ).fetchall()
    }

    candidates = obsolete_snapshots(
        published_ids,
        current_id,
        existing,
    )

    summary = {
        "status": "succeeded" if apply else "preview",
        "active_release": str(current_id),
        "protected_recent_releases": 2,
        "snapshot_count": len(candidates),
        "snapshots": candidates,
    }

    if not apply:
        return summary

    for name in candidates:
        # RESTRICT aborts cleanup if a view still depends on this snapshot.
        db.execute(
            sql.SQL("DROP TABLE {} RESTRICT").format(sql.Identifier("reporting_versions", name))
        )

    # Deletions and their success record commit together.
    record_result(
        db,
        "daily_snapshot_cleanup",
        run_key,
        started_at,
        "succeeded",
        summary,
    )

    return summary


def status_counts(db, table, since):
    """Count audited outcomes within the reporting window."""

    query = sql.SQL(
        """
        SELECT status, count(*)
        FROM {}
        WHERE started_at >= %s
        GROUP BY status
        ORDER BY status
        """
    ).format(sql.Identifier("ops", table))

    return dict(db.execute(query, (since,)).fetchall())


def usage_report(db, run_key, started_at, output):
    """Write database storage and seven-day execution statistics."""

    since = started_at - timedelta(days=7)

    database, database_bytes = db.execute(
        """
        SELECT
            current_database(),
            pg_database_size(current_database())
        """
    ).fetchone()

    sizes = db.execute(
        """
        SELECT
            namespace.nspname,
            sum(pg_total_relation_size(relation.oid))::bigint
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname IN (
            'raw',
            'ops',
            'staging',
            'analytics',
            'analytics_work',
            'bw_candidate',
            'bw_candidate_analytics',
            'reporting_versions'
        )
          AND relation.relkind IN ('r', 'm')
        GROUP BY namespace.nspname
        ORDER BY namespace.nspname
        """
    ).fetchall()

    observation_count, latest_collection = db.execute(
        """
        SELECT count(*), max(collected_at)
        FROM raw.station_observation
        """
    ).fetchone()

    release = db.execute(
        """
        SELECT run_id, published_at
        FROM ops.reporting_release
        WHERE singleton
        """
    ).fetchone()

    report = {
        "generated_at": started_at.isoformat(),
        "window_start": since.isoformat(),
        "database": database,
        "database_bytes": database_bytes,
        "schema_bytes": dict(sizes),
        "retained_observation_count": observation_count,
        "latest_collection": (latest_collection.isoformat() if latest_collection else None),
        "published_release": (
            {
                "run_id": str(release[0]),
                "published_at": release[1].isoformat(),
            }
            if release
            else None
        ),
        "ingestion_outcomes": status_counts(db, "pipeline_run", since),
        "publication_outcomes": status_counts(db, "publication_run", since),
        "maintenance_outcomes": status_counts(db, "maintenance_run", since),
        "neon_compute_usage": None,
        "usage_note": (
            "PostgreSQL storage and execution counts only. "
            "Check Neon Usage separately for compute and transfer consumption."
        ),
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )

    record_result(
        db,
        "weekly_usage_report",
        run_key,
        started_at,
        "succeeded",
        {
            "database_bytes": database_bytes,
            "schema_bytes": dict(sizes),
            "report_path": str(output),
        },
    )

    return report


def main():
    """Run the requested task and record failures without credentials."""

    load_dotenv(ROOT / ".env")

    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    cleanup_parser = commands.add_parser("cleanup")
    cleanup_parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete eligible snapshots; otherwise only preview.",
    )

    report_parser = commands.add_parser("report")
    report_parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts/weekly-usage-report.json",
    )

    args = parser.parse_args()
    started_at = datetime.now(UTC)
    run_key = started_at.date()

    task = "daily_snapshot_cleanup"
    should_audit_failure = args.command == "report" or args.apply

    if args.command == "report":
        task = "weekly_usage_report"
        run_key -= timedelta(days=run_key.weekday())

    try:
        with connect() as db:
            set_timeouts(db)

            if args.command == "cleanup":
                result = cleanup(db, run_key, started_at, args.apply)
            else:
                result = usage_report(
                    db,
                    run_key,
                    started_at,
                    args.output,
                )

        print(json.dumps(result, indent=2))
        return 0

    except Exception as error:
        error_type = type(error).__name__

        if should_audit_failure:
            try:
                with connect() as audit:
                    set_timeouts(audit)
                    record_result(
                        audit,
                        task,
                        run_key,
                        started_at,
                        "failed",
                        {"error_type": error_type},
                    )
            except Exception:
                print("Could not record maintenance failure.", file=sys.stderr)

        print(f"Maintenance failed: {error_type}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
