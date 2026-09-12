{{ config(severity='warn') }}

select
    'metadata_time' as check_name,
    station_id as record_id
from {{ ref('stg_station_metadata') }}
where has_time_anomaly

union all

select 'observation_time_or_age', station_id
from {{ ref('stg_station_observation') }}
where has_time_anomaly
   or is_station_time_unknown
   or was_station_stale_at_collection

union all

select 'run_gap_failure_or_time', run_id::text
from {{ ref('stg_pipeline_run') }}
where has_source_gaps
   or is_failed_run
   or has_time_anomaly