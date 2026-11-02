BikeWatch station-replacement test repair

Replace these two files in the repository root:
  tests/ci/test_station_replacement.py
  tests/ci/test_effective_station_cutover.py

Why: the original tests copied mutable production config. After a successful --apply,
the old station ID is intentionally absent, so rerunning the tests failed. These tests
now construct an isolated pre-migration 20-station fixture under pytest tmp_path.
Production config is never used or changed by these regression tests.

The TEST_DATABASE_URL errors are separate: the phase52 ingestion tests require the
disposable PostgreSQL CI service configured in .github/workflows/ci.yml.
