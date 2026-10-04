"""Show historical station reliability with explicit sample coverage."""

import json
from datetime import timedelta

import altair as alt
import pandas as pd
import streamlit as st

from dashboard.db import clear_query_cache, query_dataframe
from dashboard.metrics import format_rate, safe_rate

st.set_page_config(
    page_title="BikeWatch reliability",
    page_icon="📊",
    layout="wide",
)

st.title("Station reliability")
st.caption("Historical bike and dock reliability using published, quality-controlled observations.")


COUNT_COLUMNS = [
    "expected_observation_count",
    "scheduled_received_count",
    "scheduled_usable_count",
    "missing_observation_count",
    "availability_sample_count",
    "rental_sample_count",
    "return_sample_count",
    "empty_sample_count",
    "full_sample_count",
    "bikes_sample_sum",
    "docks_sample_sum",
]


def add_rates(frame: pd.DataFrame) -> pd.DataFrame:
    """Add weighted reliability and coverage rates."""
    result = frame.copy()

    def percentage(numerator: str, denominator: str) -> pd.Series:
        divisor = result[denominator].where(result[denominator] > 0)
        return 100.0 * result[numerator] / divisor

    result["collection_coverage"] = percentage(
        "scheduled_received_count",
        "expected_observation_count",
    )

    result["usable_coverage"] = percentage(
        "scheduled_usable_count",
        "expected_observation_count",
    )

    result["empty_rate"] = percentage(
        "empty_sample_count",
        "rental_sample_count",
    )

    result["full_rate"] = percentage(
        "full_sample_count",
        "return_sample_count",
    )

    result["bike_availability_rate"] = 100.0 - result["empty_rate"]
    result["dock_availability_rate"] = 100.0 - result["full_rate"]

    result["average_bikes"] = result["bikes_sample_sum"] / result["rental_sample_count"].where(
        result["rental_sample_count"] > 0
    )

    result["average_docks"] = result["docks_sample_sum"] / result["return_sample_count"].where(
        result["return_sample_count"] > 0
    )

    return result


def aggregate_rows(
    frame: pd.DataFrame,
    groups: list[str],
) -> pd.DataFrame:
    """Aggregate count numerators before calculating rates."""
    aggregated = frame.groupby(groups, dropna=False)[COUNT_COLUMNS].sum().reset_index()

    return add_rates(aggregated)


if st.button("Refresh reliability data"):
    clear_query_cache()
    st.rerun()


bounds = query_dataframe(
    """
    SELECT
        min(reporting_date) AS minimum_date,
        max(reporting_date) AS maximum_date
    FROM analytics.mart_station_daily
    """
)

stations = query_dataframe(
    """
    SELECT
        station_id,
        coalesce(station_name, station_id) AS station_name
    FROM analytics.mart_station_current
    ORDER BY coalesce(station_name, station_id), station_id
    """
)

if bounds.empty or bounds.iloc[0]["minimum_date"] is None or stations.empty:
    st.info("No published reliability history is available yet.")
    st.stop()


minimum_date = pd.Timestamp(bounds.iloc[0]["minimum_date"]).date()

maximum_date = pd.Timestamp(bounds.iloc[0]["maximum_date"]).date()

default_start = max(
    minimum_date,
    maximum_date - timedelta(days=29),
)

station_labels = {
    f"{row.station_name} ({row.station_id})": row.station_id for row in stations.itertuples()
}

with st.sidebar:
    st.header("Reliability filters")

    start_date = st.date_input(
        "Start date",
        value=default_start,
        min_value=minimum_date,
        max_value=maximum_date,
    )

    end_date = st.date_input(
        "End date",
        value=maximum_date,
        min_value=minimum_date,
        max_value=maximum_date,
    )

    selected_labels = st.multiselect(
        "Stations",
        options=list(station_labels),
        default=list(station_labels),
    )

    day_scope = st.selectbox(
        "Day type",
        ["All days", "Weekdays", "Weekends"],
    )

    minimum_coverage = st.slider(
        "Minimum usable coverage for ranking",
        min_value=0,
        max_value=100,
        value=60,
        step=5,
        format="%d%%",
    )


if start_date > end_date:
    st.error("The start date must not be later than the end date.")
    st.stop()

selected_station_ids = [station_labels[label] for label in selected_labels]

if not selected_station_ids:
    st.info("Select at least one station.")
    st.stop()


history = query_dataframe(
    """
    SELECT
        hourly.station_id,
        coalesce(current.station_name, hourly.station_id)
            AS station_name,
        hourly.hour_start_utc,
        hourly.expected_observation_count,
        hourly.scheduled_received_count,
        hourly.scheduled_usable_count,
        hourly.missing_observation_count,
        hourly.availability_sample_count,
        hourly.rental_sample_count,
        hourly.return_sample_count,
        hourly.empty_sample_count,
        hourly.full_sample_count,
        hourly.bikes_sample_sum,
        hourly.docks_sample_sum
    FROM analytics.mart_station_hourly AS hourly
    LEFT JOIN analytics.mart_station_current AS current
        USING (station_id)
    WHERE (
        hourly.hour_start_utc
            AT TIME ZONE 'America/New_York'
    )::date BETWEEN %s AND %s
      AND hourly.station_id IN (
          SELECT jsonb_array_elements_text(%s::jsonb)
      )
    ORDER BY hourly.hour_start_utc, hourly.station_id
    """,
    (
        start_date,
        end_date,
        json.dumps(selected_station_ids),
    ),
)

if history.empty:
    st.info("No observations match the selected filters.")
    st.stop()


for column in COUNT_COLUMNS:
    history[column] = pd.to_numeric(
        history[column],
        errors="coerce",
    ).fillna(0)

history["local_time"] = pd.to_datetime(
    history["hour_start_utc"],
    utc=True,
).dt.tz_convert("America/New_York")

history["local_hour"] = history["local_time"].dt.hour
history["day_name"] = history["local_time"].dt.day_name()
history["is_weekend"] = history["local_time"].dt.dayofweek >= 5

if day_scope == "Weekdays":
    history = history.loc[~history["is_weekend"]]
elif day_scope == "Weekends":
    history = history.loc[history["is_weekend"]]

if history.empty:
    st.info("No observations remain after applying the day filter.")
    st.stop()


totals = history[COUNT_COLUMNS].sum()

collection_coverage = safe_rate(
    totals["scheduled_received_count"],
    totals["expected_observation_count"],
)

usable_coverage = safe_rate(
    totals["scheduled_usable_count"],
    totals["expected_observation_count"],
)

empty_rate = safe_rate(
    totals["empty_sample_count"],
    totals["rental_sample_count"],
)

full_rate = safe_rate(
    totals["full_sample_count"],
    totals["return_sample_count"],
)

bike_availability = None if empty_rate is None else 100.0 - empty_rate

dock_availability = None if full_rate is None else 100.0 - full_rate


first, second, third, fourth, fifth = st.columns(5)

first.metric(
    "Bike availability",
    format_rate(bike_availability),
)

second.metric(
    "Dock availability",
    format_rate(dock_availability),
)

third.metric(
    "Empty observations",
    format_rate(empty_rate),
)

fourth.metric(
    "Full observations",
    format_rate(full_rate),
)

fifth.metric(
    "Usable coverage",
    format_rate(usable_coverage),
    help=("Quality-eligible scheduled samples divided by expected scheduled samples."),
)

st.caption(
    f"{int(totals['rental_sample_count']):,} rental samples · "
    f"{int(totals['return_sample_count']):,} return samples · "
    f"{int(totals['missing_observation_count']):,} missing "
    f"station intervals · collection coverage "
    f"{format_rate(collection_coverage)}"
)


station_summary = aggregate_rows(
    history,
    ["station_id", "station_name"],
)

ranking = station_summary.loc[station_summary["usable_coverage"] >= minimum_coverage].copy()

st.subheader("Station comparison")

if ranking.empty:
    st.warning("No station meets the selected minimum usable coverage.")
else:
    ranking_chart = ranking.melt(
        id_vars=["station_id", "station_name"],
        value_vars=[
            "bike_availability_rate",
            "dock_availability_rate",
        ],
        var_name="metric",
        value_name="rate",
    )

    ranking_chart["metric"] = ranking_chart["metric"].map(
        {
            "bike_availability_rate": "Bike availability",
            "dock_availability_rate": "Dock availability",
        }
    )

    chart = (
        alt.Chart(ranking_chart)
        .mark_bar()
        .encode(
            x=alt.X(
                "rate:Q",
                title="Eligible observations with service available (%)",
                scale=alt.Scale(domain=[0, 100]),
            ),
            y=alt.Y(
                "station_name:N",
                title=None,
                sort="-x",
            ),
            color=alt.Color(
                "metric:N",
                title=None,
            ),
            tooltip=[
                alt.Tooltip("station_name:N", title="Station"),
                alt.Tooltip("metric:N", title="Metric"),
                alt.Tooltip(
                    "rate:Q",
                    title="Rate",
                    format=".1f",
                ),
            ],
        )
        .properties(height=max(320, 24 * len(ranking)))
    )

    st.altair_chart(chart, use_container_width=True)


st.subheader("Hourly pattern")

hourly = aggregate_rows(
    history,
    ["local_hour"],
)

hourly_chart = hourly.melt(
    id_vars=["local_hour"],
    value_vars=[
        "empty_rate",
        "full_rate",
        "bike_availability_rate",
        "dock_availability_rate",
    ],
    var_name="metric",
    value_name="rate",
)

hourly_chart["metric"] = hourly_chart["metric"].map(
    {
        "empty_rate": "Empty rate",
        "full_rate": "Full rate",
        "bike_availability_rate": "Bike availability",
        "dock_availability_rate": "Dock availability",
    }
)

line_chart = (
    alt.Chart(hourly_chart)
    .mark_line(point=True)
    .encode(
        x=alt.X(
            "local_hour:O",
            title="Hour in America/New_York",
            sort=list(range(24)),
        ),
        y=alt.Y(
            "rate:Q",
            title="Rate (%)",
            scale=alt.Scale(domain=[0, 100]),
        ),
        color=alt.Color("metric:N", title=None),
        tooltip=[
            alt.Tooltip("local_hour:O", title="Local hour"),
            alt.Tooltip("metric:N", title="Metric"),
            alt.Tooltip("rate:Q", title="Rate", format=".1f"),
        ],
    )
    .properties(height=360)
)

st.altair_chart(line_chart, use_container_width=True)


st.subheader("Weekday and hour heatmap")

heatmap_metric = st.selectbox(
    "Heatmap metric",
    [
        "Empty rate",
        "Full rate",
        "Bike availability",
        "Dock availability",
        "Usable coverage",
    ],
)

metric_columns = {
    "Empty rate": "empty_rate",
    "Full rate": "full_rate",
    "Bike availability": "bike_availability_rate",
    "Dock availability": "dock_availability_rate",
    "Usable coverage": "usable_coverage",
}

weekday_hour = aggregate_rows(
    history,
    ["day_name", "local_hour"],
)

weekday_hour["display_rate"] = weekday_hour[metric_columns[heatmap_metric]]

day_order = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]

heatmap = (
    alt.Chart(weekday_hour)
    .mark_rect()
    .encode(
        x=alt.X(
            "local_hour:O",
            title="Hour in America/New_York",
            sort=list(range(24)),
        ),
        y=alt.Y(
            "day_name:N",
            title=None,
            sort=day_order,
        ),
        color=alt.Color(
            "display_rate:Q",
            title="Rate (%)",
            scale=alt.Scale(
                domain=[0, 100],
                scheme="viridis",
            ),
        ),
        tooltip=[
            alt.Tooltip("day_name:N", title="Day"),
            alt.Tooltip("local_hour:O", title="Local hour"),
            alt.Tooltip(
                "display_rate:Q",
                title=heatmap_metric,
                format=".1f",
            ),
            alt.Tooltip(
                "expected_observation_count:Q",
                title="Expected samples",
                format=",.0f",
            ),
            alt.Tooltip(
                "availability_sample_count:Q",
                title="Usable samples",
                format=",.0f",
            ),
        ],
    )
    .properties(height=280)
)

st.altair_chart(heatmap, use_container_width=True)


st.subheader("Weekday compared with weekend")

history["day_type"] = history["is_weekend"].map(
    {
        False: "Weekday",
        True: "Weekend",
    }
)

day_type = aggregate_rows(
    history,
    ["day_type"],
)

day_type_chart = day_type.melt(
    id_vars=["day_type"],
    value_vars=[
        "empty_rate",
        "full_rate",
        "bike_availability_rate",
        "dock_availability_rate",
    ],
    var_name="metric",
    value_name="rate",
)

day_type_chart["metric"] = day_type_chart["metric"].map(
    {
        "empty_rate": "Empty rate",
        "full_rate": "Full rate",
        "bike_availability_rate": "Bike availability",
        "dock_availability_rate": "Dock availability",
    }
)

comparison = (
    alt.Chart(day_type_chart)
    .mark_bar()
    .encode(
        x=alt.X("day_type:N", title=None),
        y=alt.Y(
            "rate:Q",
            title="Rate (%)",
            scale=alt.Scale(domain=[0, 100]),
        ),
        color=alt.Color("metric:N", title=None),
        xOffset="metric:N",
        tooltip=[
            alt.Tooltip("day_type:N", title="Day type"),
            alt.Tooltip("metric:N", title="Metric"),
            alt.Tooltip("rate:Q", title="Rate", format=".1f"),
        ],
    )
    .properties(height=330)
)

st.altair_chart(comparison, use_container_width=True)


st.subheader("Coverage and sample counts")

display = station_summary[
    [
        "station_name",
        "station_id",
        "collection_coverage",
        "usable_coverage",
        "expected_observation_count",
        "scheduled_received_count",
        "missing_observation_count",
        "rental_sample_count",
        "return_sample_count",
        "empty_sample_count",
        "full_sample_count",
        "average_bikes",
        "average_docks",
    ]
].copy()

display = display.rename(
    columns={
        "station_name": "Station",
        "station_id": "Station ID",
        "collection_coverage": "Collection coverage (%)",
        "usable_coverage": "Usable coverage (%)",
        "expected_observation_count": "Expected",
        "scheduled_received_count": "Received",
        "missing_observation_count": "Missing",
        "rental_sample_count": "Bike samples",
        "return_sample_count": "Dock samples",
        "empty_sample_count": "Empty samples",
        "full_sample_count": "Full samples",
        "average_bikes": "Average bikes",
        "average_docks": "Average docks",
    }
)

st.dataframe(
    display.sort_values(
        "Usable coverage (%)",
        ascending=False,
    ),
    use_container_width=True,
    hide_index=True,
)
