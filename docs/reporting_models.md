# BikeWatch — Phase 5.3 reporting models and quality gates

This add-on implements the two tasks after sources and staging:

1. Build the cleaned observation fact and current, hourly, and daily marts.
2. Add grain, required-field, relationship, value-range, timestamp-order, and reconciliation tests.

It targets the staging models supplied in this conversation, dbt-core 1.10.15,
dbt-postgres 1.9.1, and PostgreSQL 16+. It is an add-on, not a replacement repository.
No credentials are included. It does not connect to Neon during installation.

## 1. Confirm the prerequisite

Use the repaired `stg_pipeline_run.sql` and all four macros in `bw_casts.sql`
from the preceding step. From your existing project root, with its virtual environment active:

```bash
python3 scripts/run_dbt.py build
```

Resolve errors before installing the new reporting layer. Warnings about old data
are different from compilation/database errors. A successful parse alone is not a
database build.

## 2. Install the files

Extract this ZIP to a separate directory, for example `~/Downloads/bikewatch-reporting-step53`.
From your **existing BikeWatch project root**, run:

```bash
python3 ~/Downloads/bikewatch-reporting-step53/install.py .
```

Adjust the extracted package path if needed. The installer copies add-on files into
`dbt_bikewatch`, `config`, `sql/migrations`, and `docs`. Existing changed add-on files
are backed up before replacement. If a different schema-generation macro already
exists, installation stops before any writes so its project-wide behavior is not lost.

Your `.env`, `profiles.yml`, `dbt_project.yml`, ingestion code, and existing staging
files are not changed. The new models have their materializations configured inside
their SQL, and their documentation lives in `reporting_schema.yml`.

If installing manually, merge the package's `dbt_bikewatch`, `config`, `sql`, and
`docs` directories into the corresponding project directories. Review an existing
`generate_schema_name.sql` before merging it.

## 3. Apply the schema migration

Open `sql/migrations/006_reporting_models.sql` and execute its contents in the
Neon SQL editor as the database owner, on the same BikeWatch branch/database.

This grants the existing `bikewatch_transform` role permission to create objects in
`analytics` and `staging`, and read the three raw/operations tables. It does not
grant dashboard access or alter raw data.

The schema macro maps `schema='analytics'` to exactly `analytics` only for this
project's existing `neon` target with `target.schema='staging'`. Staging/internal
models remain in `staging`. Other targets keep dbt's standard target-schema prefix
to avoid development/CI collisions. If you use another target or target schema,
the resulting reporting schema and corresponding grants must match that target.

## 4. Configure coverage honestly

The installed file `config/bikewatch_reporting_vars.yml` defaults to:

```yaml
reporting_timezone: America/New_York
feed_stale_after_seconds: 2700
coverage_tracking_periods: []
```

Keep the list empty while collection is manual. The marts still contain actual
observations, but expected counts are zero and coverage ratios are NULL. NULL
means “no configured scheduled expectation,” not zero percent coverage.

When a scheduler is enabled, add one interval for each real tracked station:

```yaml
coverage_tracking_periods:
  - station_id: "YOUR_REAL_STATION_ID"
    start_bucket: "2026-09-14T00:00:00+00:00"
    end_bucket: null
```

This is a shape example: replace both the ID and the start timestamp with the real
station and the actual first scheduled bucket. Use text station IDs, explicit UTC
timestamps, and boundaries divisible by 15 minutes. The start is inclusive; the end
is exclusive. An end of NULL means ongoing. Do not declare a start date earlier
than the actual scheduling commitment. Disjoint periods support removals and
reactivations. Periods for the same station must not overlap.

The models use a fixed 15-minute bucket definition, matching the collector we
built. Changing only `COLLECTION_INTERVAL_MINUTES` will not change this reporting
design; a cadence change requires coordinated ingestion, configuration, and SQL edits.

Tracking configuration is declarative history: preserve old intervals when adding
new ones. Full builds intentionally recompute coverage if that history is corrected.

## 5. Build and test

From the existing project root:

```bash
python3 scripts/run_dbt.py parse
python3 scripts/run_dbt.py source freshness --select source:bikewatch_raw
python3 scripts/run_dbt.py build --vars "$(cat config/bikewatch_reporting_vars.yml)"
```

The `--vars` argument is necessary to load this separate configuration file; dbt
does not automatically read it. Always pass the same vars file to subsequent builds
and tests. A full build refreshes the internal snapshots, fact, marts, and their
tests in dependency order. Do not refresh only the marts after collecting new raw
data: their internal inputs are tables and must also be refreshed.

The as-of cutoff defaults to one dbt run-start timestamp, shared across all models.
Only observations received by that cutoff are considered. For reproducible historical
runs, set `reporting_as_of` in the vars file to an explicit timestamp with UTC offset.

Freshness checks remain a separate command. Manual collection pauses can legitimately
cause source freshness warnings/errors; a build does not make the raw data newer.

To regenerate documentation with the same configuration:

```bash
python3 scripts/run_dbt.py docs generate --vars "$(cat config/bikewatch_reporting_vars.yml)"
python3 -m http.server 8080 --bind 127.0.0.1 --directory dbt_bikewatch/target
```

Open http://127.0.0.1:8080 and stop the server with Ctrl+C.

## 6. What the code creates

| Relation | Grain and purpose |
|---|---|
| `staging.int_bw_tracking_periods` | One explicitly configured tracking interval |
| `staging.int_bw_expected_buckets` | One station × completed scheduled bucket |
| `staging.int_bw_observation_enriched` | Every retained observation by cutoff, with historical metadata and eligibility |
| `analytics.fct_station_observation` | One structurally valid observation per station × bucket |
| `staging.int_bw_reporting_samples` | Received keys plus expected keys, including entirely missing observations |
| `analytics.mart_station_current` | Latest trusted observation per observed or actively scheduled station |
| `analytics.mart_station_hourly` | One station × UTC hour with received or due expected samples |
| `analytics.mart_station_daily` | One station × New York reporting date with received or due expected samples |

All are initially rebuilt tables. At 20 stations and a 15-minute cadence, an ordinary
day adds at most 1,920 observation keys. Measure build duration before adding
incremental complexity. Unique indexes enforce key grains on the new tables.

### Historical metadata

The lateral join selects at most one valid metadata record: the newest one whose
`collected_at` is no later than the observation's `collected_at`. The metadata key,
receipt timestamp, and run ID remain attached for traceability. Today's capacity
is never substituted into old observations. Daily metadata collection only tells
us the latest known state; it does not establish the physical change time.

An observation without applicable metadata remains eligible for bike/dock counts.
Its name, coordinates, and capacity stay NULL, and the missing metadata is counted.
Capacity-based ratios are deliberately not part of these initial marts. The code
does not assume bikes plus docks must equal capacity.

### Cleaning and eligibility

The enriched table preserves all retained observations by cutoff. The fact retains
structurally valid rows, including stale, unknown-time, and closed observations.
Raw/staging data is never deleted by this add-on.

`availability_quality_reason` records the first matching exclusion in this order:
invalid structure, timestamp anomaly, unknown station time, stale station report,
stale feed, otherwise eligible. Original staging flags remain available too.

Availability eligibility requires valid structure and acceptable station/feed time
at collection. The station-age limit inherits `station_stale_after_seconds` (default
2,700 seconds). The feed-age limit is `feed_stale_after_seconds` (default 2,700).
The staging models retain the existing 300-second clock tolerance. These are project
thresholds, not upstream guarantees. Closure does not imply bad data.

Bike mean/min/max and empty fractions use **installed, rental-open, availability-eligible**
samples. Dock measures use **installed, return-open, availability-eligible** samples.
Closed samples remain visible in operating-state counts but leave the relevant service
denominator. Empty and full fractions describe sampled observations, not exact durations.

### Current data and freshness

The current mart includes observed station IDs and currently active scheduled station
IDs, including those with no observations. Historical observed stations remain present;
this initial model is not an active-station directory.

It chooses the newest availability-eligible observation. `latest_received_bucket`,
`latest_quality_reason`, and `using_older_trusted_observation` reveal newer unusable
observations. When no trusted row exists, availability stays NULL.

The selected observation can be closed or old. This table is a build-time snapshot.
At query time compare `freshness_valid_until` against `current_timestamp`, as well
as checking the operating flags. For example, in the Neon SQL editor:

```sql
SELECT
    station_id,
    bikes_available,
    docks_available,
    is_renting,
    is_returning,
    collected_at,
    using_older_trusted_observation,
    has_trusted_observation
        AND current_timestamp <= freshness_valid_until AS is_fresh_now
FROM analytics.mart_station_current
ORDER BY station_id;
```

Without a trusted observation, `has_trusted_observation` is false and `is_fresh_now`
is false. Re-evaluating freshness at read time avoids freezing a “fresh” flag after
the collector or dbt stops. The dashboard must still refresh/cache data appropriately.

### Coverage and aggregation

Expected samples are generated only for explicit tracking intervals and **completed**
15-minute windows by the build cutoff. An open current bucket is not overdue. A
received observation in that bucket contributes to observed metrics but not the
completed scheduled denominator yet.

`collection_coverage = scheduled_received_count / expected_observation_count`.
`usable_coverage = scheduled_usable_count / expected_observation_count`.

Scheduled received counts include structurally invalid retained observations; usable
counts require availability eligibility. Samples outside the declared schedule appear
in observed metrics but not scheduled coverage. Operational closures do not reduce
collection coverage, and they do not reduce availability-quality coverage if the
received data is otherwise valid. Rental/return eligibility has separate counts.

An entirely missing scheduled hour still appears, with four expected samples, zero
received, zero coverage, and NULL means. Without a configured schedule, empty hours
are not invented. No-data never becomes zero bikes or zero docks.

Daily metrics aggregate underlying samples directly, not averages of hourly averages.
For example, four samples averaging 3 bikes and one sample containing 20 bikes produce
a daily mean of `(4 × 3 + 20) / 5 = 6.4`, not 11.5. Counts and sums remain available
for inspection. A bike sample sum is not a trip count or a unique bike count.

Hours are keyed in UTC. Dates use `America/New_York` (configurable). Full local DST
days can have 92 or 100 quarter-hour buckets; ordinary full days have 96. Partial
days and tracking intervals use only elapsed, scheduled buckets.

## 7. Quality gates

The package adds 44 data tests: 8 singular SQL tests plus 36 configured standard
tests. Existing staging tests remain in place.

| Gate | What it checks |
|---|---|
| Tracking periods | Required IDs, aligned times, non-empty intervals, no overlap |
| Grain checks | Unique/non-null composite station-period keys and current station IDs |
| Fact integrity | Valid counts, conditional time order, source membership, fact inclusion set |
| Metadata links | Exact applicable snapshot and no newer valid snapshot overlooked |
| Relationships | Fact/current collector runs and attached metadata runs exist |
| Current integrity | Newest trusted row, valid fallback state, NULL measurements when unavailable |
| Sample reconciliation | Every received/expected key survives the reporting sample union |
| Mart ranges | Valid denominators, NULL when absent, bounded fractions, inclusion/exclusion equations |
| Mart reconciliation | Output period membership, counts, sums, and weighted means match samples |

`reporting_quality_warnings` warns for excluded observations, missing metadata,
incomplete scheduled hours, and unavailable/old current data. It intentionally covers
retained history, so historical issues can continue warning. These are not the same
as critical structural failures. No arbitrary live-coverage error threshold is imposed
while scheduling is not yet operational.

These tests are assertions, not row repair operations. Critical errors require
investigation; do not remove tests or delete records merely to make a build pass.

`dbt build` can skip dependent models when upstream error tests fail. It does not
make all models one atomic transaction and does not preserve a previous complete
reporting release after a failed multi-model build. Atomic publication and dashboard
permissions remain a later task. Do not treat a partly failed build as published.

## 8. Validation performed with this package

- `dbt parse` passed using dbt-core 1.10.15 and dbt-postgres 1.9.1 in an isolated
  project with staging model placeholders. No Neon connection was used.
- All eight rendered model queries and 44 test queries parsed as PostgreSQL SQL.
- All eight models executed on controlled fixtures using PGlite 0.5.8 / PostgreSQL 18.3.
- 43 error-level data checks passed. The warning query returned the deliberately
  introduced missing/stale fixture conditions.
- 20 behavioral/mutation checks passed, including historical capacity, all-missing
  hours, weighted daily means, invalid newest records, missing metadata, closures,
  unknown/stale/future timestamps, local midnight, both DST changes, partial buckets,
  disjoint tracking intervals, empty schedules, duplicate keys, orphan runs, and
  intentionally corrupted report outputs.

See `verification/validation-results.json` for the exact checks. This verifies model
SQL and intended behavior, not your live Neon grants, data, or a full adapter-driven
database build. The installation/build steps above complete that validation locally.

To reproduce the fixture validation separately from your application environment:

```bash
cd verification
python3 -m venv .verify-env
.verify-env/bin/python -m pip install -r requirements.txt
npm install
.verify-env/bin/python render_and_parse.py
node verify.mjs
```

The fixture runner creates an isolated in-memory PostgreSQL-compatible database;
it does not use `.env` or contact Neon. Generated SQL and the updated validation
report remain inside `verification`.

## References

- dbt schema naming: https://docs.getdbt.com/docs/build/custom-schemas
- dbt materializations: https://docs.getdbt.com/docs/build/materializations
- dbt data tests: https://docs.getdbt.com/docs/build/data-tests
- dbt build and dependency behavior: https://docs.getdbt.com/reference/commands/build
- PostgreSQL date/time functions: https://www.postgresql.org/docs/17/functions-datetime.html

The thresholds, denominators, eligibility rules, and coverage policy above are
BikeWatch project decisions. The references describe the underlying software features.
