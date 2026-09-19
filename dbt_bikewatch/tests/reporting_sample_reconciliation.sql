{{ config(tags=['reporting']) }}
with differences as (
    (select station_id, collection_bucket from {{ ref('int_bw_observation_enriched') }}
     except select station_id, collection_bucket from {{ ref('int_bw_reporting_samples') }} where was_received)
    union all
    (select station_id, collection_bucket from {{ ref('int_bw_reporting_samples') }} where was_received
     except select station_id, collection_bucket from {{ ref('int_bw_observation_enriched') }})
    union all
    (select station_id, collection_bucket from {{ ref('int_bw_expected_buckets') }}
     except select station_id, collection_bucket from {{ ref('int_bw_reporting_samples') }} where is_expected)
    union all
    (select station_id, collection_bucket from {{ ref('int_bw_reporting_samples') }} where is_expected
     except select station_id, collection_bucket from {{ ref('int_bw_expected_buckets') }})
)
select * from differences
union all
select station_id, collection_bucket from {{ ref('int_bw_expected_buckets') }}
where collection_bucket + interval '15 minutes' > {{ bw_reporting_as_of() }}
