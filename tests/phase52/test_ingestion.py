import copy
import json

import httpx
import psycopg
import pytest

from src.bikewatch.ingestion import collect as collector


def run_record(db):
    # Used only by tests that create exactly one run.
    return db.execute(
        """
        SELECT status, stage, metrics, error_type
        FROM ops.pipeline_run
        """
    ).fetchone()


def test_commit_and_same_bucket_retry(
    database,
    run_collector,
    capsys,
):
    code, calls = run_collector()

    assert code == 0
    assert calls == [
        "gbfs",
        "station_information",
        "station_status",
    ]

    metrics = run_record(database)[2]
    assert metrics["station_status"]["accepted"] == 4

    events = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.startswith('{"event": "batch_committed"')
    ]

    assert [event["feed"] for event in events] == [
        "station_information",
        "station_status",
    ]
    assert events[1]["counts"]["accepted"] == 4

    # Another run in the same bucket.
    code, calls = run_collector()

    assert code == 0

    # Today's complete metadata is reused.
    assert calls == ["gbfs", "station_status"]

    observation_count = database.execute(
        "SELECT count(*) FROM raw.station_observation"
    ).fetchone()[0]

    assert observation_count == 4

    runs = database.execute(
        "SELECT status, metrics FROM ops.pipeline_run"
    ).fetchall()

    assert len(runs) == 2
    assert all(status == "succeeded" for status, _ in runs)

    outcomes = sorted(
        (
            metrics["station_status"]["accepted"],
            metrics["station_status"]["duplicate"],
        )
        for _, metrics in runs
    )

    assert outcomes == [(0, 4), (4, 0)]


def test_mixed_records(
    database,
    run_collector,
    source_feeds,
):
    status = copy.deepcopy(source_feeds["station_status"])

    # Fourth expected station is missing.
    status["data"]["stations"].pop()

    # Third station is identifiable but invalid.
    status["data"]["stations"][2]["num_bikes_available"] = -1

    assert run_collector(status)[0] == 1

    outcome, _, metrics, _ = run_record(database)

    assert outcome == "partial"
    assert metrics["station_status"] == {
        "received": 3,
        "ignored": 0,
        "valid": 2,
        "accepted": 2,
        "duplicate": 0,
        "rejected": 1,
        "missing": 1,
    }

    assert database.execute(
        "SELECT count(*) FROM raw.station_observation"
    ).fetchone()[0] == 2

    reason, payload = database.execute(
        "SELECT reason, payload FROM ops.rejected_record"
    ).fetchone()

    assert "num_bikes_available" in reason
    assert payload["num_bikes_available"] == -1


def test_repeated_source_id(
    database,
    run_collector,
    source_feeds,
):
    status = copy.deepcopy(source_feeds["station_status"])
    records = status["data"]["stations"]

    records.append(copy.deepcopy(records[0]))

    assert run_collector(status)[0] == 1

    counts = run_record(database)[2]["station_status"]

    assert (
        counts["accepted"],
        counts["rejected"],
        counts["duplicate"],
    ) == (4, 1, 0)

    reason = database.execute(
        "SELECT reason FROM ops.rejected_record"
    ).fetchone()[0]

    assert reason == "repeated_station_id"


@pytest.mark.parametrize(
    "status",
    [
        # Invalid envelope.
        {"version": "1.1", "data": {}},

        # Structurally valid envelope with no station records.
        {
            "version": "1.1",
            "last_updated": 1,
            "ttl": 0,
            "data": {"stations": []},
        },
    ],
)
def test_unusable_status_fails(
    database,
    run_collector,
    status,
):
    assert run_collector(status)[0] == 1
    assert run_record(database)[0] == "failed"

    assert database.execute(
        "SELECT count(*) FROM raw.station_observation"
    ).fetchone()[0] == 0


def test_database_failure_rolls_back_batch(
    database,
    run_collector,
    source_feeds,
    monkeypatch,
    capsys,
):
    status = copy.deepcopy(source_feeds["station_status"])

    # Two valid records are processed before this rejection.
    status["data"]["stations"][2]["num_bikes_available"] = -1

    original_quarantine = collector.quarantine
    reached_failure = []

    def fail_after_quarantine(
        db,
        run_id,
        feed,
        reason,
        payload,
    ):
        original_quarantine(
            db,
            run_id,
            feed,
            reason,
            payload,
        )

        if feed == "station_status":
            # This connection can see its uncommitted inserts.
            inserted = db.execute(
                """
                SELECT count(*)
                FROM raw.station_observation
                WHERE run_id = %s
                """,
                (run_id,),
            ).fetchone()[0]

            assert inserted == 2
            reached_failure.append(True)

            # A real PostgreSQL error aborts the transaction.
            db.execute("SELECT 1 / 0")

    monkeypatch.setattr(
        collector,
        "quarantine",
        fail_after_quarantine,
    )

    assert run_collector(status)[0] == 1
    assert reached_failure == [True]

    outcome, stage, metrics, error = run_record(database)

    assert (outcome, stage, error) == (
        "failed",
        "collecting_status",
        "DivisionByZero",
    )

    # The failed status batch did not publish metrics.
    assert "station_status" not in metrics

    # The earlier independent metadata batch remains committed.
    assert metrics["station_information"]["accepted"] == 4

    assert database.execute(
        "SELECT count(*) FROM raw.station_observation"
    ).fetchone()[0] == 0

    assert database.execute(
        "SELECT count(*) FROM ops.rejected_record"
    ).fetchone()[0] == 0

    assert database.execute(
        "SELECT count(*) FROM raw.station_metadata"
    ).fetchone()[0] == 4

    output = capsys.readouterr().out

    # No committed-status-batch log should have been emitted.
    assert '"feed": "station_status"' not in output


def test_failure_logs_hide_exception_details(
    database,
    run_collector,
    monkeypatch,
    capsys,
):
    secret = "DO_NOT_LOG_THIS_TEST_SECRET"

    def fail(*args):
        raise httpx.ConnectError(
            f"postgresql://someone:{secret}@example.invalid/db"
        )

    monkeypatch.setattr(collector, "discover", fail)

    assert run_collector()[0] == 1
    assert run_record(database)[3] == "ConnectError"

    logs = capsys.readouterr()
    output = logs.out + logs.err

    assert secret not in output
    assert "postgresql://" not in output


def test_role_permissions(database, run_collector):
    # The collector performs these inserts as bikewatch_ingest.
    assert run_collector()[0] == 0

    cases = [
        (
            "bikewatch_ingest",
            "raw.station_observation",
            (True, True, False, False),
        ),
        (
            "bikewatch_ingest",
            "raw.station_metadata",
            (True, True, False, False),
        ),
        (
            "bikewatch_ingest",
            "ops.pipeline_run",
            (True, True, True, False),
        ),
        (
            "bikewatch_ingest",
            "ops.rejected_record",
            (False, True, False, False),
        ),
        (
            "bikewatch_transform",
            "ops.pipeline_run",
            (True, False, False, False),
        ),
        (
            "bikewatch_transform",
            "ops.rejected_record",
            (False, False, False, False),
        ),
    ]

    privileges = ("SELECT", "INSERT", "UPDATE", "DELETE")

    for role, table, expected in cases:
        actual = tuple(
            database.execute(
                "SELECT has_table_privilege(%s, %s, %s)",
                (role, table, privilege),
            ).fetchone()[0]
            for privilege in privileges
        )

        assert actual == expected, (role, table, actual)

    # Use a real restricted login, not SET ROLE from an owner.
    with psycopg.connect(
        host="127.0.0.1",
        hostaddr="127.0.0.1",
        port=55432,
        dbname="bikewatch_test",
        user="bikewatch_transform",
        password="bikewatch_test_only",
        autocommit=True,
        sslmode="disable",
        connect_timeout=5,
    ) as db:
        assert db.execute(
            "SELECT count(*) FROM raw.station_observation"
        ).fetchone()[0] == 4

        with pytest.raises(
            psycopg.errors.InsufficientPrivilege
        ):
            db.execute(
                """
                UPDATE raw.station_observation
                SET payload = payload
                WHERE false
                """
            )