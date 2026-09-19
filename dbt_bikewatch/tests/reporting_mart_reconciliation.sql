{{ config(tags=['reporting']) }}
{% for model, expression, key in [
    ('mart_station_hourly', "date_trunc('hour', collection_bucket, 'UTC')", 'hour_start_utc'),
    ('mart_station_daily', '(collection_bucket at time zone ' ~ bw_reporting_timezone() ~ ')::date', 'reporting_date')
] %}
select '{{ model }}' as model_name, coalesce(m.station_id, x.station_id) as station_id
from {{ ref(model) }} m
full join (
    select station_id, {{ expression }} as period_key,
        count(*) filter (where was_received) as received,
        count(*) filter (where is_expected) as expected,
        count(*) filter (where is_rental_eligible) as rental_samples,
        count(*) filter (where is_return_eligible) as return_samples,
        sum(bikes_available::numeric) filter (where is_rental_eligible) as bikes_sum,
        sum(docks_available::numeric) filter (where is_return_eligible) as docks_sum
    from {{ ref('int_bw_reporting_samples') }}
    group by station_id, {{ expression }}
) x on m.station_id = x.station_id and m.{{ key }} = x.period_key
where m.station_id is null or x.station_id is null
   or m.observation_count is distinct from x.received
   or m.expected_observation_count is distinct from x.expected
   or m.bikes_sample_sum is distinct from x.bikes_sum
   or m.docks_sample_sum is distinct from x.docks_sum
   or m.rental_sample_count is distinct from x.rental_samples
   or m.return_sample_count is distinct from x.return_samples
   or abs(m.avg_bikes_available - x.bikes_sum / nullif(x.rental_samples, 0)) > 0.000000001
   or abs(m.avg_docks_available - x.docks_sum / nullif(x.return_samples, 0)) > 0.000000001
{% if not loop.last %}union all{% endif %}
{% endfor %}
