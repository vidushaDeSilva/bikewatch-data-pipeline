"""Protect the collector's data-audit status versus orchestration exit policy."""

from bikewatch.ingestion.collect import exit_code_for_outcome


def summary(*, rejected=0, missing=0):
    """Make the relevant collector feed counters for decision tests."""
    return {"rejected": rejected, "missing": missing}


def test_missing_only_partial_collection_can_continue_reporting():
    assert exit_code_for_outcome("succeeded", [summary()]) == 0
    assert exit_code_for_outcome("partial", [summary(missing=1)]) == 0
    assert exit_code_for_outcome("partial", [summary(), summary(missing=1)]) == 0


def test_rejected_records_and_unusable_runs_still_fail_github():
    assert exit_code_for_outcome("failed", [summary()]) == 1
    assert exit_code_for_outcome("partial", [summary(rejected=1)]) == 1
    assert exit_code_for_outcome("partial", [summary(), summary(rejected=1)]) == 1
