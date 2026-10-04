"""Test the dashboard's reliability and pipeline-health rules."""

from dashboard.metrics import (
    classify_pipeline_health,
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
