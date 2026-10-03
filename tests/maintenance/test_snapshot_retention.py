# Verify that snapshot selection protects published reporting data.
# These tests do not connect to PostgreSQL.

"""Test the snapshot retention selection rules."""

from uuid import UUID

from scripts.maintenance import obsolete_snapshots
from scripts.publication_db import MODELS


def snapshot_names(run_ids):
    """Generate publisher-compatible snapshot names."""

    return {f"v_{run_id.hex}_{model}" for run_id in run_ids for model in MODELS}


def test_keeps_two_recent_releases():
    """Remove only snapshots outside the two newest releases."""

    newest, previous, oldest = [UUID(int=value) for value in (3, 2, 1)]
    published = [newest, previous, oldest]

    result = obsolete_snapshots(
        published,
        newest,
        snapshot_names(published),
    )

    assert set(result) == snapshot_names([oldest])


def test_preserves_active_release_even_when_older():
    """Protect an older active release as well as recent releases."""

    published = [UUID(int=value) for value in (4, 3, 2, 1)]
    active = published[-1]

    result = obsolete_snapshots(
        published,
        active,
        snapshot_names(published),
    )

    assert set(result) == snapshot_names([published[2]])


def test_ignores_unknown_and_legacy_tables():
    """Leave tables without a matching successful publication untouched."""

    published = [UUID(int=value) for value in (3, 2, 1)]
    unknown = UUID(int=99)

    existing = snapshot_names(published) | snapshot_names([unknown])
    existing.add("legacy_imported_station_history")

    result = obsolete_snapshots(
        published,
        published[0],
        existing,
    )

    assert set(result) == snapshot_names([published[-1]])
