import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg
import pytest
from psycopg import sql

from src.bikewatch.ingestion import collect as collector


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/gbfs_v1_1"

# Public, disposable test credential
PASSWORD = "bikewatch_test_only"


def test_dsn(role):
    return (
        "host=127.0.0.1 hostaddr=127.0.0.1 port=55432 "
        f"dbname=bikewatch_test user={role} password={PASSWORD} "
        "connect_timeout=5 sslmode=disable"
    )


@pytest.fixture(autouse=True)
def no_live_http(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError(
            "Tests must use MockTransport, not live HTTP"
        )

    monkeypatch.setattr(
        httpx.HTTPTransport,
        "handle_request",
        blocked,
    )


@pytest.fixture
def source_feeds():
    return {
        name: json.loads(
            (FIXTURES / f"{name}.json").read_text(encoding="utf-8")
        )
        for name in ("station_information", "station_status")
    }


@pytest.fixture(scope="session")
def database_ready():
    with psycopg.connect(
        test_dsn("bikewatch_test_admin"),
        autocommit=True,
    ) as db:
        identity = db.execute(
            "SELECT current_database(), current_user"
        ).fetchone()

        assert identity == (
            "bikewatch_test",
            "bikewatch_test_admin",
        )

        for role in ("bikewatch_ingest", "bikewatch_transform"):
            exists = db.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s",
                (role,),
            ).fetchone()

            if not exists:
                db.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD {}"
                    ).format(
                        sql.Identifier(role),
                        sql.Literal(PASSWORD),
                    )
                )

        # Reset only this disposable database's test schemas.
        db.execute("DROP SCHEMA IF EXISTS raw, ops CASCADE")
        db.execute("CREATE SCHEMA raw; CREATE SCHEMA ops")

        # Reproduce earlier broad defaults so migration 004
        # must remove the permissions inherited by new tables.
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

        # Apply the actual project migrations.
        for filename in (
            "003_ingestion_tables.sql",
            "004_ingestion_permissions.sql",
        ):
            migration = ROOT / "sql/migrations" / filename
            db.execute(migration.read_text(encoding="utf-8"))


@pytest.fixture
def database(database_ready):
    with psycopg.connect(
        test_dsn("bikewatch_test_admin"),
        autocommit=True,
    ) as db:
        # Every database test starts with empty tables.
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
    tracked = {
        row["station_id"]
        for row in source_feeds["station_status"]["data"]["stations"]
    }

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
                                    "url": f"https://fixture.test/{feed}",
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

        with httpx.Client(
            transport=httpx.MockTransport(handler)
        ) as client:
            exit_code = collector.collect(
                client,
                "https://fixture.test/gbfs",
            )

        return exit_code, calls

    return run