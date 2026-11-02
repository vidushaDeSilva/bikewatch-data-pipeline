"""Offline regression tests for live station selection and historical cutover."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from scripts import replace_station
from scripts.reporting_config import load_reporting_config, utc_boundary


@pytest.fixture
def live_case(tmp_path):
    """Produce representative GBFS 1.1 feeds with two valid alternatives."""
    now = datetime(2026, 11, 2, 12, tzinfo=UTC)
    # Keep this fixture independent of the repository's mutable production config.
    # Otherwise the suite becomes impossible to rerun after --apply replaces OLD_ID.
    original_ids = [replace_station.OLD_ID] + [f"TRACKED_{index:02d}" for index in range(19)]
    reports = [
        "reporting_timezone: America/New_York",
        "feed_stale_after_seconds: 2700",
        "",
        "coverage_tracking_periods:",
    ]
    for station_id in sorted(original_ids):
        reports.extend(
            [
                f'  - station_id: "{station_id}"',
                '    start_bucket: "2026-10-03T08:00:00Z"',
                "    end_bucket: null",
                "",
            ]
        )
    tracked_path = tmp_path / "tracked_stations.json"
    reporting_path = tmp_path / "bikewatch_reporting_vars.yml"
    tracked_path.write_text(json.dumps(original_ids, indent=2) + "\n", encoding="utf-8")
    reporting_path.write_text("\n".join(reports), encoding="utf-8")

    def info(station_id, latitude, longitude):
        return {
            "station_id": station_id,
            "name": station_id,
            "lat": latitude,
            "lon": longitude,
            "capacity": 30,
        }

    def status(station_id, installed=1):
        return {
            "station_id": station_id,
            "num_bikes_available": 10,
            "num_docks_available": 20,
            "is_installed": installed,
            "is_renting": 1,
            "is_returning": 1,
            "last_reported": int(now.timestamp()),
        }

    info_rows = [
        info(station_id, 40.759, -73.986)
        for station_id in original_ids
        if station_id != replace_station.OLD_ID
    ]
    status_rows = [
        status(station_id) for station_id in original_ids if station_id != replace_station.OLD_ID
    ]
    info_rows.extend([info("NEW_NEAR", 40.758, -73.9855), info("NEW_FAR", 40.768, -73.99)])
    status_rows.extend([status("NEW_NEAR"), status("NEW_FAR")])

    def envelope(rows):
        return {
            "version": "1.1",
            "last_updated": int(now.timestamp()),
            "ttl": 60,
            "data": {"stations": rows},
        }

    return now, tracked_path, reporting_path, envelope(info_rows), envelope(status_rows)


def test_live_selection_finds_nearest_operational_station(live_case):
    now, tracked_path, _, information, status = live_case
    ids = set(json.loads(tracked_path.read_text()))
    ranked, sources = replace_station.rank_replacements(information, status, ids, now)
    assert [row["station_id"] for row in ranked] == ["NEW_NEAR", "NEW_FAR"]
    assert sources["information"] == now.isoformat()


def test_replacement_preserves_historical_period_and_20_active_stations(live_case):
    now, tracked_path, reporting_path, _, _ = live_case
    change_at = now + timedelta(hours=2)
    old_config = load_reporting_config(reporting_path)
    original = reporting_path.read_text()
    old_period_count = len(old_config["coverage_tracking_periods"])

    new_json, new_yaml = replace_station.plan_changes(
        tracked_path, reporting_path, replace_station.OLD_ID, "NEW_NEAR", change_at
    )
    # Preview must never write either file.
    assert reporting_path.read_text() == original
    assert replace_station.OLD_ID in json.loads(tracked_path.read_text())

    replace_station.write_configs(tracked_path, reporting_path, new_json, new_yaml)
    stations = json.loads(tracked_path.read_text())
    periods = load_reporting_config(reporting_path)["coverage_tracking_periods"]
    assert len(stations) == len(set(stations)) == 20
    assert replace_station.OLD_ID not in stations
    assert "NEW_NEAR" in stations
    assert len(periods) == old_period_count + 1
    old = [p for p in periods if p["station_id"] == replace_station.OLD_ID][-1]
    new = [p for p in periods if p["station_id"] == "NEW_NEAR"][0]
    assert utc_boundary(old["end_bucket"]) == change_at
    assert utc_boundary(new["start_bucket"]) == change_at
    assert old["start_bucket"] == "2026-10-03T08:00:00Z"
    assert new["end_bucket"] is None


def test_missing_other_existing_station_blocks_replacement(live_case):
    now, tracked_path, _, information, status = live_case
    status["data"]["stations"].pop(0)
    with pytest.raises(ValueError, match="Other tracked stations"):
        replace_station.rank_replacements(
            information, status, set(json.loads(tracked_path.read_text())), now
        )


def test_stale_feed_is_rejected(live_case):
    now, tracked_path, _, information, status = live_case
    status["last_updated"] -= 3600
    with pytest.raises(ValueError, match="stale"):
        replace_station.rank_replacements(
            information, status, set(json.loads(tracked_path.read_text())), now
        )


def test_closed_station_not_selected(live_case):
    now, tracked_path, _, information, status = live_case
    for row in status["data"]["stations"]:
        if row["station_id"] == "NEW_NEAR":
            row["is_installed"] = 0
    ranked, _ = replace_station.rank_replacements(
        information, status, set(json.loads(tracked_path.read_text())), now
    )
    assert ranked[0]["station_id"] == "NEW_FAR"


def test_invalid_second_migration_has_no_side_effects(live_case):
    now, tracked_path, reporting_path, _, _ = live_case
    before_json = tracked_path.read_bytes()
    before_yaml = reporting_path.read_bytes()
    with pytest.raises(ValueError, match="already tracked"):
        replace_station.plan_changes(
            tracked_path,
            reporting_path,
            replace_station.OLD_ID,
            json.loads(tracked_path.read_text())[0],
            now + timedelta(hours=2),
        )
    assert tracked_path.read_bytes() == before_json
    assert reporting_path.read_bytes() == before_yaml


def test_cli_preview_and_apply_use_verified_live_feeds(live_case, monkeypatch, capsys):
    """Exercise the command's full workflow without contacting the Internet."""
    now, tracked_path, reporting_path, information, status = live_case
    monkeypatch.setattr(
        replace_station,
        "discover",
        lambda *_: {"station_information": "info", "station_status": "status"},
    )
    monkeypatch.setattr(
        replace_station,
        "fetch_json",
        lambda _, url: information if url == "info" else status,
    )
    cutover = (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    args = [
        "--tracked-file",
        str(tracked_path),
        "--reporting-file",
        str(reporting_path),
        "--effective-bucket",
        cutover,
    ]
    assert replace_station.main(args, now=now, client=object()) == 0
    preview = json.loads(capsys.readouterr().out)
    assert not preview["applied"]
    assert preview["new_station"]["station_id"] == "NEW_NEAR"
    assert replace_station.OLD_ID in json.loads(tracked_path.read_text())

    assert replace_station.main(args + ["--apply"], now=now, client=object()) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["applied"]
    assert result["new_station"]["station_id"] == "NEW_NEAR"
    assert "NEW_NEAR" in json.loads(tracked_path.read_text())
