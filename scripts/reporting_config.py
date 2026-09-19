"""Validate coverage configuration without connecting to Neon."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml


def utc_boundary(value):
    if not isinstance(value, str):
        raise ValueError(
            "Tracking timestamps must be quoted ISO strings "
            "with a UTC offset."
        )

    stamp = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    if stamp.utcoffset() != timedelta(0):
        raise ValueError(
            "Tracking timestamps must use an explicit UTC offset: "
            "Z or +00:00."
        )

    if stamp.minute % 15 or stamp.second or stamp.microsecond:
        raise ValueError(
            "Tracking boundaries must align to 15-minute UTC buckets."
        )

    return stamp.astimezone(timezone.utc)


def expected_count(start, end, cutoff):
    """Count fully elapsed 15-minute windows in [start, end)."""

    upper = min(end, cutoff) if end is not None else cutoff

    return max(
        0,
        int((upper - start).total_seconds() // 900),
    )


def load_reporting_config(path):
    config = yaml.safe_load(Path(path).read_text()) or {}

    if not isinstance(config, dict):
        raise ValueError(
            "Reporting configuration must be a YAML mapping."
        )

    allowed = {
        "reporting_timezone",
        "feed_stale_after_seconds",
        "coverage_tracking_periods",
        "reporting_as_of",
    }

    unknown = set(config) - allowed

    if unknown:
        raise ValueError(
            "Unknown reporting configuration keys: "
            + ", ".join(sorted(unknown))
        )

    ZoneInfo(
        config.get(
            "reporting_timezone",
            "America/New_York",
        )
    )

    age = config.get("feed_stale_after_seconds", 2700)

    if type(age) is not int or age <= 0:
        raise ValueError(
            "feed_stale_after_seconds must be a positive integer."
        )

    periods = config.get("coverage_tracking_periods", [])

    if not isinstance(periods, list):
        raise ValueError(
            "coverage_tracking_periods must be a list."
        )

    grouped = {}

    for period in periods:
        if (
            not isinstance(period, dict)
            or set(period)
            - {"station_id", "start_bucket", "end_bucket"}
        ):
            raise ValueError("Invalid tracking-period fields.")

        station = period.get("station_id")

        if (
            not isinstance(station, str)
            or not station.strip()
            or station != station.strip()
        ):
            raise ValueError(
                "station_id must be a nonempty quoted string "
                "without outer spaces."
            )

        start = utc_boundary(period.get("start_bucket"))

        end = (
            utc_boundary(period["end_bucket"])
            if period.get("end_bucket") is not None
            else None
        )

        if end is not None and end <= start:
            raise ValueError(
                "Tracking end must be after start."
            )

        grouped.setdefault(station, []).append(
            (start, end)
        )

    for intervals in grouped.values():
        intervals.sort(key=lambda pair: pair[0])

        for (_, end), (next_start, _) in zip(
            intervals,
            intervals[1:],
        ):
            if end is None or next_start < end:
                raise ValueError(
                    "Tracking intervals for the same station "
                    "must not overlap."
                )

    return config


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]

    config = load_reporting_config(
        root / "config/bikewatch_reporting_vars.yml"
    )

    print(
        json.dumps(
            {
                "valid": True,
                "tracking_period_count": len(
                    config.get("coverage_tracking_periods", [])
                ),
                "coverage_mode": (
                    "scheduled"
                    if config.get("coverage_tracking_periods")
                    else "manual"
                ),
            },
            indent=2,
        )
    )


# load YAML config
#     ↓
# check allowed keys
#     ↓
# validate timezone
#     ↓
# validate stale-age setting
#     ↓
# validate tracking periods
#     ↓
# check 15-minute alignment
#     ↓
# check UTC timestamps
#     ↓
# check interval ordering / overlap
#     ↓
# print validation summary