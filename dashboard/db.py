"""Provide read-only PostgreSQL queries for the BikeWatch dashboard."""

import os
from collections.abc import Sequence

import pandas as pd
import psycopg
import streamlit as st
from psycopg.rows import dict_row


def dashboard_dsn() -> str:
    """Return the dashboard connection string without displaying it."""
    dsn = os.getenv("BIKEWATCH_DASHBOARD_URL", "").strip()

    if not dsn:
        try:
            dsn = str(st.secrets["BIKEWATCH_DASHBOARD_URL"]).strip()
        except (KeyError, FileNotFoundError):
            dsn = ""

    if not dsn:
        raise RuntimeError(
            "BIKEWATCH_DASHBOARD_URL is missing. "
            "Configure the read-only bikewatch_dashboard connection."
        )

    return dsn


@st.cache_data(ttl=60, show_spinner=False)
def query_dataframe(
    statement: str,
    params: Sequence[object] = (),
) -> pd.DataFrame:
    """Execute one read-only query and return its rows as a DataFrame."""
    with psycopg.connect(
        dashboard_dsn(),
        autocommit=True,
        connect_timeout=10,
        row_factory=dict_row,
    ) as connection:
        connection.execute("SET statement_timeout = '15s'")

        rows = connection.execute(
            statement,
            tuple(params),
        ).fetchall()

    return pd.DataFrame(rows)


def clear_query_cache() -> None:
    """Clear cached query results after a manual dashboard refresh."""
    query_dataframe.clear()
