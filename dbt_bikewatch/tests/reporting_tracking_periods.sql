{{ config(tags=['reporting']) }}
with periods as (
    select *, row_number() over () as row_number
    from {{ ref('int_bw_tracking_periods') }}
)
select 'invalid_period' as check_name, station_id
from periods
where station_id is null or btrim(station_id) = '' or start_bucket is null
   or end_bucket <= start_bucket
   or mod(extract(epoch from start_bucket), 900) <> 0
   or mod(extract(epoch from end_bucket), 900) <> 0
union all
select 'overlapping_period', a.station_id
from periods a join periods b
  on a.station_id = b.station_id and a.row_number < b.row_number
 and a.start_bucket < coalesce(b.end_bucket, 'infinity'::timestamptz)
 and b.start_bucket < coalesce(a.end_bucket, 'infinity'::timestamptz)
