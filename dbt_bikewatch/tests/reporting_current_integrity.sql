{{ config(tags=['reporting']) }}

-- Validate fields and source linkage when a trusted observation is present
select c.station_id
from {{ ref('mart_station_current') }} c
where (c.has_trusted_observation and (
    c.collection_bucket is null or c.run_id is null or c.freshness_valid_until is null
    or c.collected_at > c.reporting_as_of
    or not exists (
        select 1 from {{ ref('fct_station_observation') }} f
        where f.station_id = c.station_id and f.collection_bucket = c.collection_bucket
          and f.run_id = c.run_id and f.is_availability_eligible
    )
))

-- Ensure stations without a trusted observation do not expose trusted status values
or (not c.has_trusted_observation and (
    c.bikes_available is not null or c.docks_available is not null
    or c.collection_bucket is not null or c.freshness_valid_until is not null
))

-- Ensure the current mart is using the latest available trusted observation
or exists (
    select 1 from {{ ref('fct_station_observation') }} f
    where f.station_id = c.station_id and f.is_availability_eligible
      and (not c.has_trusted_observation or f.collection_bucket > c.collection_bucket)
)

-- Verify the older-trusted-observation flag matches the latest received bucket comparison
or c.using_older_trusted_observation is distinct from
   coalesce(c.latest_received_bucket > c.collection_bucket, false)
