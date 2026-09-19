{{ config(severity='warn', tags=['reporting']) }}

-- Warn on observations excluded from availability analysis or missing metadata
select 'excluded_observation_or_missing_metadata' as check_name, station_id,
       collection_bucket::text as period_key
from {{ ref('int_bw_observation_enriched') }}
where not is_availability_eligible or not has_metadata
union all

-- Warn when an expected hourly collection period has less than full usable coverage
select 'incomplete_scheduled_hour', station_id, hour_start_utc::text
from {{ ref('mart_station_hourly') }}
where expected_observation_count > 0 and usable_coverage < 1
union all

-- Warn when the current station snapshot is unavailable, stale, or using an older trusted observation.
select 'current_unavailable_or_stale', station_id, collection_bucket::text
from {{ ref('mart_station_current') }}
where not has_trusted_observation or freshness_valid_until < reporting_as_of
   or using_older_trusted_observation
