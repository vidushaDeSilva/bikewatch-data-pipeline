import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from ci_database import ci_port, connect_ci
from psycopg import sql

from bikewatch.ingestion import collect as collector

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "dbt_bikewatch/tests/gbfs_v1_1"

# Public credential for the disposable CI database only.
PASSWORD = "ci_only"


def test_dsn(role):
    if os.getenv("APP_ENV") != "ci":
        raise RuntimeError("These database tests require APP_ENV=ci.")

    # Keep compatibility with older tests requesting this admin name.
    if role == "bikewatch_test_admin":
        role = "postgres"

    allowed_roles = {
        "postgres",
        "bikewatch_ingest",
        "bikewatch_transform",
        "bikewatch_dashboard",
    }

    if role not in allowed_roles:
        raise ValueError(f"Unexpected test role: {role}")

    return (
        "host=127.0.0.1 hostaddr=127.0.0.1 "
        f"port={ci_port()} "
        f"dbname=bikewatch_test user={role} "
        f"password={PASSWORD} "
        "connect_timeout=5 sslmode=disable"
    )


@pytest.fixture(autouse=True)
def no_live_http(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Tests must use MockTransport, not live HTTP")

    monkeypatch.setattr(
        httpx.HTTPTransport,
        "handle_request",
        blocked,
    )


@pytest.fixture
def source_feeds():
    return {
        name: json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
        for name in (
            "station_information",
            "station_status",
        )
    }


@pytest.fixture(scope="session")
def database_ready():
    # connect_ci checks APP_ENV, host, port, and database name
    # before allowing access to the disposable database.
    with connect_ci() as db:
        identity = db.execute("SELECT current_database(), current_user").fetchone()

        if identity != ("bikewatch_test", "postgres"):
            raise RuntimeError(
                "Expected the disposable bikewatch_test database "
                "with the postgres test administrator."
            )

        # Read migrations before changing any database objects.
        migrations = [
            (ROOT / "sql" / "migrations" / filename).read_text(encoding="utf-8")
            for filename in (
                "003_ingestion_tables.sql",
                "004_ingestion_permissions.sql",
            )
        ]

        for role in (
            "bikewatch_ingest",
            "bikewatch_transform",
        ):
            exists = db.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s",
                (role,),
            ).fetchone()

            if not exists:
                db.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role)))

            db.execute(
                sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role),
                    sql.Literal(PASSWORD),
                )
            )

        # Reset only schemas in the guarded disposable database.
        db.execute("DROP SCHEMA IF EXISTS raw, ops CASCADE")
        db.execute("CREATE SCHEMA raw; CREATE SCHEMA ops")

        # Reproduce the broad defaults that migration 004
        # must remove. This preserves the permission tests.
        for schema in ("raw", "ops"):
            db.execute(
                sql.SQL(
                    """
                    ALTER DEFAULT PRIVILEGES IN SCHEMA {}
                    GRANT SELECT, INSERT, UPDATE
                    ON TABLES TO bikewatch_ingest
                    """
                ).format(sql.Identifier(schema))
            )

        # Test the actual ingestion migrations.
        for migration in migrations:
            db.execute(migration)


@pytest.fixture
def database(database_ready):
    with connect_ci() as db:
        db.execute(
            """
            TRUNCATE
                raw.station_metadata,
                raw.station_observation,
                ops.rejected_record,
                ops.pipeline_run
            RESTART IDENTITY
            """
        )

        yield db


@pytest.fixture
def run_collector(database, source_feeds, monkeypatch):
    tracked = {row["station_id"] for row in source_feeds["station_status"]["data"]["stations"]}

    monkeypatch.setenv(
        "BIKEWATCH_DATABASE_URL",
        test_dsn("bikewatch_ingest"),
    )
    monkeypatch.setattr(
        collector,
        "load_station_ids",
        lambda: set(tracked),
    )
    monkeypatch.setattr(
        collector,
        "now_utc",
        lambda: datetime(2026, 1, 15, 10, 7, tzinfo=UTC),
    )

    def run(status=None):
        calls = []

        def handler(request):
            name = request.url.path.rsplit("/", 1)[-1]
            calls.append(name)

            if name == "gbfs":
                payload = {
                    "version": "1.1",
                    "data": {
                        "en": {
                            "feeds": [
                                {
                                    "name": feed,
                                    "url": (f"https://fixture.test/{feed}"),
                                }
                                for feed in source_feeds
                            ]
                        }
                    },
                }
            elif name == "station_status" and status is not None:
                payload = status
            else:
                payload = source_feeds[name]

            return httpx.Response(200, json=payload)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            exit_code = collector.collect(
                client,
                "https://fixture.test/gbfs",
            )

        return exit_code, calls

    return run
