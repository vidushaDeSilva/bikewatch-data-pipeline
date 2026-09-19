{{ config(materialized='table', schema='analytics', tags=['reporting'],
          indexes=[{'columns': ['station_id'], 'unique': true}]) }}
with stations as (
    select station_id from {{ ref('int_bw_observation_enriched') }}
    union
    select station_id from {{ ref('int_bw_tracking_periods') }}
    where start_bucket <= {{ bw_reporting_as_of() }}
      and (end_bucket is null or end_bucket > {{ bw_reporting_as_of() }})
)
select
    s.station_id,
    t.station_name, t.latitude, t.longitude, t.capacity,
    t.collection_bucket, t.collected_at, t.station_reported_at, t.feed_last_updated,
    t.run_id, t.metadata_collection_bucket, t.metadata_collected_at, t.metadata_run_id,
    coalesce(t.has_metadata, false) as has_metadata,
    t.bikes_available, t.docks_available,
    t.is_installed, t.is_renting, t.is_returning,
    t.station_id is not null as has_trusted_observation,
    latest.collection_bucket as latest_received_bucket,
    latest.collected_at as latest_received_at,
    latest.availability_quality_reason as latest_quality_reason,
    coalesce(latest.collection_bucket > t.collection_bucket, false) as using_older_trusted_observation,
    case when t.station_id is not null then least(
        t.collected_at + {{ var('station_stale_after_seconds', 2700) }} * interval '1 second',
        t.station_reported_at + {{ var('station_stale_after_seconds', 2700) }} * interval '1 second',
        t.feed_last_updated + {{ var('feed_stale_after_seconds', 2700) }} * interval '1 second'
    ) end as freshness_valid_until,
    {{ bw_reporting_as_of() }} as reporting_as_of
from stations s
left join lateral (
    select * from {{ ref('fct_station_observation') }} f
    where f.station_id = s.station_id and f.is_availability_eligible
    order by f.collection_bucket desc, f.collected_at desc, f.run_id desc
    limit 1
) t on true
left join lateral (
    select * from {{ ref('int_bw_observation_enriched') }} c
    where c.station_id = s.station_id
    order by c.collection_bucket desc, c.collected_at desc, c.run_id desc
    limit 1
) latest on true


