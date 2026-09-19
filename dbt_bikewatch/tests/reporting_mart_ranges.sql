{{ config(tags=['reporting']) }}
{% for model, key in [('mart_station_hourly', 'hour_start_utc'), ('mart_station_daily', 'reporting_date')] %}
select '{{ model }}' as model_name, station_id, {{ key }}::text as period_key
from {{ ref(model) }}
where observation_count <> invalid_structure_count + fact_observation_count
   or fact_observation_count <> quality_excluded_count + availability_sample_count
   or expected_observation_count <> scheduled_received_count + missing_observation_count
   or scheduled_usable_count > scheduled_received_count
   or scheduled_received_count > observation_count
   or rental_sample_count > availability_sample_count
   or return_sample_count > availability_sample_count
   or empty_sample_count > rental_sample_count or full_sample_count > return_sample_count
   {% for name in ['expected_observation_count','observation_count','invalid_structure_count',
                   'fact_observation_count','quality_excluded_count','availability_sample_count',
                   'scheduled_received_count','scheduled_usable_count','missing_observation_count',
                   'rental_sample_count','return_sample_count','empty_sample_count','full_sample_count'] %}
   or {{ name }} is null or {{ name }} < 0
   {% endfor %}
   {% for name in ['collection_coverage','usable_coverage','empty_sample_fraction','full_sample_fraction'] %}
   or {{ name }} not between 0 and 1
   {% endfor %}
   or collection_coverage is distinct from scheduled_received_count::numeric / nullif(expected_observation_count, 0)
   or usable_coverage is distinct from scheduled_usable_count::numeric / nullif(expected_observation_count, 0)
   or empty_sample_fraction is distinct from empty_sample_count::numeric / nullif(rental_sample_count, 0)
   or full_sample_fraction is distinct from full_sample_count::numeric / nullif(return_sample_count, 0)
   or (rental_sample_count = 0 and avg_bikes_available is not null)
   or (return_sample_count = 0 and avg_docks_available is not null)
   or (rental_sample_count > 0 and (avg_bikes_available is null
       or avg_bikes_available < min_bikes_available or avg_bikes_available > max_bikes_available))
   or (return_sample_count > 0 and (avg_docks_available is null
       or avg_docks_available < min_docks_available or avg_docks_available > max_docks_available))
{% if not loop.last %}union all{% endif %}
{% endfor %}
