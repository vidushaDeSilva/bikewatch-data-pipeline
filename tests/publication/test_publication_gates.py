import json
from pathlib import Path
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(
    0,
    str(PROJECT_ROOT / "scripts"),
)

from scripts.publish_reporting import (
    MODELS,
    PublicationBlocked,
    REQUIRED_MODELS,
    REQUIRED_TESTS,
    check_build_artifacts,
    check_freshness_artifact,
)

class GateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)

        self.nodes = {}
        self.results = []

        for name in REQUIRED_MODELS:
            uid = "model.bikewatch." + name

            self.nodes[uid] = {
                "name": name,
                "resource_type": "model",
                "config": {"enabled": True},
                "schema": (
                    "bw_candidate_analytics"
                    if name in MODELS
                    else "bw_candidate"
                ),
            }

            self.results.append(
                {
                    "unique_id": uid,
                    "status": "success",
                }
            )

        names = list(REQUIRED_TESTS) + [
            f"generic_test_{number}"
            for number in range(
                69 - len(REQUIRED_TESTS)
            )
        ]

        warning_tests = {
            "reporting_quality_warnings",
            "staging_quality_warnings",
        }

        for name in names:
            uid = "test.bikewatch." + name

            severity = (
                "warn"
                if name in warning_tests
                else "error"
            )

            self.nodes[uid] = {
                "name": name,
                "resource_type": "test",
                "config": {
                    "enabled": True,
                    "severity": severity,
                },
            }

            self.results.append(
                {
                    "unique_id": uid,
                    "status": "pass",
                    "failures": 0,
                }
            )

    def tearDown(self):
        self.directory.cleanup()

    def validate(self):
        (
            self.path / "manifest.json"
        ).write_text(
            json.dumps({"nodes": self.nodes})
        )

        (
            self.path / "run_results.json"
        ).write_text(
            json.dumps({"results": self.results})
        )

        return check_build_artifacts(self.path)

    def result(self, name):
        return next(
            result
            for result in self.results
            if result["unique_id"].endswith("." + name)
        )

    def test_complete_build_accepted(self):
        self.assertEqual(
            self.validate()["tests"],
            69,
        )

    def test_missing_test_result_blocks(self):
        self.results.pop()

        with self.assertRaises(PublicationBlocked):
            self.validate()

    def test_critical_fail_error_skip_block(self):
        for status in ["fail", "error", "skipped"]:
            self.result(
                "reporting_fact_integrity"
            )["status"] = status

            with self.assertRaises(PublicationBlocked):
                self.validate()

    def test_weakened_critical_severity_blocks(self):
        self.nodes[
            "test.bikewatch.reporting_fact_integrity"
        ]["config"]["severity"] = "warn"

        with self.assertRaises(PublicationBlocked):
            self.validate()

    def test_noncritical_warning_allowed(self):
        self.result(
            "reporting_quality_warnings"
        ).update(
            status="warn",
            failures=20,
        )

        self.assertEqual(
            len(self.validate()["warnings"]),
            1,
        )

    def test_wrong_schema_blocks(self):
        self.nodes[
            "model.bikewatch.mart_station_current"
        ]["schema"] = "analytics"

        with self.assertRaises(PublicationBlocked):
            self.validate()

    def test_missing_required_gate_blocks(self):
        del self.nodes[
            "test.bikewatch.reporting_coverage_expectations"
        ]

        with self.assertRaises(PublicationBlocked):
            self.validate()

    def test_freshness_statuses(self):
        results = [
            {
                "unique_id": (
                    "source.bikewatch.bikewatch_raw."
                    + name
                ),
                "status": "pass",
            }
            for name in [
                "station_metadata",
                "station_observation",
            ]
        ]

        path = self.path / "sources.json"

        path.write_text(
            json.dumps({"results": results})
        )

        self.assertEqual(
            len(check_freshness_artifact(path)),
            2,
        )

        results[0]["status"] = "error"

        path.write_text(
            json.dumps({"results": results})
        )

        with self.assertRaises(PublicationBlocked):
            check_freshness_artifact(path)

    def test_missing_freshness_result_blocks(self):
        path = self.path / "sources.json"
        path.write_text('{"results": []}')

        with self.assertRaises(PublicationBlocked):
            check_freshness_artifact(path)


if __name__ == "__main__":
    unittest.main()


# Create fake valid dbt build
#         ↓
# modify one condition
#         ↓
# run publication validation
#         ↓
# check expected behavior

# valid condition     → accepted
# critical problem    → PublicationBlocked
# warning-only issue  → allowed

# setUp() — creates a temporary fake dbt build environment containing all required models and tests in a valid state. 
# This gives each test a clean baseline.
# tearDown() — removes the temporary test directory after each unit test.
# validate() — writes fake manifest.json and run_results.json files, 
# then calls check_build_artifacts() to see whether the simulated dbt build should be accepted.
# result(name) — finds a specific test result from the fake results list so an individual test can change its status.

# test_complete_build_accepted() — confirms a complete valid build with 69 tests is accepted.
# test_missing_test_result_blocks() — confirms publication is blocked if an enabled model/test has no corresponding result.
# test_critical_fail_error_skip_block() — confirms a critical test blocks publication when its status is fail, error, or skipped.
# test_weakened_critical_severity_blocks() — confirms a critical test cannot be changed from error severity to warn.
# test_noncritical_warning_allowed() — confirms designated warning tests are allowed to return warnings without blocking 
# publication.
# test_wrong_schema_blocks() — confirms reporting models must be built in the isolated candidate schemas, 
# not directly in analytics.
# test_missing_required_gate_blocks() — confirms publication is blocked if one of the mandatory validation tests is missing.
# test_freshness_statuses() — confirms source freshness passes when both required sources return acceptable statuses, 
# and blocks if one returns an error.
# test_missing_freshness_result_blocks() — confirms publication is blocked if the source-freshness artifact contains no 
# expected source results.