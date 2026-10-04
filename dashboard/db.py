"""Provide cached, read-only PostgreSQL queries for BikeWatch dashboards."""

import os
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
import psycopg
import streamlit as st
from dotenv import load_dotenv
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
CACHE_TTL_SECONDS = 15 * 60
EXPECTED_ROLE = "bikewatch_dashboard"

load_dotenv(ROOT / ".env")


def dashboard_dsn() -> str:
    """Return the configured read-only dashboard connection string."""
    dsn = os.getenv("BIKEWATCH_DASHBOARD_URL", "").strip()

    if not dsn:
        try:
            dsn = str(st.secrets["BIKEWATCH_DASHBOARD_URL"]).strip()
        except Exception:
            dsn = ""

    if not dsn:
        raise RuntimeError(
            "BIKEWATCH_DASHBOARD_URL is missing. Configure a connection "
            "for the bikewatch_dashboard role."
        )

    return dsn


@st.cache_data(
    ttl=CACHE_TTL_SECONDS,
    max_entries=128,
    show_spinner=False,
)
def query_dataframe(
    statement: str,
    params: Sequence[object] = (),
) -> pd.DataFrame:
    """Run one cached query inside a read-only database transaction."""
    with psycopg.connect(
        dashboard_dsn(),
        connect_timeout=10,
        row_factory=dict_row,
    ) as connection:
        # PostgreSQL also enforces read-only behavior for this transaction.
        connection.execute("SET TRANSACTION READ ONLY")
        connection.execute("SET LOCAL statement_timeout = '15s'")

        identity = connection.execute("SELECT current_user AS role_name").fetchone()

        role_name = identity["role_name"]

        if role_name != EXPECTED_ROLE:
            raise RuntimeError(
                f"Dashboard connection must use the {EXPECTED_ROLE} role; connected as {role_name}."
            )

        rows = connection.execute(
            statement,
            tuple(params),
        ).fetchall()

    return pd.DataFrame(rows)


def clear_query_cache() -> None:
    """Clear cached dashboard results after a manual refresh."""
    query_dataframe.clear()
