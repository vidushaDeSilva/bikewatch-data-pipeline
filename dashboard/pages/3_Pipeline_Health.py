"""Monitor BikeWatch ingestion, publication, and interval coverage."""

from datetime import timedelta

import altair as alt
import pandas as pd
import streamlit as st

from dashboard.db import clear_query_cache, query_dataframe
from dashboard.metrics import (
    age_minutes,
    classify_pipeline_health,
    format_age,
)

st.set_page_config(
    page_title="BikeWatch pipeline health",
    page_icon="🩺",
    layout="wide",
)

st.title("Pipeline health")
st.caption(
    "Live operational state from collector audits, publication history, "
    "source timestamps, and expected 15-minute intervals."
)


if st.button("Refresh pipeline health"):
    clear_query_cache()
    st.rerun()


window_labels = {
    "Last 24 hours": 24,
    "Last 3 days": 72,
    "Last 7 days": 168,
    "Last 30 days": 720,
}

selected_window = st.selectbox(
    "Monitoring window",
    list(window_labels),
    index=0,
)

window_hours = window_labels[selected_window]
now_utc = pd.Timestamp.now(tz="UTC")
window_start = now_utc - timedelta(hours=window_hours)


publication_health = query_dataframe(
    """
    SELECT *
    FROM analytics.pipeline_health
    """
)

source_summary = query_dataframe(
    """
    SELECT
        max(latest_received_at) AS latest_source_received_at,
        max(collected_at) AS latest_trusted_collected_at,
        count(*) AS station_count,
        count(*) FILTER (
            WHERE has_trusted_observation
              AND current_timestamp <= freshness_valid_until
        ) AS fresh_station_count,
        count(*) FILTER (
            WHERE NOT has_trusted_observation
               OR current_timestamp > freshness_valid_until
        ) AS stale_station_count
    FROM analytics.mart_station_current
    """
)

runs = query_dataframe(
    """
    SELECT *
    FROM analytics.dashboard_ingestion_runs
    WHERE started_at >= %s
    ORDER BY started_at DESC, run_id DESC
    """,
    (window_start.to_pydatetime(),),
)

publications = query_dataframe(
    """
    SELECT *
    FROM analytics.dashboard_publication_runs
    WHERE started_at >= %s
    ORDER BY started_at DESC, run_id DESC
    """,
    (window_start.to_pydatetime(),),
)

intervals = query_dataframe(
    """
    SELECT *
    FROM analytics.dashboard_collection_intervals
    WHERE collection_bucket >= %s
    ORDER BY collection_bucket
    """,
    (window_start.to_pydatetime(),),
)

rejections = query_dataframe(
    """
    SELECT *
    FROM analytics.dashboard_rejection_summary
    WHERE run_started_at >= %s
    ORDER BY run_started_at DESC, run_id DESC
    """,
    (window_start.to_pydatetime(),),
)


if publication_health.empty:
    publication = {}
else:
    publication = publication_health.iloc[0].to_dict()

if source_summary.empty:
    source = {}
else:
    source = source_summary.iloc[0].to_dict()


latest_ingestion = runs.iloc[0].to_dict() if not runs.empty else {}

successful_runs = runs.loc[runs["run_status"] == "succeeded"] if not runs.empty else pd.DataFrame()

latest_successful_ingestion = successful_runs.iloc[0].to_dict() if not successful_runs.empty else {}


latest_source_at = source.get("latest_source_received_at")
published_reporting_as_of = publication.get("published_reporting_as_of")

source_age = age_minutes(latest_source_at)
publication_age = age_minutes(published_reporting_as_of)

last_24_hours = now_utc - timedelta(hours=24)

recent_intervals = (
    intervals.loc[
        pd.to_datetime(
            intervals["collection_bucket"],
            utc=True,
        )
        >= last_24_hours
    ]
    if not intervals.empty
    else pd.DataFrame()
)

missing_intervals_24h = (
    int((recent_intervals["interval_status"] == "missing").sum())
    if not recent_intervals.empty
    else 0
)

rejected_records_24h = 0

if not rejections.empty:
    recent_rejections = rejections.loc[
        pd.to_datetime(
            rejections["run_started_at"],
            utc=True,
        )
        >= last_24_hours
    ]

    rejected_records_24h = int(
        pd.to_numeric(
            recent_rejections["rejected_record_count"],
            errors="coerce",
        )
        .fillna(0)
        .sum()
    )


assessment = classify_pipeline_health(
    source_age_minutes=source_age,
    publication_age_minutes=publication_age,
    latest_ingestion_status=latest_ingestion.get("run_status"),
    latest_publication_status=publication.get("latest_attempt_status"),
    missing_intervals_24h=missing_intervals_24h,
    rejected_records_24h=rejected_records_24h,
)

status_messages = {
    "green": st.success,
    "orange": st.warning,
    "red": st.error,
    "gray": st.info,
}

status_messages[assessment.colour](f"{assessment.state}: {assessment.explanation}")


def display_timestamp(value: object) -> str:
    """Format a timestamp in UTC for operational display."""
    if value is None or pd.isna(value):
        return "—"

    return pd.Timestamp(value).tz_convert("UTC").strftime("%Y-%m-%d %H:%M:%S UTC")


first, second, third, fourth = st.columns(4)

first.metric(
    "Latest source age",
    format_age(source_age),
)

second.metric(
    "Published cutoff age",
    format_age(publication_age),
)

third.metric(
    "Missing intervals (24 h)",
    f"{missing_intervals_24h:,}",
)

fourth.metric(
    "Rejected records (24 h)",
    f"{rejected_records_24h:,}",
)


fifth, sixth, seventh, eighth = st.columns(4)

fifth.metric(
    "Fresh stations",
    (f"{int(source.get('fresh_station_count', 0))}/{int(source.get('station_count', 0))}"),
)

sixth.metric(
    "Latest ingestion",
    latest_ingestion.get("run_status", "—"),
)

seventh.metric(
    "Latest publication",
    publication.get("latest_attempt_status", "—"),
)

latest_duration = latest_ingestion.get("duration_seconds")

eighth.metric(
    "Latest ingestion duration",
    (
        "—"
        if latest_duration is None or pd.isna(latest_duration)
        else f"{float(latest_duration):.1f} s"
    ),
)


st.subheader("Latest successful stages")

stage_left, stage_right = st.columns(2)

with stage_left:
    st.markdown("#### Ingestion")
    st.write(
        "Latest attempt:",
        display_timestamp(latest_ingestion.get("started_at")),
    )
    st.write(
        "Latest success:",
        display_timestamp(latest_successful_ingestion.get("finished_at")),
    )
    st.write(
        "Stage:",
        latest_ingestion.get("stage", "—"),
    )
    st.write(
        "Error type:",
        latest_ingestion.get("error_type") or "None",
    )

with stage_right:
    st.markdown("#### Publication")
    st.write(
        "Latest attempt:",
        display_timestamp(publication.get("latest_attempt_started_at")),
    )
    st.write(
        "Current published release:",
        display_timestamp(publication.get("published_at")),
    )
    st.write(
        "Published reporting cutoff:",
        display_timestamp(publication.get("published_reporting_as_of")),
    )
    st.write(
        "Latest phase:",
        publication.get("latest_attempt_phase") or "—",
    )
    st.write(
        "Error type:",
        publication.get("latest_attempt_error_type") or "None",
    )


st.subheader("Expected collection intervals")

if intervals.empty:
    st.info("No interval history is available.")
else:
    intervals["collection_bucket"] = pd.to_datetime(
        intervals["collection_bucket"],
        utc=True,
    )

    interval_counts = intervals.groupby("interval_status").size().rename("count").reset_index()

    status_colours = {
        "succeeded": "#2ca02c",
        "partial": "#ffbf00",
        "failed": "#d62728",
        "missing": "#7f7f7f",
        "running": "#1f77b4",
        "awaiting": "#c7c7c7",
    }

    interval_chart = (
        alt.Chart(intervals)
        .mark_tick(thickness=3, size=35)
        .encode(
            x=alt.X(
                "collection_bucket:T",
                title="Collection bucket (UTC)",
            ),
            color=alt.Color(
                "interval_status:N",
                title="Status",
                scale=alt.Scale(
                    domain=list(status_colours),
                    range=list(status_colours.values()),
                ),
            ),
            tooltip=[
                alt.Tooltip(
                    "collection_bucket:T",
                    title="Bucket",
                ),
                alt.Tooltip(
                    "interval_status:N",
                    title="Status",
                ),
                alt.Tooltip(
                    "run_count:Q",
                    title="Audit runs",
                ),
                alt.Tooltip(
                    "latest_started_at:T",
                    title="Latest start",
                ),
            ],
        )
        .properties(height=120)
    )

    st.altair_chart(
        interval_chart,
        use_container_width=True,
    )

    st.dataframe(
        interval_counts,
        use_container_width=True,
        hide_index=True,
    )

    missing_table = intervals.loc[
        intervals["interval_status"].isin(["missing", "failed", "partial"]),
        [
            "collection_bucket",
            "interval_status",
            "run_count",
            "latest_started_at",
            "latest_finished_at",
            "latest_run_status",
        ],
    ].sort_values(
        "collection_bucket",
        ascending=False,
    )

    if not missing_table.empty:
        with st.expander("Show missing, failed, and partial intervals"):
            st.dataframe(
                missing_table,
                use_container_width=True,
                hide_index=True,
            )


st.subheader("Processing duration")

duration_frames = []

if not runs.empty:
    ingestion_duration = runs[
        [
            "started_at",
            "duration_seconds",
            "run_status",
        ]
    ].copy()

    ingestion_duration = ingestion_duration.rename(
        columns={
            "started_at": "timestamp",
            "run_status": "status",
        }
    )

    ingestion_duration["process"] = "Ingestion"
    duration_frames.append(ingestion_duration)

if not publications.empty:
    publication_duration = publications[
        [
            "started_at",
            "duration_seconds",
            "publication_status",
        ]
    ].copy()

    publication_duration = publication_duration.rename(
        columns={
            "started_at": "timestamp",
            "publication_status": "status",
        }
    )

    publication_duration["process"] = "Publication"
    duration_frames.append(publication_duration)

if duration_frames:
    durations = pd.concat(
        duration_frames,
        ignore_index=True,
    )

    durations["duration_seconds"] = pd.to_numeric(
        durations["duration_seconds"],
        errors="coerce",
    )

    durations = durations.dropna(subset=["duration_seconds"])

    duration_chart = (
        alt.Chart(durations)
        .mark_line(point=True)
        .encode(
            x=alt.X(
                "timestamp:T",
                title="Start time",
            ),
            y=alt.Y(
                "duration_seconds:Q",
                title="Duration (seconds)",
            ),
            color=alt.Color(
                "process:N",
                title=None,
            ),
            tooltip=[
                alt.Tooltip(
                    "timestamp:T",
                    title="Start",
                ),
                alt.Tooltip(
                    "process:N",
                    title="Process",
                ),
                alt.Tooltip(
                    "status:N",
                    title="Status",
                ),
                alt.Tooltip(
                    "duration_seconds:Q",
                    title="Duration",
                    format=".1f",
                ),
            ],
        )
        .properties(height=330)
    )

    st.altair_chart(
        duration_chart,
        use_container_width=True,
    )
else:
    st.info("No completed duration data is available.")


st.subheader("Recent ingestion runs")

if runs.empty:
    st.info("No ingestion runs exist in the selected window.")
else:
    ingestion_columns = [
        "collection_bucket",
        "started_at",
        "finished_at",
        "run_status",
        "stage",
        "duration_seconds",
        "tracked_station_count",
        "accepted_count",
        "duplicate_count",
        "rejected_count",
        "missing_station_count",
        "error_type",
    ]

    st.dataframe(
        runs[ingestion_columns],
        use_container_width=True,
        hide_index=True,
    )


st.subheader("Recent publication attempts")

if publications.empty:
    st.info("No publication attempts exist in the selected window.")
else:
    publication_columns = [
        "started_at",
        "finished_at",
        "reporting_as_of",
        "publication_status",
        "phase",
        "duration_seconds",
        "model_count",
        "test_count",
        "warning_count",
        "coverage_mode",
        "error_type",
    ]

    st.dataframe(
        publications[publication_columns],
        use_container_width=True,
        hide_index=True,
    )


st.subheader("Rejected records")

if rejections.empty:
    st.success("No committed rejected records exist in the selected window.")
else:
    reason_summary = (
        rejections.groupby(
            ["feed_name", "reason"],
            dropna=False,
        )["rejected_record_count"]
        .sum()
        .reset_index()
        .sort_values(
            "rejected_record_count",
            ascending=False,
        )
    )

    st.dataframe(
        reason_summary,
        use_container_width=True,
        hide_index=True,
    )

    with st.expander("Show rejection history by run"):
        st.dataframe(
            rejections,
            use_container_width=True,
            hide_index=True,
        )


st.caption(
    "Missing intervals are derived from expected 15-minute buckets. "
    "An interval without an audit row may represent an absent workflow "
    "trigger or a collector failure before audit creation."
)
