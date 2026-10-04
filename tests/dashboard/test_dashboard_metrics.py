"""Test the dashboard's reliability and pipeline-health rules."""

from datetime import datetime, timedelta, timezone

from dashboard.metrics import (
    classify_current_observation,
    classify_pipeline_health,
    may_show_current_counts,
    safe_rate,
)


def test_safe_rate_preserves_missing_denominator():
    """A missing denominator must not become a zero-percent result."""
    assert safe_rate(0, 0) is None
    assert safe_rate(2, 4) == 50.0


def test_pipeline_is_healthy_when_current():
    """Recent successful data with no gaps should be healthy."""
    result = classify_pipeline_health(
        source_age_minutes=10,
        publication_age_minutes=12,
        latest_ingestion_status="succeeded",
        latest_publication_status="published",
        missing_intervals_24h=0,
        rejected_records_24h=0,
    )

    assert result.state == "Healthy"


def test_pipeline_becomes_delayed_with_missing_intervals():
    """A missing scheduled interval should produce a delayed state."""
    result = classify_pipeline_health(
        source_age_minutes=20,
        publication_age_minutes=20,
        latest_ingestion_status="succeeded",
        latest_publication_status="published",
        missing_intervals_24h=1,
        rejected_records_24h=0,
    )

    assert result.state == "Delayed"


def test_pipeline_becomes_stale_from_source_age():
    """Old source data should override an earlier successful status."""
    result = classify_pipeline_health(
        source_age_minutes=90,
        publication_age_minutes=20,
        latest_ingestion_status="succeeded",
        latest_publication_status="published",
        missing_intervals_24h=0,
        rejected_records_24h=0,
    )

    assert result.state == "Stale"


def test_failed_ingestion_is_reported():
    """The latest failed collector attempt should be visible."""
    result = classify_pipeline_health(
        source_age_minutes=20,
        publication_age_minutes=20,
        latest_ingestion_status="failed",
        latest_publication_status="published",
        missing_intervals_24h=0,
        rejected_records_24h=0,
    )

    assert result.state == "Failed"


def test_blocked_publication_is_degraded():
    """A blocked candidate should not hide the older published release."""
    result = classify_pipeline_health(
        source_age_minutes=20,
        publication_age_minutes=20,
        latest_ingestion_status="succeeded",
        latest_publication_status="blocked",
        missing_intervals_24h=0,
        rejected_records_24h=0,
    )

    assert result.state == "Degraded"


def current_state(**changes):
    """Create a valid current observation with selected overrides."""
    values = {
        "has_trusted_observation": True,
        "freshness_valid_until": (datetime.now(timezone.utc) + timedelta(minutes=10)),
        "using_older_trusted_observation": False,
        "is_installed": True,
        "is_renting": True,
        "is_returning": True,
        "bikes_available": 4,
        "docks_available": 6,
    }

    values.update(changes)
    return classify_current_observation(**values)


def test_missing_observation_does_not_become_zero_availability():
    """Missing source data must remain explicitly unavailable."""
    state = current_state(
        has_trusted_observation=False,
        bikes_available=None,
        docks_available=None,
    )

    assert state == "Missing"
    assert may_show_current_counts(state) is False


def test_expired_observation_is_stale():
    """Expired trusted values must not be labelled current."""
    state = current_state(freshness_valid_until=(datetime.now(timezone.utc) - timedelta(seconds=1)))

    assert state == "Stale"
    assert may_show_current_counts(state) is False


def test_real_zero_bikes_remains_a_valid_operating_state():
    """A fresh zero-bike observation is different from missing data."""
    state = current_state(bikes_available=0)

    assert state == "No bikes"
    assert may_show_current_counts(state) is True


def test_older_trusted_reading_is_explicit():
    """A valid fallback must be labelled as an older trusted reading."""
    state = current_state(using_older_trusted_observation=True)

    assert state == "Older trusted reading"
    assert may_show_current_counts(state) is True
