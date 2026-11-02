# BikeWatch — safe 20-station replacement (November 2026)

## Why this change is needed

Station `2004153375425087884` was absent from both Citi Bike GBFS 1.1
`station_information` and `station_status` in the user's November 2 check.
This does **not** prove permanent retirement: source omissions can be temporary.
The old station's historical observations and tracking start stay unchanged.

## Changed files

- `src/bikewatch/ingestion/collect.py` — use the reporting period's active set before
  a future cutover; expose missing IDs in logs; allow missing-only partial
  collection to proceed to publication. Rejected data and failed collection still
  return a failing exit status.
- `.github/workflows/collect.yml` — preflight checks the effective 20 stations at
  the current time, including a not-yet-effective future swap.
- `dashboard/current_availability.py` — query the publication's active station
  mart rather than filter on the future JSON population; prevents pre-cutover gaps.
- `scripts/replace_station.py` — independently validate live GBFS 1.1 feeds,
  select an active and fresh candidate in the original Midtown study area,
  and edit both configuration files with one historical UTC cutover.
- New regression tests cover candidate eligibility, history and cutover, and
  missing-only versus rejected-record exit handling.

## Required operator steps — after applying the patch to your existing repository

From the repository root, activate your existing Python environment.

```bash
# 1. READ-ONLY: fetch current feeds and print five ranked candidates.
PYTHONPATH=src:. python -m scripts.replace_station
```

The tool uses the source's current `station_information` AND `station_status`,
checks feed timestamps and operating status, refuses another missing tracked
station, excludes the existing 20 IDs, and ranks remaining operational stations
by distance to BikeWatch's original Times Square sampling centroid
(40.7580, -73.9855), in the study rectangle 40.750–40.770 latitude and
-73.995 to -73.975 longitude. It does **not** assert it found the nearest
replacement to the missing station's historical coordinate (which is not in
this uploaded repository). Its output includes the exact ID, name, coordinates,
capacity, distance and source-update timestamps.

If the preview identifies a suitable station, apply it. By default, its UTC
cutover is at the start of the third UTC hour after the current hour, giving
some time to commit/deploy the code. If you need more deployment time, select
an explicit **future** 15-minute-aligned cutover; apply must be completed at
least 30 minutes beforehand. For example (replace with a future UTC time):

```bash
# 2. APPLY: use a future cutover timestamp appropriate to deployment time.
PYTHONPATH=src:. python -m scripts.replace_station --apply \
  --effective-bucket '2026-11-03T12:00:00Z'

# 3. Check the changes; do not hand-edit old observations or database timestamps.
python scripts/reporting_config.py
git diff -- config/tracked_stations.json config/bikewatch_reporting_vars.yml

# 4. Format + run CI tests with the existing project dependencies/PostgreSQL.
ruff format --config ruff-ci.toml src scripts tests dbt_bikewatch/tests
ruff check --config ruff-ci.toml src scripts tests dbt_bikewatch/tests
python -m pytest tests dbt_bikewatch/tests -o addopts=''

# 5. Commit together (with the replacement patch and all changed files).
git add src/bikewatch/ingestion/collect.py .github/workflows/collect.yml \
  dashboard/current_availability.py scripts/replace_station.py \
  config/tracked_stations.json config/bikewatch_reporting_vars.yml \
  tests docs/station_replacement_2026-11.md
git commit -m 'Handle Citi Bike station retirement and timed replacement'
git push
```

The example cutover in this document is illustrative, not a verified future
cutover at the time you run it. Do not use an already elapsed timestamp. If you
want to choose one of the eligible top-five candidates manually, use
`--station-id STATION_ID` with both the preview and apply commands.

**Deployment prerequisite:** Push/merge the new code and both changed configs
*before* the specified cutover. Existing scheduled runs continue to use the
old station until the UTC cutover, returning `partial` with a GitHub warning
when it is missing; dbt publication still runs and preserves quality gates.
Afterward the collector tracks the replacement, and dbt begins expected
intervals for the new station. Historical expectations for the old station
end at exactly that boundary. Scheduled reporting never silently backfills
missing old-station observations.

The Streamlit dashboard must deploy the updated `current_availability.py` code
and read the freshly published marts. The Reliability and Pipeline Health
views continue to include historical shortfalls and partial outcomes.

## Post-cutover acceptance checks

1. GitHub `BikeWatch collection`: configuration preflight succeeds, status
   batch reports 20 valid tracked stations and zero missing; run status is
   `succeeded`, unless there is a new source problem.
2. `Validate and publish reporting` and maintenance run. A partial run may
   still publish if dbt quality checks pass; rejected data still blocks.
3. The most recent reporting mart shows 20 active station rows; both hourly
   and daily historical models retain data for retired station and new ID.
4. `config/tracked_stations.json` contains 20 unique strings, while the
   reporting YAML has 21 station periods (20 original, 1 new); the retired
   station has a finite end, and the new one starts at the same UTC instant.
5. Database remains untouched by the migration script. Use the following
   read-only SQL after the first replacement collection:

```sql
SELECT station_id, collection_bucket, count(*) AS rows
FROM raw.station_observation
WHERE station_id IN ('2004153375425087884', '<NEW_STATION_ID>')
GROUP BY station_id, collection_bucket
ORDER BY collection_bucket DESC
LIMIT 20;

SELECT run_id, started_at, status,
       metrics->'station_status'->>'missing' AS missing_count,
       metrics->'station_status'->>'rejected' AS rejected_count
FROM ops.pipeline_run
ORDER BY started_at DESC
LIMIT 10;

SELECT count(*) AS active_stations
FROM analytics.mart_station_current;
```

`raw.station_observation` is append-only/idempotent at station × bucket. Old
observations remain until the project's existing retention policy applies.

## What remains unverified in this prepared patch

The current live feed cannot be fetched by the code-editing runtime. Therefore
**no specific new station ID has been asserted as live or written into the
configuration in this package**. The operator must run the preview/apply
command against Citi Bike's actual live feed and commit the resulting two files.
Offline regression tests alone cannot prove the live Neon/dbt publication or
GitHub scheduled-run gate; check those in the production environment.
