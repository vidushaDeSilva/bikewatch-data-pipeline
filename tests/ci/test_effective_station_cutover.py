"""Test collector's population before and after a scheduled configuration change."""

import json
from datetime import UTC, datetime, timedelta

from bikewatch.ingestion import collect as collector
from scripts import replace_station


def test_effective_population_switches_at_exact_cutover(tmp_path, monkeypatch):
    """The old ID remains active until the declared UTC boundary."""
    tracked_file = tmp_path / "tracked.json"
    reporting_file = tmp_path / "reporting.yml"
    # Build a stable pre-migration fixture. Production config is expected to
    # change after the real cutover and must not make this regression test fail.
    original_ids = [replace_station.OLD_ID] + [f"TRACKED_{index:02d}" for index in range(19)]
    tracked_file.write_text(json.dumps(original_ids, indent=2) + "\n", encoding="utf-8")
    reporting_lines = [
        "reporting_timezone: America/New_York",
        "feed_stale_after_seconds: 2700",
        "",
        "coverage_tracking_periods:",
    ]
    for station_id in sorted(original_ids):
        reporting_lines.extend(
            [
                f'  - station_id: "{station_id}"',
                '    start_bucket: "2026-10-03T08:00:00Z"',
                "    end_bucket: null",
                "",
            ]
        )
    reporting_file.write_text("\n".join(reporting_lines), encoding="utf-8")
    now = datetime(2026, 11, 2, 12, tzinfo=UTC)
    cutover = now + timedelta(hours=2)
    new_json, new_yaml = replace_station.plan_changes(
        tracked_file, reporting_file, replace_station.OLD_ID, "NEW_STATION", cutover
    )
    replace_station.write_configs(tracked_file, reporting_file, new_json, new_yaml)
    monkeypatch.setattr(collector, "STATIONS_FILE", tracked_file)
    monkeypatch.setattr(collector, "REPORTING_FILE", reporting_file)

    early = collector.load_effective_station_ids(cutover - timedelta(microseconds=1))
    late = collector.load_effective_station_ids(cutover)
    assert len(early) == len(late) == 20
    assert replace_station.OLD_ID in early
    assert "NEW_STATION" not in early
    assert "NEW_STATION" in late
    assert replace_station.OLD_ID not in late
    assert late == set(json.loads(tracked_file.read_text()))
