"""Show published BikeWatch station availability with live freshness labels."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import streamlit as st
from dotenv import load_dotenv
from psycopg.rows import dict_row


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

st.set_page_config(
    page_title="BikeWatch | Current availability",
    page_icon="🚲",
    layout="wide",
)


def tracked_station_ids():
    """Load the same station selection used by the collector."""
    path = ROOT / "config/tracked_stations.json"
    values = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(values, list) or not values:
        raise ValueError("Expected a nonempty tracked-station array.")

    return values


@st.cache_data(ttl=900, show_spinner=False)
def read_published_snapshot():
    """Read the published mart and release health in one database snapshot."""
    dsn = os.environ["BIKEWATCH_DASHBOARD_URL"]
    ids = tracked_station_ids()

    with psycopg.connect(
        dsn,
        row_factory=dict_row,
        connect_timeout=10,
    ) as db:
        with db.transaction():
            db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")

            stations = db.execute(
                """
                SELECT
                    station_id,
                    station_name,
                    latitude,
                    longitude,
                    capacity,
                    bikes_available,
                    docks_available,
                    is_installed,
                    is_renting,
                    is_returning,
                    has_trusted_observation,
                    using_older_trusted_observation,
                    station_reported_at,
                    collected_at,
                    latest_received_at,
                    latest_quality_reason,
                    freshness_valid_until
                FROM analytics.mart_station_current
                WHERE station_id = ANY(%s)
                ORDER BY station_name NULLS LAST, station_id
                """,
                (ids,),
            ).fetchall()

            health = db.execute(
                """
                SELECT
                    latest_attempt_status,
                    latest_attempt_phase,
                    published_release_id,
                    published_at
                FROM analytics.pipeline_health
                """
            ).fetchone()

    return stations, health


def is_fresh(row, now):
    """Check expiry when the page renders, including cached database rows."""
    expires = row["freshness_valid_until"]

    return row["has_trusted_observation"] and expires is not None and expires > now


def station_status(row, now):
    """Give a station one clear, user-facing availability status."""
    if not row["has_trusted_observation"]:
        return "No trusted reading"

    if not is_fresh(row, now):
        return "Stale"

    if row["using_older_trusted_observation"]:
        return "Older trusted reading"

    if not row["is_installed"]:
        return "Not installed"

    if not row["is_renting"] and not row["is_returning"]:
        return "Closed"

    if not row["is_renting"]:
        return "Rentals closed"

    if not row["is_returning"]:
        return "Returns closed"

    if row["bikes_available"] == 0:
        return "No bikes"

    if row["docks_available"] == 0:
        return "No docks"

    return "Open"


def reported_age_minutes(row, now):
    """Measure station reporting age against the current display time."""
    reported = row["station_reported_at"]

    if reported is None:
        return None

    return max(0, int((now - reported).total_seconds() // 60))


def display_time(value):
    """Format a database timestamp for the page."""
    if value is None:
        return "—"

    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


@st.fragment(run_every="60s")
def show_current_availability():
    """Refresh freshness labels each minute while caching database reads."""
    try:
        stations, health = read_published_snapshot()
    except (KeyError, ValueError, FileNotFoundError, psycopg.Error) as error:
        st.error(
            f"Unable to read the published dashboard data ({type(error).__name__})."
        )
        return

    now = datetime.now(UTC)

    if health is None or health["published_release_id"] is None:
        st.warning("No reporting release has been published yet.")
        return

    st.caption(
        f"Published {display_time(health['published_at'])} · "
        f"Latest publication attempt: "
        f"{health['latest_attempt_status'] or 'unknown'} "
        f"({health['latest_attempt_phase'] or 'unknown phase'})"
    )

    if not stations:
        st.warning("No tracked stations are present in the published mart.")
        return

    fresh = [row for row in stations if is_fresh(row, now)]

    rentable_bikes = sum(
        row["bikes_available"]
        for row in fresh
        if row["is_installed"]
        and row["is_renting"]
        and row["bikes_available"] is not None
    )

    returnable_docks = sum(
        row["docks_available"]
        for row in fresh
        if row["is_installed"]
        and row["is_returning"]
        and row["docks_available"] is not None
    )

    first, second, third = st.columns(3)

    first.metric(
        "Stations with fresh readings",
        f"{len(fresh)} / {len(stations)}",
    )
    second.metric(
        "Known rentable bikes",
        str(rentable_bikes) if fresh else "—",
    )
    third.metric(
        "Known returnable docks",
        str(returnable_docks) if fresh else "—",
    )

    st.caption(
        "Totals include only fresh trusted readings and stations open "
        "for the corresponding service. Missing readings are not zero."
    )

    statuses = sorted({station_status(row, now) for row in stations})
    selected = st.multiselect(
        "Show station statuses",
        options=statuses,
        default=statuses,
    )

    visible = [row for row in stations if station_status(row, now) in selected]

    colors = {
        "Open": "#1F9D55",
        "No bikes": "#D64545",
        "No docks": "#7652B8",
        "Older trusted reading": "#E69F00",
        "Stale": "#808080",
        "No trusted reading": "#808080",
        "Not installed": "#D88737",
        "Closed": "#D88737",
        "Rentals closed": "#D88737",
        "Returns closed": "#D88737",
    }

    map_rows = [
        {
            "latitude": float(row["latitude"]),
            "longitude": float(row["longitude"]),
            "color": colors[station_status(row, now)],
        }
        for row in visible
        if row["latitude"] is not None and row["longitude"] is not None
    ]

    st.subheader("Station map")

    if map_rows:
        st.map(
            map_rows,
            latitude="latitude",
            longitude="longitude",
            color="color",
            size=55,
            zoom=12,
        )
    else:
        st.info("No selected stations have usable map coordinates.")

    st.caption(
        "Green open · Red no bikes · Purple no docks · "
        "Amber older trusted reading · Orange closed service · "
        "Gray stale or unavailable"
    )

    table_rows = []

    for row in visible:
        fresh_reading = is_fresh(row, now)

        table_rows.append(
            {
                "Station": row["station_name"] or row["station_id"],
                "Status": station_status(row, now),
                "Bikes observed": (row["bikes_available"] if fresh_reading else None),
                "Docks observed": (row["docks_available"] if fresh_reading else None),
                "Rentals open": (
                    bool(row["is_installed"] and row["is_renting"])
                    if fresh_reading
                    else None
                ),
                "Returns open": (
                    bool(row["is_installed"] and row["is_returning"])
                    if fresh_reading
                    else None
                ),
                "Station age (minutes)": (
                    reported_age_minutes(row, now) if fresh_reading else None
                ),
                "Station reported": display_time(row["station_reported_at"]),
                "Latest received": display_time(row["latest_received_at"]),
                "Latest quality reason": (row["latest_quality_reason"] or "—"),
                "Station ID": row["station_id"],
            }
        )

    st.subheader("Station details")
    st.dataframe(
        table_rows,
        hide_index=True,
        use_container_width=True,
    )

    fallback_count = sum(
        bool(row["using_older_trusted_observation"]) for row in stations
    )

    if fallback_count:
        st.warning(
            f"{fallback_count} station(s) use an older trusted reading "
            "because their latest received observation was not eligible."
        )


st.title("BikeWatch current availability")

if st.button("Refresh published data now"):
    read_published_snapshot.clear()
    st.rerun()

show_current_availability()
