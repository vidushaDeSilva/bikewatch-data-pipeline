"""Transactional publication helpers.

The caller owns the transaction and publication lock.
"""

from psycopg import sql
from psycopg.types.json import Jsonb


MODELS = {
    "fct_station_observation": (
        "station_id",
        "collection_bucket",
    ),
    "mart_station_current": (
        "station_id",
    ),
    "mart_station_hourly": (
        "station_id",
        "hour_start_utc",
    ),
    "mart_station_daily": (
        "station_id",
        "reporting_date",
    ),
}

CANDIDATE_SCHEMA = "bw_candidate_analytics"


class PublicationBlocked(Exception):
    pass


def published_fingerprints(connection):
    result = {}

    for model, key in MODELS.items():
        result[model] = connection.execute(
            sql.SQL(
                """
                SELECT
                    count(*),
                    md5(
                        coalesce(
                            string_agg(
                                row_to_json(t)::text,
                                chr(10)
                                ORDER BY {}
                            ),
                            ''
                        )
                    )
                FROM {} t
                """
            ).format(
                sql.SQL(", ").join(
                    map(sql.Identifier, key)
                ),
                sql.Identifier("analytics", model),
            )
        ).fetchone()

    return result


def columns(connection, schema, name):
    return connection.execute(
        """
        SELECT
            a.attname,
            a.atttypid,
            a.atttypmod

        FROM pg_attribute a

        JOIN pg_class c
            ON c.oid = a.attrelid

        JOIN pg_namespace n
            ON n.oid = c.relnamespace

        WHERE n.nspname = %s
          AND c.relname = %s
          AND a.attnum > 0
          AND NOT a.attisdropped

        ORDER BY a.attnum
        """,
        (schema, name),
    ).fetchall()


def publish_candidate(connection, run_id, cutoff, summary):
    """Copy candidates and switch public views.

    Does not commit. The caller's transaction includes all DDL
    changes, audit status, and the release pointer.

    Old snapshot tables are retained.
    """

    token = run_id.hex
    relations = {}
    row_counts = {}

    for model, key in MODELS.items():
        kind = connection.execute(
            """
            SELECT c.relkind

            FROM pg_class c

            JOIN pg_namespace n
                ON n.oid = c.relnamespace

            WHERE n.nspname = %s
              AND c.relname = %s
            """,
            (CANDIDATE_SCHEMA, model),
        ).fetchone()

        if kind != ("r",):
            raise PublicationBlocked(
                f"Candidate {model} must be a built table."
            )

        candidate_columns = columns(
            connection,
            CANDIDATE_SCHEMA,
            model,
        )

        public_columns = columns(
            connection,
            "analytics",
            model,
        )

        if (
            public_columns
            and public_columns != candidate_columns
        ):
            raise PublicationBlocked(
                f"Column contract changed for {model}; "
                "use an explicit migration."
            )

        snapshot = f"v_{token}_{model}"

        target = sql.Identifier(
            "reporting_versions",
            snapshot,
        )

        connection.execute(
            sql.SQL(
                "CREATE TABLE {} AS SELECT * FROM {}"
            ).format(
                target,
                sql.Identifier(CANDIDATE_SCHEMA, model),
            )
        )

        connection.execute(
            sql.SQL(
                "ALTER TABLE {} ADD PRIMARY KEY ({})"
            ).format(
                target,
                sql.SQL(", ").join(
                    map(sql.Identifier, key)
                ),
            )
        )

        row_counts[model] = connection.execute(
            sql.SQL(
                "SELECT count(*) FROM {}"
            ).format(target)
        ).fetchone()[0]

        mismatch = connection.execute(
            sql.SQL(
                """
                SELECT count(*)
                FROM {}
                WHERE reporting_as_of IS DISTINCT FROM %s
                """
            ).format(target),
            (cutoff,),
        ).fetchone()[0]

        if mismatch:
            raise PublicationBlocked(
                f"Build-cutoff mismatch in {model}."
            )

        relations[model] = (
            f"reporting_versions.{snapshot}"
        )

    for model in MODELS:
        kind = connection.execute(
            """
            SELECT c.relkind

            FROM pg_class c

            JOIN pg_namespace n
                ON n.oid = c.relnamespace

            WHERE n.nspname = 'analytics'
              AND c.relname = %s
            """,
            (model,),
        ).fetchone()

        public = sql.Identifier("analytics", model)

        if kind == ("r",):
            # Preserve tables created by the earlier package.
            legacy = f"legacy_{token}_{model}"

            connection.execute(
                sql.SQL(
                    "ALTER TABLE {} RENAME TO {}"
                ).format(
                    public,
                    sql.Identifier(legacy),
                )
            )

            connection.execute(
                sql.SQL(
                    "ALTER TABLE {} "
                    "SET SCHEMA reporting_versions"
                ).format(
                    sql.Identifier("analytics", legacy)
                )
            )

        elif kind is not None and kind != ("v",):
            raise PublicationBlocked(
                f"Unexpected public relation type for {model}."
            )

        snapshot = f"v_{token}_{model}"

        names = sql.SQL(", ").join(
            sql.Identifier(column[0])
            for column in columns(
                connection,
                CANDIDATE_SCHEMA,
                model,
            )
        )

        connection.execute(
            sql.SQL(
                "CREATE OR REPLACE VIEW {} "
                "AS SELECT {} FROM {}"
            ).format(
                public,
                names,
                sql.Identifier(
                    "reporting_versions",
                    snapshot,
                ),
            )
        )

        connection.execute(
            sql.SQL(
                "REVOKE ALL ON {} FROM PUBLIC"
            ).format(public)
        )

        connection.execute(
            sql.SQL(
                "REVOKE ALL ON {} FROM bikewatch_dashboard"
            ).format(public)
        )

        if model.startswith("mart_"):
            connection.execute(
                sql.SQL(
                    "GRANT SELECT ON {} TO bikewatch_dashboard"
                ).format(public)
            )

    summary = dict(
        summary,
        published_row_counts=row_counts,
    )

    updated = connection.execute(
        """
        UPDATE ops.publication_run

        SET status = 'published',
            phase = 'publish',
            finished_at = clock_timestamp(),
            check_summary = %s

        WHERE run_id = %s
          AND status = 'running'
        """,
        (Jsonb(summary), run_id),
    ).rowcount

    if updated != 1:
        raise PublicationBlocked(
            "Publication attempt is no longer in running status."
        )

    connection.execute(
        """
        INSERT INTO ops.reporting_release (
            singleton,
            run_id,
            published_at,
            reporting_as_of,
            relations
        )

        VALUES (
            true,
            %s,
            clock_timestamp(),
            %s,
            %s
        )

        ON CONFLICT (singleton)
        DO UPDATE SET
            run_id = excluded.run_id,
            published_at = excluded.published_at,
            reporting_as_of = excluded.reporting_as_of,
            relations = excluded.relations
        """,
        (
            run_id,
            cutoff,
            Jsonb(relations),
        ),
    )

    return summary

#         dbt candidate build
#                 │
#                 ▼
# bw_candidate_analytics
#                 │
#          validate candidate
#                 │
#                 ▼
#        copy immutable version
#                 │
#                 ▼
# reporting_versions
# ├── v_run1_mart_station_current
# ├── v_run2_mart_station_current
# ├── v_run3_mart_station_current
# └── ...
#                 ▲
#                 │
# analytics.mart_station_current
#           VIEW ─┘

# dashboard
#    │
#    └── always queries analytics.mart_station_current


# published_fingerprints()
#         ↓
# check current published data

# columns()
#         ↓
# inspect/compare table structure

# publish_candidate()
#         ↓
# validate candidate
#         ↓
# create versioned snapshot
#         ↓
# switch public analytics views
#         ↓
# record new published release