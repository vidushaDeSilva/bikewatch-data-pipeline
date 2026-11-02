"""Safely plan and apply a Citi Bike tracked-station replacement.

Read the provider's live GBFS 1.1 metadata/status feeds, rank eligible untracked
Midtown stations, and preserve historical dbt coverage by ending the old period
at exactly the new period's start. Preview is read-only; --apply updates both
configuration files, with rollback if either write fails. Never edits the DB.
"""

import argparse
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import httpx
from pydantic import ValidationError

from bikewatch.ingestion.gbfs import discover, fetch_json
from bikewatch.ingestion.models import StationEnvelope, StationInformation, StationStatus
from scripts.reporting_config import load_reporting_config, utc_boundary

ROOT = Path(__file__).resolve().parents[1]
TRACKED_FILE = ROOT / "config/tracked_stations.json"
REPORTING_FILE = ROOT / "config/bikewatch_reporting_vars.yml"
OLD_ID = "2004153375425087884"
DISCOVERY_URL = "https://gbfs.citibikenyc.com/gbfs/gbfs.json"
# Preserve the initial BikeWatch station-sampling area and selection centroid.
LAT_MIN, LAT_MAX = 40.750, 40.770
LON_MIN, LON_MAX = -73.995, -73.975
CENTRE_LAT, CENTRE_LON = 40.7580, -73.9855


def distance_meters(latitude, longitude):
    """Haversine distance from BikeWatch's original Midtown centroid."""
    earth_radius = 6_371_000
    a_lat, b_lat = radians(CENTRE_LAT), radians(latitude)
    delta_lat = b_lat - a_lat
    delta_lon = radians(longitude - CENTRE_LON)
    arc = sin(delta_lat / 2) ** 2 + cos(a_lat) * cos(b_lat) * sin(delta_lon / 2) ** 2
    return 2 * earth_radius * asin(sqrt(arc))


def checked_feed(payload, label, now):
    """Reject invalid or stale responses rather than selecting from old data."""
    envelope = StationEnvelope.model_validate(payload)
    updated = datetime.fromtimestamp(envelope.last_updated, UTC)
    age = (now - updated).total_seconds()
    if not -300 <= age <= 900:
        raise ValueError(f"{label} feed is stale or future-dated: cannot choose a station")
    return envelope.data.stations, updated


def rank_replacements(information, status, tracked, now):
    """Select stations currently listed, operational and in the original area."""
    info_rows, info_updated = checked_feed(information, "information", now)
    status_rows, status_updated = checked_feed(status, "status", now)

    statuses = {}
    for row in status_rows:
        try:
            state = StationStatus.model_validate(row)
        except ValidationError:
            continue
        if state.station_id in statuses:
            raise ValueError("Duplicate station ID in source station_status feed")
        statuses[state.station_id] = state

    # Refuse migration if a different tracked station is missing too.

    ranked = []
    information_ids = set()
    for row in info_rows:
        try:
            station = StationInformation.model_validate(row)
        except ValidationError:
            continue
        if station.station_id in information_ids:
            raise ValueError("Duplicate station ID in source station_information feed")
        information_ids.add(station.station_id)
        state = statuses.get(station.station_id)
        if (
            station.station_id in tracked
            or not LAT_MIN <= station.lat <= LAT_MAX
            or not LON_MIN <= station.lon <= LON_MAX
            or station.capacity <= 0
            or state is None
            or state.is_installed != 1
            or state.is_renting != 1
            or state.is_returning != 1
            or now.timestamp() - state.last_reported > 2700
            or state.last_reported - now.timestamp() > 300
        ):
            continue

        ranked.append(
            {
                "station_id": station.station_id,
                "name": station.name,
                "latitude": station.lat,
                "longitude": station.lon,
                "capacity": station.capacity,
                "distance_m": round(distance_meters(station.lat, station.lon)),
            }
        )

    unexpected_missing = (tracked - {OLD_ID}) - (information_ids & set(statuses))
    if unexpected_missing:
        raise ValueError(
            "Other tracked stations are absent from the live GBFS pair: "
            + ", ".join(sorted(unexpected_missing))
        )

    ranked.sort(key=lambda item: (item["distance_m"], item["station_id"]))
    if not ranked:
        raise ValueError("No suitable operational replacement in the Midtown study area")
    return ranked, {"information": info_updated.isoformat(), "status": status_updated.isoformat()}


def plan_changes(tracked_path, reporting_path, old_id, new_id, effective_bucket):
    """Create two validated configuration files without altering old history."""
    station_ids = json.loads(tracked_path.read_text(encoding="utf-8"))
    if not isinstance(station_ids, list) or len(station_ids) != 20 or len(set(station_ids)) != 20:
        raise ValueError("Expected exactly 20 distinct tracked station IDs")
    if station_ids.count(old_id) != 1 or new_id in station_ids or new_id == old_id:
        raise ValueError("Old station is absent or the proposed station is already tracked")

    config = load_reporting_config(reporting_path)
    periods = config.get("coverage_tracking_periods", [])
    current = {
        period["station_id"]
        for period in periods
        if utc_boundary(period["start_bucket"]) <= effective_bucket
        and (
            period.get("end_bucket") is None
            or effective_bucket < utc_boundary(period["end_bucket"])
        )
    }
    if current != set(station_ids):
        raise ValueError("Active reporting periods at cutover must match the 20 existing IDs")

    matches = [
        period
        for period in periods
        if period["station_id"] == old_id and period.get("end_bucket") is None
    ]
    if len(matches) != 1 or utc_boundary(matches[0]["start_bucket"]) >= effective_bucket:
        raise ValueError("Old station must have one open period starting before the cutover")

    # Preserve the YAML formatting and comments instead of dumping the full file.
    original = reporting_path.read_text(encoding="utf-8")
    needle = (
        f'  - station_id: "{old_id}"\n'
        f'    start_bucket: "{matches[0]["start_bucket"]}"\n'
        "    end_bucket: null\n"
    )
    if original.count(needle) != 1:
        raise ValueError("Could not locate the exact old station interval; no files changed")

    timestamp = effective_bucket.strftime("%Y-%m-%dT%H:%M:%SZ")
    replacement = needle.replace("end_bucket: null", f'end_bucket: "{timestamp}"') + (
        f'\n  - station_id: "{new_id}"\n    start_bucket: "{timestamp}"\n    end_bucket: null\n'
    )
    updated_yaml = original.replace(needle, replacement)
    updated_stations = [new_id if value == old_id else value for value in station_ids]
    updated_json = json.dumps(updated_stations, indent=2) + "\n"

    # Validate the new YAML with the project's actual parser before writing.
    with tempfile.TemporaryDirectory() as directory:
        scratch = Path(directory) / "reporting.yml"
        scratch.write_text(updated_yaml, encoding="utf-8")
        checked = load_reporting_config(scratch)
        if len(checked["coverage_tracking_periods"]) != len(periods) + 1:
            raise ValueError("Tracking-period migration did not create one new interval")
    return updated_json, updated_yaml


def write_configs(tracked_path, reporting_path, tracked_json, reporting_yaml):
    """Stage both files and restore originals if a replacement write fails."""
    originals = {
        tracked_path: tracked_path.read_bytes(),
        reporting_path: reporting_path.read_bytes(),
    }
    pending = {}
    try:
        for path, contents in (
            (tracked_path, tracked_json),
            (reporting_path, reporting_yaml),
        ):
            handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(contents)
            pending[path] = Path(temp_name)

        for path, staged in pending.items():
            os.replace(staged, path)
    except Exception:
        # Two different files cannot be changed in one filesystem transaction.
        # Restore their previous bytes on write failure; CI also checks parity.
        for path, original in originals.items():
            path.write_bytes(original)
        raise
    finally:
        for staged in pending.values():
            staged.unlink(missing_ok=True)


def main(argv=None, *, now=None, client=None):
    """Print ranked candidates; only --apply writes the verified migration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write both configs")
    parser.add_argument("--station-id", help="Select a specific verified candidate")
    parser.add_argument("--effective-bucket", help="UTC 15-minute bucket for historical cutover")
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--tracked-file", type=Path, default=TRACKED_FILE, help=argparse.SUPPRESS)
    parser.add_argument(
        "--reporting-file", type=Path, default=REPORTING_FILE, help=argparse.SUPPRESS
    )
    args = parser.parse_args(argv)

    now = now or datetime.now(UTC)
    if args.effective_bucket:
        effective_bucket = utc_boundary(args.effective_bucket)
    else:
        # Choose a future whole hour with time to review and merge the patch.
        effective_bucket = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=3)
    if effective_bucket < now + timedelta(minutes=30):
        raise ValueError("Cutover must be at least 30 minutes in the future")

    tracked = set(json.loads(args.tracked_file.read_text(encoding="utf-8")))
    if OLD_ID not in tracked:
        raise ValueError("Old station already absent; inspect your configuration")

    owns_client = client is None
    if owns_client:
        client = httpx.Client(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": "BikeWatch/0.1"},
        )
    try:
        feeds = discover(client, os.getenv("GBFS_DISCOVERY_URL", DISCOVERY_URL))
        information = fetch_json(client, feeds["station_information"])
        status = fetch_json(client, feeds["station_status"])
    finally:
        if owns_client:
            client.close()

    ranked, timestamps = rank_replacements(information, status, tracked, now)
    if args.station_id and args.station_id not in {row["station_id"] for row in ranked}:
        raise ValueError("The selected ID is not an eligible live replacement")
    chosen = next((row for row in ranked if row["station_id"] == args.station_id), ranked[0])
    changed = plan_changes(
        args.tracked_file,
        args.reporting_file,
        OLD_ID,
        chosen["station_id"],
        effective_bucket,
    )
    if args.apply:
        write_configs(args.tracked_file, args.reporting_file, *changed)

    print(
        json.dumps(
            {
                "applied": args.apply,
                "retiring_station_id": OLD_ID,
                "new_station": chosen,
                "cutover_utc": effective_bucket.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "source_feed_last_updated": timestamps,
                "ranked_candidates": ranked[: max(1, args.top)],
                "action": (
                    "Review and commit both configs before the future cutover"
                    if args.apply
                    else "Preview only; add --apply after review"
                ),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
