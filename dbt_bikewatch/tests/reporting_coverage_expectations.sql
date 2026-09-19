{{ config(tags=['reporting']) }}

-- Calculate how many 15-minute buckets should exist for each station
-- based on its tracking periods up to the reporting cutoff.
with wanted as (
    select
        station_id,
        sum(
            greatest(
                0,
                floor(
                    extract(
                        epoch from (
                            least(
                                coalesce(
                                    end_bucket,
                                    {{ bw_reporting_as_of() }}
                                ),
                                {{ bw_reporting_as_of() }}
                            ) - start_bucket
                        )
                    ) / 900
                )
            )
        )::bigint as expected_count

    from {{ ref('int_bw_tracking_periods') }}
    group by station_id
),

-- Count how many expected 15-minute buckets were actually generated.
actual as (
    select
        station_id,
        count(*) as actual_count

    from {{ ref('int_bw_expected_buckets') }}
    group by station_id
)

-- Report stations where generated bucket count differs from expected count.
select
    'expected_count' as check_name,
    coalesce(w.station_id, a.station_id) as station_id

from wanted w
full join actual a using (station_id)
where
    coalesce(w.expected_count, 0) <> coalesce(a.actual_count, 0)

union all

-- Report generated buckets that do not belong to a valid tracking interval.
select
    'bucket_outside_tracking_interval',
    e.station_id

from {{ ref('int_bw_expected_buckets') }} e

where not exists (
    select 1

    from {{ ref('int_bw_tracking_periods') }} p

    where p.station_id = e.station_id

      and e.collection_bucket >= p.start_bucket

      and (
          p.end_bucket is null
          or e.collection_bucket < p.end_bucket
      )

      and e.collection_bucket + interval '15 minutes'
          <= {{ bw_reporting_as_of() }}

      and mod(
          extract(
              epoch from e.collection_bucket - p.start_bucket
          ),
          900
      ) = 0
)

