{{ config(materialized='table', tags=['reporting'],
          indexes=[{'columns': ['station_id', 'collection_bucket'], 'unique': true}]) }}
with observations as (
    select
        c.station_id, c.collection_bucket, c.collected_at,
        c.is_structurally_valid, c.has_metadata,
        f.bikes_available, f.docks_available,
        f.is_installed, f.is_renting, f.is_returning,
        coalesce(f.is_availability_eligible, false) as is_availability_eligible,
        coalesce(f.is_rental_eligible, false) as is_rental_eligible,
        coalesce(f.is_return_eligible, false) as is_return_eligible
    from {{ ref('int_bw_observation_enriched') }} c
    left join {{ ref('fct_station_observation') }} f
      on c.station_id = f.station_id and c.collection_bucket = f.collection_bucket
)
select
    coalesce(o.station_id, e.station_id) as station_id,
    coalesce(o.collection_bucket, e.collection_bucket) as collection_bucket,
    e.station_id is not null as is_expected,
    o.station_id is not null as was_received,
    o.collected_at,
    coalesce(o.is_structurally_valid, false) as is_structurally_valid,
    coalesce(o.has_metadata, false) as has_metadata,
    o.bikes_available, o.docks_available,
    o.is_installed, o.is_renting, o.is_returning,
    coalesce(o.is_availability_eligible, false) as is_availability_eligible,
    coalesce(o.is_rental_eligible, false) as is_rental_eligible,
    coalesce(o.is_return_eligible, false) as is_return_eligible
from observations o
full outer join {{ ref('int_bw_expected_buckets') }} e
  on o.station_id = e.station_id and o.collection_bucket = e.collection_bucket
