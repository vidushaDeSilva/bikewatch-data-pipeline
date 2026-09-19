{{ config(tags=['reporting']) }}
{% set models = [
    ('int_bw_observation_enriched', 'collection_bucket'),
    ('fct_station_observation', 'collection_bucket'),
    ('int_bw_expected_buckets', 'collection_bucket'),
    ('int_bw_reporting_samples', 'collection_bucket'),
    ('mart_station_hourly', 'hour_start_utc'),
    ('mart_station_daily', 'reporting_date')
] %}
{% for model, key in models %}
select '{{ model }}' as model_name, station_id, {{ key }}::text as period_key
from {{ ref(model) }}
group by station_id, {{ key }}
having count(*) <> 1 or station_id is null or btrim(station_id) = '' or {{ key }} is null
union all
{% endfor %}
select 'mart_station_current', station_id, null::text
from {{ ref('mart_station_current') }}
group by station_id
having count(*) <> 1 or station_id is null or btrim(station_id) = ''
