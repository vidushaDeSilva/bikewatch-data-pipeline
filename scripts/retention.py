"""Prune generated snapshots inside the publication transaction."""

import re

from psycopg import sql


def prune_reporting_versions(connection, keep=3):
    # Keep enough historical releases to allow safe rollback.
    if type(keep) is not int or keep < 2:
        raise ValueError("Retain at least two releases.")

    # Read the currently active reporting release and its published relations.
    release = connection.execute(
        """
        SELECT run_id, relations
        FROM ops.reporting_release
        WHERE singleton
        """
    ).fetchone()

    if release is None:
        return

    # Retain the most recent successfully published runs.
    retained = {
        row[0].hex
        for row in connection.execute(
            """
            SELECT run_id
            FROM ops.publication_run
            WHERE status = 'published'
            ORDER BY finished_at DESC, run_id DESC
            LIMIT %s
            """,
            (keep,),
        ).fetchall()
    }

    # Always protect the currently active release and its referenced tables.
    retained.add(release[0].hex)
    protected = set(release[1].values())

    # Match only generated reporting-version tables managed by this pipeline.
    pattern = re.compile(
        r"v_([0-9a-f]{32})_"
        r"(?:fct_station_observation|mart_station_current|"
        r"mart_station_hourly|mart_station_daily)"
    )

    # Find physical snapshot tables in the reporting_versions schema.
    tables = connection.execute(
        """
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n
            ON n.oid = c.relnamespace
        WHERE n.nspname = 'reporting_versions'
          AND c.relkind = 'r'
        """
    ).fetchall()

    # Drop old snapshots that are neither retained nor currently referenced.
    for (name,) in tables:
        match = pattern.fullmatch(name)

        if match and match[1] not in retained and f"reporting_versions.{name}" not in protected:
            connection.execute(
                sql.SQL("DROP TABLE {} RESTRICT").format(
                    sql.Identifier(
                        "reporting_versions",
                        name,
                    )
                )
            )


# Requires at least 2 releases to be retained.
# Reads the currently active reporting release.
# Finds the most recent successfully published runs and marks them for retention.
# Also protects all tables referenced by the active release.
# Scans the reporting_versions schema for generated snapshot tables with the expected naming pattern.
# Drops only those snapshot tables that belong to old runs and are not currently protected.
