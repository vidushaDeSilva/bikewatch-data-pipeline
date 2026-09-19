"""Build, validate, and atomically publish BikeWatch reporting."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

from dotenv import load_dotenv
import psycopg
from psycopg.types.json import Jsonb
import yaml

from reporting_config import load_reporting_config
from publication_db import (
    MODELS,
    PublicationBlocked,
    publish_candidate,
    published_fingerprints,
)


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "dbt_bikewatch"

REQUIRED_MODELS = {
    *MODELS,
    "stg_station_metadata",
    "stg_station_observation",
    "stg_pipeline_run",
    "int_bw_tracking_periods",
    "int_bw_expected_buckets",
    "int_bw_observation_enriched",
    "int_bw_reporting_samples",
}

REQUIRED_TESTS = {
    "safe_casts",
    "staging_integrity",
    "staging_quality_warnings",
    "reporting_tracking_periods",
    "reporting_grains",
    "reporting_fact_integrity",
    "reporting_current_integrity",
    "reporting_mart_ranges",
    "reporting_mart_reconciliation",
    "reporting_sample_reconciliation",
    "reporting_quality_warnings",
    "reporting_coverage_expectations",
}

WARNING_TESTS = {
    "staging_quality_warnings",
    "reporting_quality_warnings",
}


def connect():
    return psycopg.connect(
        host=os.environ["DBT_HOST"],
        port=int(os.getenv("DBT_PORT", "5432")),
        dbname=os.getenv("DBT_DBNAME", "bikewatch"),
        user="bikewatch_transform",
        password=os.environ["DBT_ENV_SECRET_PASSWORD"],
        sslmode="require",
        connect_timeout=20,
        application_name="bikewatch_publication",
        autocommit=True,
    )


def check_freshness_artifact(path):
    artifact = json.loads(path.read_text())
    results = artifact.get("results", [])

    expected = {
        "source.bikewatch.bikewatch_raw.station_metadata",
        "source.bikewatch.bikewatch_raw.station_observation",
    }

    if {r["unique_id"] for r in results} != expected:
        raise PublicationBlocked(
            "Freshness results are incomplete."
        )

    if any(
        r["status"] not in ("pass", "warn")
        for r in results
    ):
        raise PublicationBlocked(
            "Source freshness failed; "
            "collect fresh data before publication."
        )

    return [
        {
            "check": r["unique_id"],
            "status": r["status"],
        }
        for r in results
    ]


def check_build_artifacts(target_path):
    manifest = json.loads(
        (target_path / "manifest.json").read_text()
    )

    artifact = json.loads(
        (target_path / "run_results.json").read_text()
    )

    nodes = manifest["nodes"]

    results = {
        r["unique_id"]: r
        for r in artifact.get("results", [])
    }

    expected = {
        uid: node
        for uid, node in nodes.items()
        if node.get("resource_type") in ("model", "test")
        and node.get("config", {}).get("enabled", True)
    }

    if any(uid not in results for uid in expected):
        raise PublicationBlocked(
            "Some enabled models/tests did not run."
        )

    model_names = {
        node["name"]
        for node in expected.values()
        if node["resource_type"] == "model"
    }

    test_names = {
        node["name"]
        for node in expected.values()
        if node["resource_type"] == "test"
    }

    if (
        not REQUIRED_MODELS <= model_names
        or not REQUIRED_TESTS <= test_names
    ):
        raise PublicationBlocked(
            "Required reporting models or gates are missing."
        )

    if len(test_names) < 69:
        raise PublicationBlocked(
            "Expected the existing 68 tests "
            "plus the new coverage test."
        )

    for uid, node in expected.items():
        status = results[uid]["status"]

        if node["resource_type"] == "model":
            if status != "success":
                raise PublicationBlocked(
                    "A model failed or was skipped."
                )

            if node["name"] in REQUIRED_MODELS:
                wanted = (
                    "bw_candidate_analytics"
                    if node["name"] in MODELS
                    else "bw_candidate"
                )

                if node["schema"] != wanted:
                    raise PublicationBlocked(
                        "Candidate model resolved outside "
                        "its isolated schema."
                    )

        elif status not in ("pass", "warn"):
            raise PublicationBlocked(
                f'Data test {node["name"]} returned {status}.'
            )

        elif (
            status == "warn"
            and node.get("config", {})
            .get("severity", "error")
            .lower() != "warn"
        ):
            raise PublicationBlocked(
                "A critical test returned warning results."
            )

        elif node["name"] in REQUIRED_TESTS - WARNING_TESTS:
            severity = (
                node.get("config", {})
                .get("severity", "error")
                .lower()
            )

            if severity != "error":
                raise PublicationBlocked(
                    "A required critical gate was weakened "
                    "to warning severity."
                )

    return {
        "models": len(model_names),
        "tests": len(test_names),
        "warnings": [
            {
                "check": uid,
                "rows": result.get("failures"),
            }
            for uid, result in results.items()
            if result["status"] == "warn"
        ],
    }


def main():
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--prove-failure",
        action="store_true",
        help=(
            "Require an existing release, deliberately fail "
            "a critical candidate test, and confirm that "
            "published data stays unchanged."
        ),
    )

    args = parser.parse_args()

    load_dotenv(ROOT / ".env")

    config = load_reporting_config(
        ROOT / "config/bikewatch_reporting_vars.yml"
    )

    if config.get("reporting_as_of") is not None:
        raise SystemExit(
            "Remove reporting_as_of from the vars file "
            "for live publication. This runner sets one "
            "cutoff from the database clock."
        )

    dbt = shutil.which("dbt")

    if dbt is None:
        raise SystemExit(
            "Activate the project virtual environment "
            "so dbt is on PATH."
        )

    run_id = uuid4()

    run_dir = (
        ROOT
        / ".bikewatch-publication"
        / str(run_id)
    )

    run_dir.mkdir(parents=True)

    target_path = run_dir / "target"

    phase = "starting"
    summary = {}
    recorded = False
    proof_triggered = False
    old_release = None
    old_fingerprints = None

    # Audit updates commit independently from publication.
    with connect() as connection, connect() as audit:
        try:
            with connection.transaction():
                connection.execute(
                    "SET LOCAL "
                    "idle_in_transaction_session_timeout = '15min'"
                )

                # Held throughout the candidate build and publication.
                locked = connection.execute(
                    "SELECT pg_try_advisory_xact_lock(5303, 1)"
                ).fetchone()[0]

                if not locked:
                    raise PublicationBlocked(
                        "Another publication is already running."
                    )

                old_release = audit.execute(
                    """
                    SELECT run_id
                    FROM ops.reporting_release
                    WHERE singleton
                    """
                ).fetchone()

                if args.prove_failure and old_release is None:
                    raise PublicationBlocked(
                        "Publish one successful release "
                        "before the failure drill."
                    )

                if args.prove_failure:
                    old_fingerprints = published_fingerprints(
                        connection
                    )

                audit.execute(
                    """
                    UPDATE ops.publication_run

                    SET status = 'interrupted',
                        finished_at = clock_timestamp(),
                        error_type = 'InterruptedPreviousRun'

                    WHERE status = 'running'
                    """
                )

                cutoff = connection.execute(
                    "SELECT clock_timestamp()"
                ).fetchone()[0]

                config["reporting_as_of"] = cutoff.isoformat()

                audit.execute(
                    """
                    INSERT INTO ops.publication_run (
                        run_id,
                        reporting_as_of,
                        status,
                        phase
                    )
                    VALUES (%s, %s, 'running', %s)
                    """,
                    (
                        run_id,
                        cutoff,
                        phase,
                    ),
                )

                recorded = True

                summary["coverage_mode"] = (
                    "scheduled"
                    if config.get("coverage_tracking_periods")
                    else "manual"
                )

                (
                    run_dir / "reporting_vars.json"
                ).write_text(
                    json.dumps(config, indent=2)
                )

                with TemporaryDirectory(
                    prefix="bikewatch-profile-"
                ) as temp:
                    profiles = Path(temp)

                    profile = {
                        "bikewatch": {
                            "target": "publication_candidate",
                            "outputs": {
                                "publication_candidate": {
                                    "type": "postgres",
                                    "host": (
                                        "{{ env_var('DBT_HOST') }}"
                                    ),
                                    "port": (
                                        "{{ env_var('DBT_PORT', "
                                        "'5432') | int }}"
                                    ),
                                    "dbname": (
                                        "{{ env_var('DBT_DBNAME', "
                                        "'bikewatch') }}"
                                    ),
                                    "user": "bikewatch_transform",
                                    "password": (
                                        "{{ env_var("
                                        "'DBT_ENV_SECRET_PASSWORD') }}"
                                    ),
                                    "schema": "bw_candidate",
                                    "threads": 1,
                                    "connect_timeout": 20,
                                    "sslmode": "require",
                                }
                            },
                        }
                    }

                    (
                        profiles / "profiles.yml"
                    ).write_text(
                        yaml.safe_dump(profile)
                    )

                    def run_dbt(*command):
                        command_line = [
                            dbt,
                            *command,
                            "--project-dir",
                            str(PROJECT),
                            "--profiles-dir",
                            str(profiles),
                            "--target",
                            "publication_candidate",
                            "--target-path",
                            str(target_path),
                            "--log-path",
                            str(run_dir / "logs"),
                            "--vars",
                            json.dumps(config),
                        ]

                        result = subprocess.run(
                            command_line,
                            check=False,
                            timeout=600,
                        ).returncode

                        connection.execute("SELECT 1")

                        return result

                    def set_phase(value):
                        audit.execute(
                            """
                            UPDATE ops.publication_run
                            SET phase = %s
                            WHERE run_id = %s
                            """,
                            (value, run_id),
                        )

                    phase = "source_freshness"
                    set_phase(phase)

                    result = run_dbt(
                        "source",
                        "freshness",
                        "--select",
                        "source:bikewatch_raw",
                    )

                    summary["freshness_before_build"] = (
                        check_freshness_artifact(
                            target_path / "sources.json"
                        )
                    )

                    if result:
                        raise PublicationBlocked(
                            "Freshness command exited unsuccessfully."
                        )

                    phase = "build_and_tests"
                    set_phase(phase)

                    result = run_dbt("build")

                    summary.update(
                        check_build_artifacts(target_path)
                    )

                    if result:
                        raise PublicationBlocked(
                            "dbt build exited unsuccessfully."
                        )

                    if args.prove_failure:
                        phase = "failure_drill"
                        set_phase(phase)

                        # Change only the isolated candidate.
                        affected = audit.execute(
                            """
                            UPDATE
                                bw_candidate_analytics.mart_station_hourly
                            SET collection_coverage = 2
                            """
                        ).rowcount

                        if not affected:
                            raise PublicationBlocked(
                                "Failure drill needs at least "
                                "one candidate hourly row."
                            )

                        result = run_dbt(
                            "test",
                            "--select",
                            "reporting_mart_ranges",
                        )

                        results = json.loads(
                            (
                                target_path / "run_results.json"
                            ).read_text()
                        )["results"]

                        proof_triggered = (
                            result != 0
                            and any(
                                r["unique_id"]
                                == "test.bikewatch.reporting_mart_ranges"
                                and r["status"] == "fail"
                                and (r.get("failures") or 0) > 0
                                for r in results
                            )
                        )

                        if not proof_triggered:
                            raise PublicationBlocked(
                                "Failure drill did not produce "
                                "the expected critical test failure."
                            )

                        summary["failure_drill"] = (
                            "critical range test failed as intended"
                        )

                        raise PublicationBlocked(
                            "Intentional critical-test failure; "
                            "publication blocked."
                        )

                    phase = "final_freshness"
                    set_phase(phase)

                    result = run_dbt(
                        "source",
                        "freshness",
                        "--select",
                        "source:bikewatch_raw",
                    )

                    summary["freshness_before_publish"] = (
                        check_freshness_artifact(
                            target_path / "sources.json"
                        )
                    )

                    if result:
                        raise PublicationBlocked(
                            "Final freshness command "
                            "exited unsuccessfully."
                        )

                    # Recent raw data must not conceal an old candidate.
                    candidate_is_recent = connection.execute(
                        """
                        SELECT coalesce(
                            max(collected_at)
                                >= clock_timestamp()
                                    - interval '90 minutes',
                            false
                        )
                        FROM bw_candidate.int_bw_observation_enriched
                        """
                    ).fetchone()[0]

                    if not candidate_is_recent:
                        raise PublicationBlocked(
                            "Candidate has no observations within "
                            "the 90-minute publication limit."
                        )

                    phase = "publish"
                    set_phase(phase)

                    connection.execute(
                        "SET LOCAL lock_timeout = '15s'"
                    )

                    summary = publish_candidate(
                        connection,
                        run_id,
                        cutoff,
                        summary,
                    )

            print(f"Published release {run_id}.")
            print(json.dumps(summary, indent=2))

            return 0

        except BaseException as exc:
            # The publication transaction has exited and rolled back
            # before this independent audit update.
            if recorded:
                status = (
                    "blocked"
                    if isinstance(exc, PublicationBlocked)
                    else "failed"
                )

                summary["reason"] = (
                    str(exc)
                    if isinstance(exc, PublicationBlocked)
                    else type(exc).__name__
                )

                audit.execute(
                    """
                    UPDATE ops.publication_run

                    SET status = %s,
                        phase = %s,
                        error_type = %s,
                        finished_at = clock_timestamp(),
                        check_summary = %s

                    WHERE run_id = %s
                      AND status = 'running'
                    """,
                    (
                        status,
                        phase,
                        type(exc).__name__,
                        Jsonb(summary),
                        run_id,
                    ),
                )

            current = audit.execute(
                """
                SELECT run_id
                FROM ops.reporting_release
                WHERE singleton
                """
            ).fetchone()

            # Resolve an error reported after a successful commit.
            if current == (run_id,):
                print(
                    f"Release {run_id} is committed; "
                    "a connection error occurred after commit."
                )

                return 0

            if proof_triggered:
                if current != old_release:
                    raise RuntimeError(
                        "Failure drill found an unexpected "
                        "release-pointer change."
                    ) from None

                if (
                    published_fingerprints(audit)
                    != old_fingerprints
                ):
                    raise RuntimeError(
                        "Failure drill found changed published rows."
                    ) from None

                print(
                    "PASS: critical test failed; published release "
                    "and row fingerprints are unchanged."
                )

                print(f"Blocked attempt: {run_id}")

                return 0

            message = (
                str(exc)
                if isinstance(exc, PublicationBlocked)
                else type(exc).__name__
            )

            print(
                f"Not published: {message}. "
                f"Phase: {phase}. Attempt: {run_id}",
                file=sys.stderr,
            )

            pointer = current[0] if current else "none"

            print(
                f"Existing published pointer: {pointer}",
                file=sys.stderr,
            )

            return 1


if __name__ == "__main__":
    raise SystemExit(main())



# load reporting config
#         ↓
# get publication lock
#         ↓
# record publication attempt
#         ↓
# check source freshness
#         ↓
# dbt build candidate models + tests
#         ↓
# validate dbt artifacts
#         ↓
# check freshness again
#         ↓
# verify candidate contains recent data
#         ↓
# publish_candidate()
#         ↓
# new release becomes active


# connect() — opens a PostgreSQL connection to Neon using the bikewatch_transform role and environment variables for host, 
# port, database, and password.

# check_freshness_artifact(path) — reads dbt's sources.json freshness result, makes sure the expected raw sources were checked, 
# and blocks publication if source freshness failed.

# check_build_artifacts(target_path) — reads dbt's manifest.json and run_results.json, then verifies that all 
# required models/tests actually ran, required reporting models exist, models built into the correct candidate schemas, 
# critical tests passed, and warning tests were not incorrectly configured.