{% macro bw_reporting_literal(value) -%}
'{{ (value | string) | replace("'", "''") }}'
{%- endmacro %}

{% macro bw_reporting_as_of() -%}
{{ bw_reporting_literal(var('reporting_as_of', run_started_at.isoformat())) }}::timestamptz
{%- endmacro %}

{% macro bw_reporting_timezone() -%}
{{ bw_reporting_literal(var('reporting_timezone', 'America/New_York')) }}
{%- endmacro %}

{% macro bw_reporting_aggregate(period) -%}
{% if period == 'hour' %}
    {% set period_expression = "date_trunc('hour', collection_bucket, 'UTC')" %}
    {% set period_column = 'hour_start_utc' %}
{% else %}
    {% set period_expression = '(collection_bucket at time zone ' ~ bw_reporting_timezone() ~ ')::date' %}
    {% set period_column = 'reporting_date' %}
{% endif %}
with grouped as (
    select
        station_id,
        {{ period_expression }} as {{ period_column }},
        count(*) filter (where is_expected) as expected_observation_count,
        count(*) filter (where was_received) as observation_count,
        count(*) filter (where is_expected and was_received) as scheduled_received_count,
        count(*) filter (where is_expected and is_availability_eligible) as scheduled_usable_count,
        count(*) filter (where was_received and not is_structurally_valid) as invalid_structure_count,
        count(*) filter (where is_structurally_valid) as fact_observation_count,
        count(*) filter (where is_structurally_valid and not is_availability_eligible) as quality_excluded_count,
        count(*) filter (where is_availability_eligible) as availability_sample_count,
        count(*) filter (where is_rental_eligible) as rental_sample_count,
        count(*) filter (where is_return_eligible) as return_sample_count,
        count(*) filter (where is_availability_eligible and not is_installed) as not_installed_count,
        count(*) filter (where is_availability_eligible and not is_renting) as rentals_closed_count,
        count(*) filter (where is_availability_eligible and not is_returning) as returns_closed_count,
        count(*) filter (where is_rental_eligible and bikes_available = 0) as empty_sample_count,
        count(*) filter (where is_return_eligible and docks_available = 0) as full_sample_count,
        count(*) filter (where was_received and not has_metadata) as missing_metadata_count,
        sum(bikes_available::numeric) filter (where is_rental_eligible) as bikes_sample_sum,
        avg(bikes_available::numeric) filter (where is_rental_eligible) as avg_bikes_available,
        min(bikes_available) filter (where is_rental_eligible) as min_bikes_available,
        max(bikes_available) filter (where is_rental_eligible) as max_bikes_available,
        sum(docks_available::numeric) filter (where is_return_eligible) as docks_sample_sum,
        avg(docks_available::numeric) filter (where is_return_eligible) as avg_docks_available,
        min(docks_available) filter (where is_return_eligible) as min_docks_available,
        max(docks_available) filter (where is_return_eligible) as max_docks_available,
        min(collected_at) filter (where was_received) as first_collected_at,
        max(collected_at) filter (where was_received) as last_collected_at
    from {{ ref('int_bw_reporting_samples') }}
    group by station_id, {{ period_expression }}
)
select
    *,
    expected_observation_count - scheduled_received_count as missing_observation_count,
    scheduled_received_count::numeric / nullif(expected_observation_count, 0) as collection_coverage,
    scheduled_usable_count::numeric / nullif(expected_observation_count, 0) as usable_coverage,
    empty_sample_count::numeric / nullif(rental_sample_count, 0) as empty_sample_fraction,
    full_sample_count::numeric / nullif(return_sample_count, 0) as full_sample_fraction,
    {{ bw_reporting_as_of() }} as reporting_as_of,
    {{ bw_reporting_timezone() }} as reporting_timezone
from grouped
{%- endmacro %}
