{{ config(materialized='table', tags=['reporting'],
          indexes=[{'columns': ['station_id', 'collection_bucket'], 'unique': true}]) }}
with joined as (
    select
        o.*,
        m.station_name, m.latitude, m.longitude, m.capacity,
        m.collection_bucket as metadata_collection_bucket,
        m.collected_at as metadata_collected_at,
        m.run_id as metadata_run_id,
        m.station_id is not null as has_metadata,
        (
            not coalesce(o.has_invalid_structure, true)
            and o.station_id is not null and btrim(o.station_id) <> ''
            and o.collection_bucket is not null and o.collected_at is not null
            and o.feed_last_updated is not null and o.run_id is not null
            and o.source_version is not null
        ) as is_structurally_valid
    from {{ ref('stg_station_observation') }} o
    left join lateral (
        select m.*
        from {{ ref('stg_station_metadata') }} m
        where m.station_id = o.station_id
          and m.collected_at <= o.collected_at
          and not coalesce(m.has_invalid_structure, true)
          and not coalesce(m.has_time_anomaly, true)
        order by m.collected_at desc, m.collection_bucket desc, m.run_id desc
        limit 1
    ) m on true
    where o.collected_at <= {{ bw_reporting_as_of() }}
), classified as (
    select *,
        case
            when not is_structurally_valid then 'invalid_structure'
            when coalesce(has_time_anomaly, true) then 'time_anomaly'
            when station_reported_at is null then 'unknown_station_time'
            when station_age_seconds > {{ var('station_stale_after_seconds', 2700) }}
                then 'stale_station_report'
            when feed_age_seconds > {{ var('feed_stale_after_seconds', 2700) }}
                then 'stale_feed'
            else 'eligible'
        end as availability_quality_reason
    from joined
)
select *,
    availability_quality_reason = 'eligible' as is_availability_eligible,
    coalesce(availability_quality_reason = 'eligible' and is_installed and is_renting, false)
        as is_rental_eligible,
    coalesce(availability_quality_reason = 'eligible' and is_installed and is_returning, false)
        as is_return_eligible,
    {{ bw_reporting_as_of() }} as reporting_as_of
from classified
