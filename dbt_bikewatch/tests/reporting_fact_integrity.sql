{{ config(tags=['reporting']) }}

-- Validate fact-row structure, values, timestamps, and eligibility logic
select 'invalid_fact' as check_name, f.station_id, f.collection_bucket

from {{ ref('fct_station_observation') }} f

where not f.is_structurally_valid

   or f.bikes_available < 0 or f.docks_available < 0

   or f.bikes_available is null or f.docks_available is null

   or f.is_installed is null or f.is_renting is null or f.is_returning is null

   or f.collected_at > f.reporting_as_of

   or mod(extract(epoch from f.collection_bucket), 900) <> 0

   or (f.is_availability_eligible and (

       f.station_reported_at is null

       or f.collected_at < f.collection_bucket

       or f.station_age_seconds not between -{{ var('allowed_clock_skew_seconds', 300) }}

                                          and {{ var('station_stale_after_seconds', 2700) }}

       or f.feed_age_seconds not between -{{ var('allowed_clock_skew_seconds', 300) }}

                                       and {{ var('feed_stale_after_seconds', 2700) }}

   ))

   or (f.is_rental_eligible and not (f.is_availability_eligible and f.is_installed and f.is_renting))

   or (f.is_return_eligible and not (f.is_availability_eligible and f.is_installed and f.is_returning))

union all

-- Ensure every fact row has a matching source observation
select 'missing_source_observation', f.station_id, f.collection_bucket

from {{ ref('fct_station_observation') }} f

where not exists (

    select 1 from {{ ref('stg_station_observation') }} s

    where s.station_id = f.station_id and s.collection_bucket = f.collection_bucket

      and s.run_id = f.run_id

)

union all

-- Verify linked metadata exists, is valid, and was available by observation time
select 'invalid_metadata_link', f.station_id, f.collection_bucket

from {{ ref('fct_station_observation') }} f

where f.has_metadata and (

    f.metadata_collected_at > f.collected_at

    or not exists (

        select 1 from {{ ref('stg_station_metadata') }} m

        where m.station_id = f.station_id

          and m.collection_bucket = f.metadata_collection_bucket

          and m.collected_at = f.metadata_collected_at

          and m.run_id = f.metadata_run_id

          and m.capacity is not distinct from f.capacity

          and not coalesce(m.has_invalid_structure, true)

          and not coalesce(m.has_time_anomaly, true)

    )

)

union all

-- Ensure the fact uses the latest valid metadata available at observation time
select 'newer_applicable_metadata_exists', f.station_id, f.collection_bucket

from {{ ref('fct_station_observation') }} f

where exists (

    select 1 from {{ ref('stg_station_metadata') }} m

    where m.station_id = f.station_id and m.collected_at <= f.collected_at

      and not coalesce(m.has_invalid_structure, true)

      and not coalesce(m.has_time_anomaly, true)

      and (not f.has_metadata or m.collected_at > f.metadata_collected_at)

)

union all

-- Check that the fact table contains exactly the expected structurally valid observations
select 'fact_set_difference', station_id, collection_bucket

from (

    (select station_id, collection_bucket from {{ ref('int_bw_observation_enriched') }}

     where is_structurally_valid

     except select station_id, collection_bucket from {{ ref('fct_station_observation') }})

    union all

    (select station_id, collection_bucket from {{ ref('fct_station_observation') }}

     except select station_id, collection_bucket from {{ ref('int_bw_observation_enriched') }}

     where is_structurally_valid)

) d




-- fct_station_observation
--         │
--         ├── validate fact values and eligibility logic
--         ├── verify source observation exists
--         ├── verify metadata link is correct
--         ├── verify latest applicable metadata was used
--         └── compare fact row set with upstream valid observations
--                 ↓
--           UNION ALL failures
--                 ↓
--         expected result: 0 rows