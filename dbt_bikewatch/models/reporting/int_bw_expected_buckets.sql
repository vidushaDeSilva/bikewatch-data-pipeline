{{ config(materialized='table', tags=['reporting'],
          indexes=[{'columns': ['station_id', 'collection_bucket'], 'unique': true}]) }}
-- Count completed 15-minute windows only. No inferred tracking start date.
select p.station_id, b.collection_bucket
from {{ ref('int_bw_tracking_periods') }} p
cross join lateral generate_series(
    p.start_bucket,
    least(coalesce(p.end_bucket, {{ bw_reporting_as_of() }}),
          {{ bw_reporting_as_of() }}) - interval '15 minutes',
    interval '15 minutes'
) as b(collection_bucket)
