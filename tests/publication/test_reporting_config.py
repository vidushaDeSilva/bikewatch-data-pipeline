import json
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[2] / "scripts"),
)

from  scripts.reporting_config import (
    expected_count,
    load_reporting_config,
    utc_boundary,
)


class CoverageTests(unittest.TestCase):
    def load(self, periods):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vars.yml"

            path.write_text(
                json.dumps(
                    {"coverage_tracking_periods": periods}
                )
            )

            return load_reporting_config(path)

    def period(
        self,
        start="2026-09-15T10:00:00Z",
        end=None,
    ):
        return {
            "station_id": "A",
            "start_bucket": start,
            "end_bucket": end,
        }

    def test_manual_mode(self):
        self.assertEqual(
            self.load([])["coverage_tracking_periods"],
            [],
        )

    def test_completed_hour(self):
        self.assertEqual(
            expected_count(
                utc_boundary("2026-09-15T10:00:00Z"),
                None,
                utc_boundary("2026-09-15T11:00:00Z"),
            ),
            4,
        )

    def test_partial_window(self):
        self.assertEqual(
            expected_count(
                utc_boundary("2026-09-15T10:00:00Z"),
                None,
                datetime.fromisoformat(
                    "2026-09-15T10:37:00+00:00"
                ),
            ),
            2,
        )

    def test_future_start(self):
        self.assertEqual(
            expected_count(
                utc_boundary("2026-09-15T11:00:00Z"),
                None,
                utc_boundary("2026-09-15T10:00:00Z"),
            ),
            0,
        )

    def test_exclusive_end(self):
        self.assertEqual(
            expected_count(
                utc_boundary("2026-09-15T10:00:00Z"),
                utc_boundary("2026-09-15T10:30:00Z"),
                utc_boundary("2026-09-15T12:00:00Z"),
            ),
            2,
        )

    def test_dst_days(self):
        zone = ZoneInfo("America/New_York")

        cases = [
            ("2026-03-08", "2026-03-09", 92),
            ("2026-11-01", "2026-11-02", 100),
        ]

        for start, end, expected in cases:
            a = (
                datetime.fromisoformat(start)
                .replace(tzinfo=zone)
                .astimezone(timezone.utc)
            )

            b = (
                datetime.fromisoformat(end)
                .replace(tzinfo=zone)
                .astimezone(timezone.utc)
            )

            self.assertEqual(
                expected_count(a, b, b),
                expected,
            )

    def test_overlap_rejected(self):
        with self.assertRaises(ValueError):
            self.load(
                [
                    self.period(),
                    self.period("2026-09-15T11:00:00Z"),
                ]
            )

    def test_adjacent_and_disjoint_periods_allowed(self):
        self.load(
            [
                self.period(
                    end="2026-09-15T11:00:00Z"
                ),
                self.period(
                    "2026-09-15T11:00:00Z",
                    "2026-09-15T12:00:00Z",
                ),
                self.period(
                    "2026-09-15T13:00:00Z"
                ),
            ]
        )

    def test_invalid_boundaries(self):
        values = [
            "2026-09-15T10:07:00Z",
            "2026-09-15T10:00:01Z",
            "2026-09-15T10:00:00",
            "2026-09-15T10:00:00+02:00",
        ]

        for value in values:
            with self.assertRaises(ValueError):
                utc_boundary(value)

    def test_invalid_id(self):
        for value in [123, "", " A "]:
            period = self.period()
            period["station_id"] = value

            with self.assertRaises(ValueError):
                self.load([period])

    def test_empty_period_rejected(self):
        with self.assertRaises(ValueError):
            self.load(
                [
                    self.period(
                        end="2026-09-15T10:00:00Z"
                    )
                ]
            )


if __name__ == "__main__":
    unittest.main()

# test_manual_mode() — checks that an empty tracking-period list is accepted and remains empty.
# test_completed_hour() — checks that 10:00 → 11:00 contains exactly 4 complete 15-minute windows.
# test_partial_window() — checks that 10:00 → 10:37 counts only the fully completed windows: 10:00–10:15 
# and 10:15–10:30, so result = 2.
# test_future_start() — checks that if the tracking start is after the cutoff, expected count is 0.
# test_exclusive_end() — verifies [start, end) behavior. For 10:00 → 10:30, only two buckets are counted: 10:00 and 10:15.
# test_dst_days() — checks that daylight-saving changes are handled correctly. 
# Spring-forward day has 23 hours = 92 fifteen-minute buckets, while fall-back day has 25 hours = 100 buckets.
# test_overlap_rejected() — verifies overlapping tracking intervals for the same station raise ValueError.
# test_adjacent_and_disjoint_periods_allowed() — verifies periods can touch exactly at their boundaries or 
# have gaps without being rejected.
# test_invalid_boundaries() — checks that timestamps are rejected if they are not on a 15-minute boundary, 
# contain non-zero seconds, have no timezone offset, or use a non-UTC offset.
# test_invalid_id() — checks that station_id must be a non-empty string with no surrounding spaces.
# test_empty_period_rejected() — checks that a tracking interval whose end equals its start is invalid.