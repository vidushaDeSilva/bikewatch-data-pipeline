"""Define reusable reliability, freshness, and pipeline-health rules."""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class HealthAssessment:
    """Describe the pipeline state shown by the health dashboard."""

    state: str
    colour: str
    explanation: str


def safe_rate(
    numerator: int | float,
    denominator: int | float,
) -> float | None:
    """Return a percentage, or None without a valid denominator."""
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
    """Format a percentage for dashboard display."""
    return "—" if value is None else f"{value:.1f}%"


def format_age(value: float | None) -> str:
    """Format an age using minutes or hours."""
    if value is None:
        return "—"

    if value < 60:
        return f"{value:.0f} min"

    return f"{value / 60:.1f} h"


def classify_current_observation(
    *,
    has_trusted_observation: bool,
    freshness_valid_until: datetime | None,
    using_older_trusted_observation: bool,
    is_installed: bool | None,
    is_renting: bool | None,
    is_returning: bool | None,
    bikes_available: int | None,
    docks_available: int | None,
    checked_at: datetime | None = None,
) -> str:
    """Classify how a station observation may be presented."""
    checked_at = checked_at or datetime.now(timezone.utc)

    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)

    if not has_trusted_observation:
        return "Missing"

    if freshness_valid_until is None:
        return "Stale"

    if freshness_valid_until.tzinfo is None:
        freshness_valid_until = freshness_valid_until.replace(tzinfo=timezone.utc)

    if checked_at > freshness_valid_until:
        return "Stale"

    if using_older_trusted_observation:
        return "Older trusted reading"

    if is_installed is False:
        return "Not installed"

    if is_renting is False and is_returning is False:
        return "Closed"

    if is_renting is False:
        return "Rentals closed"

    if is_returning is False:
        return "Returns closed"

    if bikes_available == 0:
        return "No bikes"

    if docks_available == 0:
        return "No docks"

    return "Open"


def may_show_current_counts(state: str) -> bool:
    """Return whether counts may be labelled as current."""
    return state not in {"Missing", "Stale"}


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
            "Data remains usable, but freshness or coverage is delayed.",
        )

    return HealthAssessment(
        "Healthy",
        "green",
        "Collection and publication are current with no critical gaps.",
    )
