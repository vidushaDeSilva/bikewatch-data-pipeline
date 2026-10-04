"""Define reusable reliability and pipeline-health calculations."""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class HealthAssessment:
    """Describe the derived pipeline state shown by the dashboard."""

    state: str
    colour: str
    explanation: str


def safe_rate(numerator: int | float, denominator: int | float) -> float | None:
    """Return a percentage, or None when the denominator is zero."""
    if denominator is None or denominator <= 0:
        return None

    return 100.0 * float(numerator or 0) / float(denominator)


def age_minutes(value: datetime | None) -> float | None:
    """Return the age of a timezone-aware timestamp in minutes."""
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return (datetime.now(timezone.utc) - value.astimezone(timezone.utc)).total_seconds() / 60.0


def format_rate(value: float | None) -> str:
    """Format a percentage for a dashboard metric."""
    return "—" if value is None else f"{value:.1f}%"


def format_age(value: float | None) -> str:
    """Format an age in minutes or hours."""
    if value is None:
        return "—"

    if value < 60:
        return f"{value:.0f} min"

    return f"{value / 60:.1f} h"


def classify_pipeline_health(
    *,
    source_age_minutes: float | None,
    publication_age_minutes: float | None,
    latest_ingestion_status: str | None,
    latest_publication_status: str | None,
    missing_intervals_24h: int,
    rejected_records_24h: int,
) -> HealthAssessment:
    """Classify current health from freshness and recent outcomes."""
    if source_age_minutes is None or publication_age_minutes is None:
        return HealthAssessment(
            "Unavailable",
            "gray",
            "A successful source collection or publication is unavailable.",
        )

    if source_age_minutes > 60 or publication_age_minutes > 75:
        return HealthAssessment(
            "Stale",
            "red",
            "Source or published reporting data exceeded its freshness limit.",
        )

    if latest_ingestion_status == "failed":
        return HealthAssessment(
            "Failed",
            "red",
            "The latest collector attempt failed.",
        )

    if (
        latest_ingestion_status == "partial"
        or latest_publication_status in {"blocked", "failed", "interrupted"}
        or rejected_records_24h > 0
    ):
        return HealthAssessment(
            "Degraded",
            "orange",
            "Recent processing completed with a partial or rejected outcome.",
        )

    if source_age_minutes > 30 or publication_age_minutes > 45 or missing_intervals_24h > 0:
        return HealthAssessment(
            "Delayed",
            "orange",
            "Data remains usable, but freshness or interval coverage is delayed.",
        )

    return HealthAssessment(
        "Healthy",
        "green",
        "Collection and publication are current with no recent critical gaps.",
    )
